"""Iterative LLM runner.

Drives one stage's tool-using LLM conversation to completion. Each iteration:

  1. POST the conversation to the model.
  2. Extract any JSON-embedded tool requests from the response text.
  3. If there are tool requests → execute them all concurrently
     (`asyncio.gather`) and append each result as a `[TOOL_RESULT]` user
     message, then loop.
  4. Otherwise → ask the stage's extractor to find its JSON shape. If found
     and valid, return it. If not, append the stage's fallback guidance and
     loop.

Two safety hatches:
  - At `SOFT_REMINDER_ITERATION` (or 80% of `max_iterations` if smaller), inject
    a soft reminder so the model knows to wrap up.
  - On the final iteration, inject a critical-final-iteration message that
    forbids more tool calls.

Tool execution runs through a `ToolExecutor` callable so this module does not
depend on `hindsight.llm.tools` directly — `pipeline_code` etc. can supply
either the new `ToolRegistry` or a shim that wraps the legacy `Tools` class.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, FrozenSet, Optional, Protocol

from hindsight.core.constants import MAX_TOOL_ITERATIONS, SOFT_REMINDER_ITERATION
from hindsight.utils.log_util import get_logger

from .client import AsyncLLMClient, LLMResponse
from .conversation import ConversationState
from .errors import LLMError
from .logger import ConversationLogger
from .stages import StageSpec
from .tool_protocol import ToolCall, extract_tool_requests

logger = get_logger(__name__)


class ToolExecutor(Protocol):
    """Anything that can run a `ToolCall` by name.

    Concrete implementations live in `hindsight.llm.tools.registry.ToolRegistry`;
    pipelines may also pass a thin adapter wrapping the legacy `Tools` instance.
    """

    async def execute(self, call: ToolCall, *, allowed: FrozenSet[str]) -> str:  # pragma: no cover - Protocol
        ...


TokenUsageCallback = Callable[[LLMResponse, int], None]


@dataclass
class IterationOutcome:
    """Result of one full stage run."""

    text: Optional[str]            # extracted JSON string on success, or last assistant text
    iterations: int
    input_tokens: int
    output_tokens: int
    error: Optional[str] = None


class IterativeRunner:
    """Async tool-using LLM loop with stage-specific JSON shape enforcement."""

    def __init__(
        self,
        client: AsyncLLMClient,
        *,
        conversation_logger: Optional[ConversationLogger] = None,
    ):
        self._client = client
        self._logger = conversation_logger

    async def run(
        self,
        stage: StageSpec,
        *,
        user_prompt: str,
        tools: Optional[ToolExecutor] = None,
        context_info: str = "",
        token_callback: Optional[TokenUsageCallback] = None,
        max_iterations: Optional[int] = None,
    ) -> IterationOutcome:
        """Drive `stage` to completion and return the validated JSON string.

        Returns `IterationOutcome.text == None` only on hard API failure;
        on max-iterations-exhausted the last assistant text is returned so the
        caller can decide whether to retry or surface a soft failure.
        """
        max_iters = max_iterations if max_iterations is not None else stage.max_iterations or MAX_TOOL_ITERATIONS
        soft_reminder_at = (
            SOFT_REMINDER_ITERATION
            if max_iters >= SOFT_REMINDER_ITERATION
            else int(max_iters * 0.8)
        )
        tools_enabled = tools is not None and bool(stage.supported_tools)

        state = ConversationState()
        state.set_system_prompt(stage.system_prompt)
        state.set_original_request(user_prompt)
        state.add_user(user_prompt)

        if self._logger is not None:
            self._logger.start_conversation(stage.name, context_info)

        total_input = 0
        total_output = 0
        iteration = 0
        last_text: Optional[str] = None

        try:
            while iteration < max_iters:
                iteration += 1
                logger.info(f"[{stage.name}] Iteration {iteration}/{max_iters}")

                self._inject_iteration_guidance(
                    state,
                    iteration=iteration,
                    max_iters=max_iters,
                    soft_reminder_at=soft_reminder_at,
                    tools_enabled=tools_enabled,
                    stage_name=stage.name,
                )

                messages = state.as_payload()
                try:
                    response = await self._client.send(
                        system_prompt=stage.system_prompt,
                        messages=messages,
                        enable_system_cache=True,
                        cache_ttl="1h",
                    )
                except LLMError as exc:
                    logger.error(f"[{stage.name}] API error in iteration {iteration}: {exc}")
                    if self._logger is not None:
                        self._logger.record_turn(messages, {"error": str(exc)})
                    return IterationOutcome(
                        text=None,
                        iterations=iteration,
                        input_tokens=total_input,
                        output_tokens=total_output,
                        error=str(exc),
                    )

                if self._logger is not None:
                    self._logger.record_turn(messages, response.raw)
                if token_callback is not None:
                    try:
                        token_callback(response, iteration)
                    except Exception as exc:
                        logger.debug(f"[{stage.name}] token_callback raised: {exc}")
                total_input += response.input_tokens
                total_output += response.output_tokens

                assistant_text = response.text
                last_text = assistant_text
                logger.info(f"[{stage.name}] Received response: {len(assistant_text)} chars")

                tool_calls = extract_tool_requests(assistant_text) if tools_enabled else []

                if tool_calls and tools_enabled:
                    state.add_assistant(assistant_text)
                    logger.info(
                        f"[{stage.name}] Iteration {iteration}: dispatching "
                        f"{len(tool_calls)} tool request(s) concurrently"
                    )
                    results = await _execute_tools_concurrently(
                        tool_calls, tools, allowed=stage.supported_tools  # type: ignore[arg-type]
                    )
                    for idx, (call, result) in enumerate(zip(tool_calls, results)):
                        tool_id = call.make_id(iteration, idx)
                        state.add_tool_result(tool_id, result)
                        logger.info(f"[{stage.name}] Tool result added for {call.name} (id: {tool_id})")
                    continue

                # No tool calls — try to extract the stage's expected JSON.
                state.add_assistant(assistant_text)
                extracted = stage.extract_json(assistant_text)
                validation_reason: Optional[str] = None
                has_valid_json = False

                if extracted and extracted.strip():
                    try:
                        parsed = json.loads(extracted)
                        # Don't accept stray tool-call JSON as the final answer.
                        if isinstance(parsed, dict) and "tool" in parsed:
                            validation_reason = (
                                "your response was a single tool-call JSON object instead of a final structured answer"
                            )
                        elif stage.validate_json(parsed):
                            has_valid_json = True
                        else:
                            validation_reason = _describe_validation_failure(parsed)
                            logger.info(f"[{stage.name}] JSON failed shape validator — {validation_reason}")
                    except json.JSONDecodeError as exc:
                        validation_reason = (
                            f"the JSON in your response could not be parsed "
                            f"(JSONDecodeError: {exc.msg} at line {exc.lineno} col {exc.colno})"
                        )
                else:
                    validation_reason = "no JSON object or array was found in your response"

                if has_valid_json:
                    logger.info(f"[{stage.name}] Analysis complete with valid JSON in iteration {iteration}")
                    return IterationOutcome(
                        text=extracted,
                        iterations=iteration,
                        input_tokens=total_input,
                        output_tokens=total_output,
                    )

                if iteration >= max_iters:
                    logger.info(f"[{stage.name}] Analysis complete (max iterations reached)")
                    return IterationOutcome(
                        text=assistant_text,
                        iterations=iteration,
                        input_tokens=total_input,
                        output_tokens=total_output,
                    )

                guidance = stage.fallback_guidance(validation_reason)
                state.add_user(guidance)
                logger.info(f"[{stage.name}] No structured output, continuing iteration {iteration + 1}")

            # Loop exit (defensive — covered by the iteration>=max_iters branch above).
            return IterationOutcome(
                text=last_text,
                iterations=iteration,
                input_tokens=total_input,
                output_tokens=total_output,
            )
        finally:
            if self._logger is not None:
                # Finalize the markdown transcript regardless of success path.
                self._logger.finalize(final_result=last_text)

    # ------------------------------------------------------------------
    # Iteration guidance — soft reminder + final-iteration forcing
    # ------------------------------------------------------------------

    @staticmethod
    def _inject_iteration_guidance(
        state: ConversationState,
        *,
        iteration: int,
        max_iters: int,
        soft_reminder_at: int,
        tools_enabled: bool,
        stage_name: str,
    ) -> None:
        if iteration == max_iters and tools_enabled:
            state.add_user(
                "CRITICAL: This is your FINAL iteration. You MUST produce your JSON verdict NOW "
                "based on what you have gathered so far. Do NOT request any more tools. "
                "Respond ONLY with your final JSON analysis result."
            )
            logger.info(f"[{stage_name}] Final iteration - injected forcing guidance")
        elif iteration == soft_reminder_at and tools_enabled:
            state.add_user(
                "REMINDER: You are approaching the iteration limit. You have a few more iterations remaining. "
                "Please start wrapping up your context collection and prepare to generate your final JSON output. "
                "If you have gathered sufficient context, you may produce your JSON result now. "
                "Otherwise, make only essential tool calls and then produce your final output."
            )
            logger.info(f"[{stage_name}] Soft reminder injected at iteration {iteration}")


async def _execute_tools_concurrently(
    calls: list[ToolCall],
    tools: ToolExecutor,
    *,
    allowed: FrozenSet[str],
) -> list[str]:
    """Run all tool calls from one iteration concurrently.

    A tool error becomes a string result rather than propagating — the LLM
    sees the error in its next turn and can react. This matches the legacy
    sync behavior where `Tools.execute_tool_use` always returned a string.
    """
    import asyncio

    async def _safe_execute(call: ToolCall) -> str:
        try:
            return await tools.execute(call, allowed=allowed)
        except Exception as exc:  # noqa: BLE001 - intentionally broad: feed back to LLM
            logger.error(f"Tool '{call.name}' raised: {exc}")
            return f"Error executing tool '{call.name}': {exc}"

    return await asyncio.gather(*[_safe_execute(c) for c in calls])


def _describe_validation_failure(parsed: Any) -> str:
    """Build a short human-readable description of a shape-validation failure.

    Used in the fallback guidance so the LLM is told exactly what was wrong.
    Mirrors `BaseIterativeAnalyzer._describe_validation_failure`.
    """
    if isinstance(parsed, dict):
        top_keys = list(parsed.keys())[:8]
        return f"got a JSON dict with top-level keys {top_keys} that did not match the required schema"
    if isinstance(parsed, list):
        first_item_type = type(parsed[0]).__name__ if parsed else "empty"
        return (
            f"got a JSON list with {len(parsed)} items (first item type: {first_item_type}) "
            "that did not match the required schema"
        )
    return f"got a JSON value of type {type(parsed).__name__} instead of the expected object/array shape"
