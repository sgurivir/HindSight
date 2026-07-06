"""Trace analysis pipeline — per-callstack Stage Ta → Tb → Tc.

Mirror of `pipeline_code.py` / `pipeline_perf.py` tuned for trace analysis.
A trace work unit is one callstack pulled from a hotspot file:

    Stage Ta — `stage_ta_trace_context`     → context bundle (dict)
    Stage Tb — `stage_tb_trace_analysis`    → list of trace-issues
    Stage Tc — `stage_tc_trace_validator`   → per-issue verdict (kept / rejected /
                                              kept-with-low-confidence)

The pipeline does NOT publish results itself. The trace publisher
(`TraceAnalysisResultsPublisher`) has a different shape from the
code/diff publisher (`add_trace_result(repo_name, trace_id, callstack, result)`),
and the runner already owns it. To keep concerns separated, the runner
provides an async `publish_callback` invoked once per successful trace —
the pipeline calls it before emitting `FunctionCompleteEvent`.

The runner may also provide an async `cache_check_callback` so the pipeline
can short-circuit traces whose `callstack_text` is already in the
`AnalyzedRecordsRegistry` (the publisher's own dedupe registry). When the
callback returns True, the trace is counted as `cached` and Ta/Tb/Tc are
skipped entirely — no LLM call, no rate-limit slot consumed.

Events: `RunStartedEvent` → `FunctionStartEvent` / `FunctionCompleteEvent`
/ `FunctionFailedEvent` per trace → `RunCompletedEvent` (or
`RunFailedEvent` on outer crash). The `function_name` slot is repurposed
to carry the trace id (`trace_0001`).
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from hindsight.llm import (
    IterativeRunner,
    stage_ta_trace_context,
    stage_tb_trace_analysis,
    stage_tc_trace_validator,
)
from hindsight.utils.log_util import get_logger

from .events import (
    FunctionCompleteEvent,
    FunctionFailedEvent,
    FunctionStartEvent,
    RunCompletedEvent,
    RunFailedEvent,
    RunStartedEvent,
)
from .session import AnalysisSession
from .prior_knowledge import format_prior_knowledge_for_functions
from .worker import bounded_gather

logger = get_logger(__name__)


TokenCallback = Callable[[int, int], None]


def _iter_trace_frame_identities(work: "TraceWork"):
    """Yield (function_name, file_path, checksum) for each frame in the trace's
    callstack. Accepts several shapes: frames may be dicts with `function` or
    `function_name` plus `file`/`file_path`, or the runner may have flattened
    strings. `checksum` is unknown at this point — always None.
    """
    if not work.callstack:
        return
    for frame in work.callstack:
        if isinstance(frame, str):
            name = frame.strip()
            if name:
                yield (name, None, None)
            continue
        if not isinstance(frame, dict):
            continue
        name = (
            frame.get("function_name")
            or frame.get("function")
            or frame.get("name")
            or ""
        ).strip()
        if not name:
            continue
        file_path = (
            frame.get("file_path")
            or frame.get("file")
            or frame.get("file_name")
        )
        yield (name, file_path or None, None)


@dataclass(frozen=True)
class TraceWork:
    """One callstack work item built by the trace runner.

    `callstack_text` is the one-function-per-line text form used as the
    cache key in `AnalyzedRecordsRegistry`. `prompt_content` is the full
    text prompt that Stage Ta consumes — the runner builds it via
    `TraceAnalysisPromptBuilder` from the structured callstack data.
    """

    callstack_index: int
    callstack: Tuple[Any, ...]
    prompt_content: str
    callstack_data: Optional[Dict[str, Any]]
    extracted_file_paths: Tuple[str, ...]
    callstack_text: str
    trace_id: str  # "trace_0001"

    @property
    def display_file_path(self) -> str:
        """Best-effort file path for the FunctionStartEvent display.

        Pulls from the first callstack entry's metadata when available;
        falls back to an empty string. Used only for progress display.
        """
        if not self.callstack:
            return ""
        first = self.callstack[0]
        if isinstance(first, dict):
            for key in ("file_path", "file", "file_name"):
                value = first.get(key)
                if isinstance(value, str) and value:
                    return value
        return ""


@dataclass(frozen=True)
class TraceRunSummary:
    """Result of a `TracePipeline.analyze_traces()` call."""

    pipeline: str = "trace"
    selected: int = 0
    successful: int = 0
    failed: int = 0
    cached: int = 0
    duration_seconds: float = 0.0
    error: Optional[str] = None


@dataclass
class _MutableCounts:
    successful: int = 0
    failed: int = 0
    cached: int = 0


# `publish_callback(work, issues) -> True` on successful publish, False on
# failure. Awaited; expected to do any sync work via `to_thread`.
PublishCallback = Callable[[TraceWork, List[Dict[str, Any]]], Awaitable[bool]]

# `cache_check_callback(work) -> True` if the trace has already been
# analyzed (cache hit). Awaited; expected to acquire any needed sync locks.
CacheCheckCallback = Callable[[TraceWork], Awaitable[bool]]


@dataclass
class TracePipeline:
    """Per-callstack trace analysis driver.

    One `TracePipeline` per `AnalysisSession` per run. Callbacks are
    injected by the runner so the pipeline stays decoupled from the
    sync publisher / registry.
    """

    session: AnalysisSession
    publish_callback: Optional[PublishCallback] = None
    cache_check_callback: Optional[CacheCheckCallback] = None
    token_callback: Optional[TokenCallback] = None

    # ------------------------------------------------------------------
    # Public API — single callstack
    # ------------------------------------------------------------------

    async def analyze_trace(self, work: TraceWork) -> Optional[List[Dict[str, Any]]]:
        """Run Ta → Tb → Tc for one callstack.

        Returns the validated issues list (each annotated with
        ``trace_id`` and ``callstack`` keys), an empty list when the LLM
        is confident no issues exist, or None when Stage Ta or Tb fails
        irrecoverably. Tc failures are non-fatal — issues fall through
        with a low-confidence annotation.
        """
        bundle = await self._run_stage_ta(work)
        if bundle is None:
            logger.warning(
                f"[{work.trace_id}] Stage Ta returned no bundle; skipping Tb"
            )
            return None

        issues = await self._run_stage_tb(bundle, work)
        if issues is None:
            return None

        if issues:
            issues = await self._validate_solutions(issues, bundle, work)

        return self._annotate_issues(issues, work)

    # ------------------------------------------------------------------
    # Public API — full run
    # ------------------------------------------------------------------

    async def analyze_traces(self, work: List[TraceWork]) -> TraceRunSummary:
        """Fan out across every callstack with `bounded_gather`.

        Per-trace failures emit `FunctionFailedEvent` and contribute to
        the `failed` count but do not abort the run.
        """
        t0 = time.monotonic()
        counts = _MutableCounts()
        selected = len(work)

        try:
            await self.session.emit(
                RunStartedEvent(pipeline="trace", total_units=selected)
            )

            if selected == 0:
                return await self._finalize_ok(t0, counts, selected)

            indexed = list(enumerate(work, 1))

            async def _do_one(item: Tuple[int, TraceWork]) -> None:
                idx, w = item
                await self._process_trace(
                    w, idx=idx, total=selected, counts=counts
                )

            await bounded_gather(
                indexed,
                _do_one,
                max_concurrency=self.session.ctx.max_workers,
                rate_limiter=self.session.rate_limiter,
            )

            return await self._finalize_ok(t0, counts, selected)

        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — outer guard
            error_msg = f"{type(exc).__name__}: {exc}"
            logger.exception(f"TracePipeline.analyze_traces crashed: {error_msg}")
            return await self._finalize_failed(t0, counts, selected, error_msg)

    # ------------------------------------------------------------------
    # Per-trace unit
    # ------------------------------------------------------------------

    async def _process_trace(
        self,
        work: TraceWork,
        *,
        idx: int,
        total: int,
        counts: _MutableCounts,
    ) -> None:
        display_path = work.display_file_path

        logger.info(f"Analyzing [{idx}/{total}] {work.trace_id} ({display_path})")

        await self.session.emit(
            FunctionStartEvent(
                pipeline="trace",
                function_name=work.trace_id,
                file_path=display_path,
                index=idx,
                total=total,
            )
        )
        t0 = time.monotonic()

        # Cache check — short-circuits Ta/Tb/Tc when the trace is already
        # analyzed. The legacy runner does this twice (registry + publisher);
        # the runner-provided callback covers both.
        if self.cache_check_callback is not None:
            try:
                if await self.cache_check_callback(work):
                    counts.cached += 1
                    await self.session.emit(
                        FunctionCompleteEvent(
                            pipeline="trace",
                            function_name=work.trace_id,
                            file_path=display_path,
                            issues=[],
                            duration_seconds=time.monotonic() - t0,
                            cached=True,
                            index=idx,
                            total=total,
                        )
                    )
                    return
            except Exception as exc:  # noqa: BLE001 — cache check must not fail the run
                logger.warning(
                    f"[{work.trace_id}] cache_check raised: {exc} — continuing with full analysis"
                )

        try:
            issues = await self.analyze_trace(work)
        except Exception as exc:  # noqa: BLE001 — per-trace isolation
            logger.exception(
                f"[{work.trace_id}] analyze_trace raised: {exc}"
            )
            issues = None

        if issues is None:
            counts.failed += 1
            await self.session.emit(
                FunctionFailedEvent(
                    pipeline="trace",
                    function_name=work.trace_id,
                    file_path=display_path,
                    error="Stage Ta or Tb produced no usable result",
                    stage="analyze_trace",
                )
            )
            return

        if self.publish_callback is not None:
            try:
                ok = await self.publish_callback(work, issues)
            except Exception as exc:  # noqa: BLE001 — per-trace isolation
                logger.exception(
                    f"[{work.trace_id}] publish_callback raised: {exc}"
                )
                ok = False
            if not ok:
                counts.failed += 1
                await self.session.emit(
                    FunctionFailedEvent(
                        pipeline="trace",
                        function_name=work.trace_id,
                        file_path=display_path,
                        error="publish failed",
                        stage="publish",
                    )
                )
                return

        counts.successful += 1
        await self.session.emit(
            FunctionCompleteEvent(
                pipeline="trace",
                function_name=work.trace_id,
                file_path=display_path,
                issues=issues,
                duration_seconds=time.monotonic() - t0,
                cached=False,
                index=idx,
                total=total,
            )
        )

    # ------------------------------------------------------------------
    # Stage Ta — context collection
    # ------------------------------------------------------------------

    async def _run_stage_ta(self, work: TraceWork) -> Optional[Dict[str, Any]]:
        try:
            system_prompt = await asyncio.to_thread(
                self._load_prompt_file, "traceContextCollectionProcess.md"
            )
        except Exception as exc:  # noqa: BLE001
            logger.error(f"[{work.trace_id}] Stage Ta prompt load failed: {exc}")
            return None
        if not system_prompt:
            logger.error(f"[{work.trace_id}] Stage Ta prompt is empty — cannot run")
            return None

        # Hydrate the system prompt with prior learnings about every frame in
        # the callstack. Janus-style: with the full callstack known upfront,
        # cache hits let the LLM skip file reads on intermediate frames.
        prior_block = format_prior_knowledge_for_functions(
            self.session.knowledge_store,
            subject="trace",
            functions=_iter_trace_frame_identities(work),
        )
        if prior_block:
            system_prompt = f"{system_prompt}\n\n{prior_block}"

        user_prompt = self._build_stage_ta_user_prompt(
            work.prompt_content, work.extracted_file_paths
        )

        runner = IterativeRunner(
            self.session.llm, conversation_logger=self.session.conversation_logger
        )
        outcome = await runner.run(
            stage_ta_trace_context(system_prompt),
            user_prompt=user_prompt,
            tools=self.session.tools,
            context_info=f"trace_context:{work.trace_id}",
            token_callback=self._token_usage_relay(),
        )

        if outcome.error or outcome.text is None:
            logger.warning(
                f"[{work.trace_id}] Stage Ta failed "
                f"(error={outcome.error}, iterations={outcome.iterations})"
            )
            return None

        try:
            bundle = json.loads(outcome.text)
        except json.JSONDecodeError as exc:
            logger.warning(f"[{work.trace_id}] Stage Ta JSON decode failed: {exc}")
            return None

        if isinstance(bundle, list):
            candidate = next(
                (b for b in bundle if isinstance(b, dict) and "call_path" in b),
                None,
            )
            if candidate is None:
                logger.warning(
                    f"[{work.trace_id}] Stage Ta returned a list with no valid bundle"
                )
                return None
            bundle = candidate

        if not isinstance(bundle, dict):
            logger.warning(
                f"[{work.trace_id}] Stage Ta returned non-dict type {type(bundle).__name__}"
            )
            return None

        return bundle

    # ------------------------------------------------------------------
    # Stage Tb — trace analysis
    # ------------------------------------------------------------------

    async def _run_stage_tb(
        self,
        bundle: Dict[str, Any],
        work: TraceWork,
    ) -> Optional[List[Dict[str, Any]]]:
        try:
            system_prompt = await asyncio.to_thread(
                self._load_prompt_file, "traceAnalysisProcess.md"
            )
        except Exception as exc:  # noqa: BLE001
            logger.error(f"[{work.trace_id}] Stage Tb prompt load failed: {exc}")
            return None
        if not system_prompt:
            logger.error(f"[{work.trace_id}] Stage Tb prompt is empty — cannot run")
            return None

        # Hydrate the analysis prompt with prior learnings for every frame in
        # the callstack — same set Stage Ta was hydrated with. Cross-cutting
        # invariants (threading rules, lock ordering) live only in the KB and
        # would otherwise force Tb to re-issue `lookup_knowledge` to see them.
        prior_block = format_prior_knowledge_for_functions(
            self.session.knowledge_store,
            subject="trace",
            functions=_iter_trace_frame_identities(work),
        )
        if prior_block:
            system_prompt = f"{system_prompt}\n\n{prior_block}"

        user_prompt = (
            "## Context Bundle\n\n"
            "Analyze the following pre-collected context for performance issues:\n\n"
            f"```json\n{json.dumps(bundle, indent=2, ensure_ascii=False)}\n```\n"
        )

        runner = IterativeRunner(
            self.session.llm, conversation_logger=self.session.conversation_logger
        )
        outcome = await runner.run(
            stage_tb_trace_analysis(system_prompt),
            user_prompt=user_prompt,
            tools=self.session.tools,
            context_info=f"trace_analysis:{work.trace_id}",
            token_callback=self._token_usage_relay(),
        )

        if outcome.error or outcome.text is None:
            logger.warning(
                f"[{work.trace_id}] Stage Tb failed "
                f"(error={outcome.error}, iterations={outcome.iterations})"
            )
            return None

        try:
            issues = json.loads(outcome.text)
        except json.JSONDecodeError as exc:
            logger.warning(f"[{work.trace_id}] Stage Tb JSON decode failed: {exc}")
            return None

        if isinstance(issues, dict):
            if "results" in issues and isinstance(issues["results"], list):
                issues = issues["results"]
            else:
                issues = [issues]

        if not isinstance(issues, list):
            logger.warning(
                f"[{work.trace_id}] Stage Tb expected list, got {type(issues).__name__}"
            )
            return None

        return [i for i in issues if isinstance(i, dict)]

    # ------------------------------------------------------------------
    # Stage Tc — solution validation
    # ------------------------------------------------------------------

    async def _validate_solutions(
        self,
        issues: List[Dict[str, Any]],
        bundle: Dict[str, Any],
        work: TraceWork,
    ) -> List[Dict[str, Any]]:
        """Validate each issue's solution. Conservative defaults: any error
        keeps the issue with a `validation.low_confidence=True` annotation
        so a flaky validator can't silently drop real findings.
        """
        try:
            system_prompt = await asyncio.to_thread(
                self._load_prompt_file, "traceSolutionValidator.md"
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                f"[{work.trace_id}] Stage Tc prompt load failed ({exc}); "
                "skipping validation"
            )
            return issues
        if not system_prompt:
            logger.warning(
                f"[{work.trace_id}] Stage Tc prompt is empty; skipping validation"
            )
            return issues

        validated: List[Dict[str, Any]] = []
        dropped = 0
        for idx, issue in enumerate(issues, 1):
            kept = await self._validate_one_issue(
                issue, bundle, system_prompt, work, idx
            )
            if kept is None:
                dropped += 1
            else:
                validated.append(kept)

        logger.info(
            f"[{work.trace_id}] Stage Tc: kept {len(validated)}/{len(issues)} "
            f"(dropped={dropped})"
        )
        return validated

    async def _validate_one_issue(
        self,
        issue: Dict[str, Any],
        bundle: Dict[str, Any],
        system_prompt: str,
        work: TraceWork,
        issue_index: int,
    ) -> Optional[Dict[str, Any]]:
        """Returns the (possibly annotated) issue to keep, or None to drop."""
        function_name = issue.get("functionName") or issue.get("function_name") or "unknown"

        user_prompt = (
            "## Issue to Validate\n\n"
            f"```json\n{json.dumps(issue, indent=2, ensure_ascii=False)}\n```\n\n"
            "## Full Context Bundle\n\n"
            "The following is the same pre-collected context the analyzer used. "
            "It contains the call path and every function the analyzer inspected. "
            "If something you need is missing, use the tools described in the system prompt.\n\n"
            f"```json\n{json.dumps(bundle, indent=2, ensure_ascii=False)}\n```\n"
        )

        runner = IterativeRunner(
            self.session.llm, conversation_logger=self.session.conversation_logger
        )
        try:
            outcome = await runner.run(
                stage_tc_trace_validator(system_prompt),
                user_prompt=user_prompt,
                tools=self.session.tools,
                context_info=f"trace_validate:{work.trace_id}:{function_name}",
                token_callback=self._token_usage_relay(),
            )
        except Exception as exc:  # noqa: BLE001 — validator must not crash the trace
            logger.warning(
                f"[{work.trace_id}] Tc #{issue_index} ({function_name}) crashed: {exc}; "
                "keeping issue with low_confidence"
            )
            return self._annotate_low_confidence(issue, f"Validator raised exception: {exc}")

        if outcome.error or outcome.text is None:
            logger.warning(
                f"[{work.trace_id}] Tc #{issue_index} ({function_name}) "
                f"returned no parseable output (error={outcome.error}); keeping with low_confidence"
            )
            return self._annotate_low_confidence(
                issue, "Validator returned no parseable output."
            )

        try:
            verdict = json.loads(outcome.text)
        except json.JSONDecodeError as exc:
            logger.warning(
                f"[{work.trace_id}] Tc #{issue_index} ({function_name}) "
                f"unparseable verdict: {exc}; keeping with low_confidence"
            )
            return self._annotate_low_confidence(
                issue, f"Validator produced unparseable output: {exc}"
            )

        is_valid = verdict.get("valid", True) if isinstance(verdict, dict) else True
        low_confidence = bool(
            isinstance(verdict, dict) and verdict.get("low_confidence", False)
        )
        reason = (verdict or {}).get("reason", "") if isinstance(verdict, dict) else ""

        if not is_valid and not low_confidence:
            logger.info(
                f"[{work.trace_id}] Tc #{issue_index} ({function_name}): "
                f"REJECTED — {reason[:200]}"
            )
            return None

        if low_confidence:
            annotated = dict(issue)
            annotated["validation"] = {
                "low_confidence": True,
                "valid": is_valid,
                "reason": reason,
            }
            return annotated

        return issue

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_stage_ta_user_prompt(
        prompt_content: str,
        extracted_file_paths: Tuple[str, ...],
    ) -> str:
        parts = [
            "## Callstack Trace\n\n",
            "Collect context for the following callstack trace:\n\n",
            "======\n",
            prompt_content,
            "\n======\n",
        ]
        valid_paths = [p for p in extracted_file_paths if p and p.strip()]
        if valid_paths:
            parts.append("\n## Known File Paths\n\n")
            parts.append("These files have been identified in the trace:\n\n")
            for path in valid_paths:
                parts.append(f"- {path}\n")
            parts.append("\n")
        return "".join(parts)

    @staticmethod
    def _annotate_low_confidence(
        issue: Dict[str, Any],
        reason: str,
    ) -> Dict[str, Any]:
        annotated = dict(issue)
        annotated["validation"] = {
            "low_confidence": True,
            "reason": reason,
        }
        return annotated

    @staticmethod
    def _annotate_issues(
        issues: List[Dict[str, Any]],
        work: TraceWork,
    ) -> List[Dict[str, Any]]:
        """Stamp every issue with the trace id + callstack lines so the
        report generator (and downstream dedupe) can group issues by trace
        without consulting the publisher state."""
        callstack_list = (
            [line.strip() for line in work.callstack_text.split("\n") if line.strip()]
            if work.callstack_text
            else []
        )
        out: List[Dict[str, Any]] = []
        for issue in issues:
            if not isinstance(issue, dict):
                continue
            issue.setdefault("trace_id", work.trace_id)
            if callstack_list:
                issue.setdefault("Callstack", "\n".join(callstack_list))
            if work.callstack_data and "original_callstack" not in issue:
                issue["original_callstack"] = work.callstack_data
            out.append(issue)
        return out

    @staticmethod
    def _load_prompt_file(filename: str) -> str:
        """Load a prompt markdown file from `hindsight/core/prompts/`."""
        from pathlib import Path

        prompts_dir = Path(__file__).resolve().parent.parent / "core" / "prompts"
        filepath = prompts_dir / filename
        try:
            return filepath.read_text(encoding="utf-8")
        except FileNotFoundError:
            logger.error(f"Prompt file not found: {filepath}")
            return ""

    def _token_usage_relay(self) -> Optional[Callable[[Any, int], None]]:
        if self.token_callback is None:
            return None
        forward = self.token_callback

        def _relay(response: Any, _iteration: int) -> None:
            try:
                forward(response.input_tokens, response.output_tokens)
            except Exception as exc:  # noqa: BLE001
                logger.debug(f"token_callback raised: {exc}")

        return _relay

    # ------------------------------------------------------------------
    # Summary builders
    # ------------------------------------------------------------------

    async def _finalize_ok(
        self,
        start: float,
        counts: _MutableCounts,
        selected: int,
    ) -> TraceRunSummary:
        summary = TraceRunSummary(
            selected=selected,
            successful=counts.successful,
            failed=counts.failed,
            cached=counts.cached,
            duration_seconds=time.monotonic() - start,
            error=None,
        )
        await self.session.emit(
            RunCompletedEvent(
                pipeline="trace",
                successful=summary.successful,
                failed=summary.failed,
                cached=summary.cached,
                duration_seconds=summary.duration_seconds,
            )
        )
        return summary

    async def _finalize_failed(
        self,
        start: float,
        counts: _MutableCounts,
        selected: int,
        error_msg: str,
    ) -> TraceRunSummary:
        summary = TraceRunSummary(
            selected=selected,
            successful=counts.successful,
            failed=counts.failed,
            cached=counts.cached,
            duration_seconds=time.monotonic() - start,
            error=error_msg,
        )
        await self.session.emit(
            RunFailedEvent(
                pipeline="trace",
                error=error_msg,
                successful=summary.successful,
                failed=summary.failed,
                cached=summary.cached,
                duration_seconds=summary.duration_seconds,
            )
        )
        return summary


__all__ = [
    "TracePipeline",
    "TraceRunSummary",
    "TraceWork",
    "PublishCallback",
    "CacheCheckCallback",
]
