"""Sync→async bridge for legacy callsites.

The issue filters (`LLMBasedFilter`, `LLMResponseChallenger`, etc.) expose a
sync `filter_issues()` API but need to make per-issue LLM verdict calls. This
module gives them a small entry point that spins up an `AsyncLLMClient` +
event loop for the duration of one batch.

Use sparingly — the preferred shape is async all the way through. This
bridge exists so we can delete the legacy `hindsight.core.llm` stack without
rewriting every sync caller at the same time.

Safe to call from worker threads spawned by `asyncio.to_thread` (e.g., from
`CodePipeline._apply_issue_filter`): the thread has no current event loop,
so `asyncio.run` builds a fresh one without colliding with the main loop.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, List, Optional

from .bedrock import LLMClientConfig
from .callsite import one_shot_json, one_shot_text
from .client import AsyncLLMClient
from .iterate import IterativeRunner, ToolExecutor
from .stages import StageSpec

# Per-batch fan-out cap for `run_many`. The bridge has no rate limiter of its
# own (unlike the async pipeline's `bounded_gather`), and each filter batch
# already runs inside one of the pipeline's `max_workers` function analyses,
# so keep the inner concurrency modest to avoid multiplying up into 429s.
DEFAULT_STAGE_CONCURRENCY = 5


class SyncStageRunner:
    """Run one or more stage calls from sync code.

    Spins up a fresh `AsyncLLMClient` + event loop per `run` / `run_many`
    invocation, then closes them cleanly. For batch verdicts (the common
    filter case) call `run_many` once so the httpx pool is amortized over
    every issue in the batch — and the prompts run concurrently under a
    bounded semaphore.
    """

    def __init__(
        self,
        client_config: LLMClientConfig,
        *,
        max_concurrency: int = DEFAULT_STAGE_CONCURRENCY,
    ):
        self._config = client_config
        self._max_concurrency = max(1, int(max_concurrency))

    def run(
        self,
        stage: StageSpec,
        user_prompt: str,
        *,
        tools: Optional[ToolExecutor] = None,
        max_iterations: Optional[int] = None,
    ) -> Optional[Any]:
        """Run one stage call. Returns the parsed JSON, or None on failure."""
        results = self.run_many(
            stage, [user_prompt], tools=tools, max_iterations=max_iterations
        )
        return results[0] if results else None

    def run_many(
        self,
        stage: StageSpec,
        user_prompts: List[str],
        *,
        tools: Optional[ToolExecutor] = None,
        max_iterations: Optional[int] = None,
    ) -> List[Optional[Any]]:
        """Run the stage once per prompt, concurrently. Returns parsed values.

        Prompts run under a bounded semaphore (`max_concurrency`) against one
        shared client; results are returned in input order so callers can zip
        them back to their inputs. Each slot is `None` if that call returned no
        usable result; that's a soft failure — callers should treat it as
        "keep the issue / no verdict" (the legacy code does this).

        Concurrency is safe: `IterativeRunner.run` builds a fresh
        `ConversationState` per call, no conversation logger is attached, and
        any `tools` executor already handles concurrent `execute()` (tool calls
        within a single iteration are dispatched concurrently).
        """
        if not user_prompts:
            return []

        async def _runner() -> List[Optional[Any]]:
            sem = asyncio.Semaphore(self._max_concurrency)
            async with AsyncLLMClient(self._config) as client:
                runner = IterativeRunner(client)

                async def _run_one(prompt: str) -> Optional[Any]:
                    async with sem:
                        outcome = await runner.run(
                            stage,
                            user_prompt=prompt,
                            tools=tools,
                            max_iterations=max_iterations,
                        )
                    if outcome.error or outcome.text is None:
                        return None
                    try:
                        return json.loads(outcome.text)
                    except json.JSONDecodeError:
                        return None

                # gather() preserves input order, which callers rely on to zip
                # verdicts back to their issues. return_exceptions keeps one bad
                # prompt from cancelling the whole batch.
                results = await asyncio.gather(
                    *(_run_one(p) for p in user_prompts),
                    return_exceptions=True,
                )
                return [None if isinstance(r, Exception) else r for r in results]

        return asyncio.run(_runner())


def make_client_config_from_dict(
    *,
    api_key: str,
    config: dict,
    default_api_url: str,
    default_model: str,
    default_max_tokens: int,
) -> LLMClientConfig:
    """Convenience: build an `LLMClientConfig` from a dict config.

    Mirrors how the legacy filters constructed `ClaudeConfig`. Keeps the
    fall-back constants explicit at the call site so each filter can keep
    its own defaults.
    """
    return LLMClientConfig(
        api_url=config.get("api_end_point", default_api_url),
        model=config.get("model", default_model),
        max_tokens=int(config.get("max_tokens", default_max_tokens)),
        api_key=api_key,
    )


def one_shot_text_sync(
    client_config: LLMClientConfig,
    *,
    system_prompt: str,
    user_prompt: str,
    enable_system_cache: bool = True,
) -> Optional[str]:
    """Sync wrapper around `callsite.one_shot_text` — for single-call,
    no-tool callers like the directory classifier and file summary CLI.

    Returns `None` on API failure; caller decides whether to retry.
    """

    async def _runner() -> Optional[str]:
        async with AsyncLLMClient(client_config) as client:
            return await one_shot_text(
                client,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                enable_system_cache=enable_system_cache,
            )

    return asyncio.run(_runner())


def one_shot_json_sync(
    client_config: LLMClientConfig,
    *,
    system_prompt: str,
    user_prompt: str,
    enable_system_cache: bool = True,
) -> Optional[Any]:
    """Sync wrapper around `callsite.one_shot_json`."""

    async def _runner() -> Optional[Any]:
        async with AsyncLLMClient(client_config) as client:
            return await one_shot_json(
                client,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                enable_system_cache=enable_system_cache,
            )

    return asyncio.run(_runner())
