"""Perf analysis pipeline — path-based Stage A → Stage B.

Mirror of `pipeline_code.py` but tuned for the performance workflow. Per the
design plan, the perf analyzer operates on **call paths** (lists of function
names) rather than individual functions; one path is one unit of work.

Stage A (`stage_perf_context`) collects context for every function along the
path, with a content-checksum keyed `PerfContextCache` so functions that
appear in multiple paths only get analyzed once. Stage B (`stage_perf_analysis`)
consumes the bundle and returns an array of perf issues; the pipeline
annotates each issue with the originating `call_path` before returning.

The legacy `PerfAnalysis` class (`hindsight/core/llm/perf_analysis.py`) saved
all issues to a single timestamped JSON file at the end of the run. The new
pipeline preserves that behaviour by returning the aggregated list to the
caller (`PerfAnalysisRunner`); per-path publishing through `AsyncResultSink`
would change the artifacts layout and break the existing report tooling.

Events: `RunStartedEvent` → `FunctionStartEvent` / `FunctionCompleteEvent` /
`FunctionFailedEvent` per path → `RunCompletedEvent` (or `RunFailedEvent` on
outer crash). The `function_name` slot is repurposed to carry a short path
identifier (`first→...→last`) so subscribers can render meaningful progress.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from hindsight.llm import (
    IterativeRunner,
    stage_perf_analysis,
    stage_perf_context,
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
from .worker import bounded_gather

logger = get_logger(__name__)


TokenCallback = Callable[[int, int], None]


# Tool sets — match the legacy `PerfAnalysis._run_context_collection` /
# `_run_performance_analysis` lists verbatim.
_STAGE_A_TOOLS = (
    "readFile",
    "runTerminalCmd",
    "getSummaryOfFile",
    "inspectDirectoryHierarchy",
    "list_files",
    "getFileContentByLines",
    "checkFileSize",
)
_STAGE_B_TOOLS = ("readFile", "runTerminalCmd", "getFileContentByLines")


@dataclass(frozen=True)
class PerfPathWork:
    """One call-path work item built by the perf runner.

    `function_bodies` mirrors the legacy `PerfAnalysis._get_function_bodies`
    output: `{func_name: {"file": str, "start_line": int, "end_line": int,
    "body": str}}`. The pipeline only uses this to build the Stage A prompt;
    AST lookup happens in the runner.
    """

    path: Tuple[str, ...]                 # ordered function names, root → leaf
    function_bodies: Dict[str, Dict[str, Any]]
    function_checksums: Dict[str, str]    # func_name → content-checksum


@dataclass(frozen=True)
class PerfRunSummary:
    """Result of a `PerfPipeline.analyze_paths()` call."""

    pipeline: str = "perf"
    selected: int = 0
    successful: int = 0
    failed: int = 0
    duration_seconds: float = 0.0
    error: Optional[str] = None
    issues: Tuple[Dict[str, Any], ...] = field(default_factory=tuple)


@dataclass
class _MutableCounts:
    successful: int = 0
    failed: int = 0


# ----------------------------------------------------------------------
# Context cache — minimal in-process implementation
# ----------------------------------------------------------------------


class _InProcessPerfContextCache:
    """Per-function context cache used by `PerfPipeline`.

    The legacy `PerfContextCache` persisted entries to
    `{artifacts}/perf_context_cache/{checksum}.json`. This new in-process
    cache is identical in shape — `get(name, checksum)` / `put(name, checksum,
    ctx)` — but lives only in memory for one pipeline run, which matches how
    paths share functions within a single CLI invocation. Cross-run disk
    persistence can be re-added by swapping this class with a disk-backed
    one without changing the pipeline.
    """

    def __init__(self) -> None:
        self._store: Dict[str, Dict[str, Any]] = {}
        self.hits = 0
        self.misses = 0

    def get(self, func_name: str, checksum: str) -> Optional[Dict[str, Any]]:
        cached = self._store.get(checksum)
        if cached is not None:
            self.hits += 1
            return cached
        self.misses += 1
        return None

    def put(self, func_name: str, checksum: str, ctx: Dict[str, Any]) -> None:
        self._store[checksum] = ctx


# ----------------------------------------------------------------------
# Pipeline
# ----------------------------------------------------------------------


@dataclass
class PerfPipeline:
    """Per-path performance analysis driver.

    One `PerfPipeline` per `AnalysisSession` per run. Holds the per-function
    context cache so two paths sharing a function only collect that function's
    context once.
    """

    session: AnalysisSession
    token_callback: Optional[TokenCallback] = None
    _cache: _InProcessPerfContextCache = field(default_factory=_InProcessPerfContextCache)

    # ------------------------------------------------------------------
    # Public API — single path
    # ------------------------------------------------------------------

    async def analyze_path(self, work: PerfPathWork) -> Optional[List[Dict[str, Any]]]:
        """Stage A → Stage B for one call path.

        Returns the issues list (annotated with `call_path` + `category`), an
        empty list when no issues are found, or None on a hard failure.
        """
        path_id = self._path_id(work.path)
        logger.info(f"Perf analyze_path starting: {path_id}")

        bundle = await self._run_stage_a(work)
        if bundle is None:
            logger.warning(f"Stage A returned no bundle for {path_id}; skipping Stage B")
            return None

        issues = await self._run_stage_b(bundle, work)
        if issues is None:
            return None

        annotated = self._annotate_issues(issues, work.path)
        return annotated

    # ------------------------------------------------------------------
    # Public API — full repo (all paths)
    # ------------------------------------------------------------------

    async def analyze_paths(
        self,
        work: List[PerfPathWork],
    ) -> PerfRunSummary:
        """Fan out across every path with `bounded_gather`; aggregate issues.

        Per-path failures emit `FunctionFailedEvent` and contribute to the
        `failed` count but do not abort the run. The aggregated issues list
        is returned on the summary (the caller dedupes / saves to disk).
        """
        t0 = time.monotonic()
        counts = _MutableCounts()
        selected = len(work)
        aggregated: List[Dict[str, Any]] = []
        agg_lock = asyncio.Lock()  # protects aggregated under bounded_gather concurrency

        try:
            await self.session.emit(RunStartedEvent(pipeline="perf", total_units=selected))

            if selected == 0:
                return await self._finalize_ok(t0, counts, selected, aggregated)

            indexed = list(enumerate(work, 1))

            async def _do_one(item: Tuple[int, PerfPathWork]) -> None:
                idx, w = item
                await self._process_path(
                    w, idx=idx, total=selected, counts=counts,
                    aggregated=aggregated, agg_lock=agg_lock,
                )

            await bounded_gather(
                indexed,
                _do_one,
                max_concurrency=self.session.ctx.max_workers,
                rate_limiter=self.session.rate_limiter,
            )

            return await self._finalize_ok(t0, counts, selected, aggregated)

        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — outer guard
            error_msg = f"{type(exc).__name__}: {exc}"
            logger.exception(f"PerfPipeline.analyze_paths crashed: {error_msg}")
            return await self._finalize_failed(t0, counts, selected, aggregated, error_msg)

    # ------------------------------------------------------------------
    # Per-path unit
    # ------------------------------------------------------------------

    async def _process_path(
        self,
        work: PerfPathWork,
        *,
        idx: int,
        total: int,
        counts: _MutableCounts,
        aggregated: List[Dict[str, Any]],
        agg_lock: asyncio.Lock,
    ) -> None:
        path_id = self._path_id(work.path)
        first_file = (
            work.function_bodies.get(work.path[0], {}).get("file", "")
            if work.path else ""
        )

        await self.session.emit(
            FunctionStartEvent(
                pipeline="perf",
                function_name=path_id,
                file_path=first_file,
                index=idx,
                total=total,
            )
        )
        t0 = time.monotonic()

        try:
            issues = await self.analyze_path(work)
        except Exception as exc:  # noqa: BLE001 — per-path isolation
            logger.exception(f"analyze_path raised for {path_id}: {exc}")
            issues = None

        if issues is None:
            counts.failed += 1
            await self.session.emit(
                FunctionFailedEvent(
                    pipeline="perf",
                    function_name=path_id,
                    file_path=first_file,
                    error="Stage A or Stage B produced no usable result",
                    stage="analyze_path",
                )
            )
            return

        async with agg_lock:
            aggregated.extend(issues)

        counts.successful += 1
        await self.session.emit(
            FunctionCompleteEvent(
                pipeline="perf",
                function_name=path_id,
                file_path=first_file,
                issues=issues,
                duration_seconds=time.monotonic() - t0,
                cached=False,
                index=idx,
                total=total,
            )
        )

    # ------------------------------------------------------------------
    # Stage A — context collection
    # ------------------------------------------------------------------

    async def _run_stage_a(self, work: PerfPathWork) -> Optional[Dict[str, Any]]:
        cached_contexts, novel_functions = self._partition_by_cache(work)
        logger.info(
            f"Stage A: {len(cached_contexts)} cached, {len(novel_functions)} novel functions"
        )

        try:
            system_prompt = await asyncio.to_thread(
                self._load_prompt_file, "perfContextCollectionProcess.md"
            )
        except Exception as exc:  # noqa: BLE001
            logger.error(f"Could not load Stage A prompt: {exc}")
            return None

        user_prompt = self._build_context_collection_user_prompt(
            work.path, work.function_bodies, cached_contexts, novel_functions
        )

        runner = IterativeRunner(
            self.session.llm, conversation_logger=self.session.conversation_logger
        )
        outcome = await runner.run(
            stage_perf_context(system_prompt),
            user_prompt=user_prompt,
            tools=self.session.tools,
            context_info=f"perf_context:{self._path_id(work.path)}",
            token_callback=self._token_usage_relay(),
        )

        if outcome.error or outcome.text is None:
            logger.warning(
                f"Stage A failed (error={outcome.error}, iterations={outcome.iterations})"
            )
            return None

        try:
            bundle = json.loads(outcome.text)
        except json.JSONDecodeError as exc:
            logger.warning(f"Stage A response was not valid JSON: {exc}")
            return None

        if not isinstance(bundle, dict):
            logger.warning(f"Stage A: expected dict, got {type(bundle).__name__}")
            return None

        # Cache newly collected per-function contexts.
        functions_data = bundle.get("functions", {})
        if isinstance(functions_data, dict):
            for func_name, func_context in functions_data.items():
                if func_name in novel_functions and isinstance(func_context, dict):
                    checksum = work.function_checksums.get(func_name)
                    if checksum:
                        self._cache.put(func_name, checksum, func_context)

        return bundle

    # ------------------------------------------------------------------
    # Stage B — performance analysis
    # ------------------------------------------------------------------

    async def _run_stage_b(
        self,
        bundle: Dict[str, Any],
        work: PerfPathWork,
    ) -> Optional[List[Dict[str, Any]]]:
        try:
            system_prompt = await asyncio.to_thread(
                self._load_prompt_file, "perfAnalysisProcess.md"
            )
        except Exception as exc:  # noqa: BLE001
            logger.error(f"Could not load Stage B prompt: {exc}")
            return None

        user_prompt = (
            "## Context Bundle\n\n"
            "```json\n"
            f"{json.dumps(bundle, indent=2, ensure_ascii=False)}\n"
            "```"
        )

        runner = IterativeRunner(
            self.session.llm, conversation_logger=self.session.conversation_logger
        )
        outcome = await runner.run(
            stage_perf_analysis(system_prompt),
            user_prompt=user_prompt,
            tools=self.session.tools,
            context_info=f"perf_analysis:{self._path_id(work.path)}",
            token_callback=self._token_usage_relay(),
        )

        if outcome.error or outcome.text is None:
            logger.warning(
                f"Stage B failed (error={outcome.error}, iterations={outcome.iterations})"
            )
            return None

        try:
            issues = json.loads(outcome.text)
        except json.JSONDecodeError as exc:
            logger.warning(f"Stage B response was not valid JSON: {exc}")
            return None

        if not isinstance(issues, list):
            logger.warning(f"Stage B: expected list, got {type(issues).__name__}")
            return None

        return [i for i in issues if isinstance(i, dict)]

    # ------------------------------------------------------------------
    # Prompt construction
    # ------------------------------------------------------------------

    @staticmethod
    def _build_context_collection_user_prompt(
        path: Tuple[str, ...],
        function_bodies: Dict[str, Dict[str, Any]],
        cached_contexts: Dict[str, Dict[str, Any]],
        novel_functions: List[str],
    ) -> str:
        """Render the Stage A user prompt — byte-identical to legacy."""
        parts = [
            f"## Call Path\n\n{' → '.join(path)}\n",
            "## Function Bodies\n",
        ]
        for func_name in path:
            body_info = function_bodies.get(func_name, {})
            body = body_info.get("body", "(body not available in AST)")
            file_path = body_info.get("file", "unknown")
            start = body_info.get("start_line", "?")
            parts.append(f"\n### {func_name}\nFile: {file_path} (line {start})\n```\n{body}\n```\n")

        if cached_contexts:
            parts.append("\n## Pre-Collected Context (already gathered — do not re-collect)\n")
            for func_name, ctx in cached_contexts.items():
                trimmed = json.dumps(ctx, indent=2)[:2000]
                parts.append(f"\n### {func_name} (cached)\n```json\n{trimmed}\n```\n")

        if novel_functions:
            parts.append("\n## Functions Requiring Context Collection\n\n")
            parts.append(", ".join(novel_functions))
            parts.append("\n\nFocus your tool usage on gathering context for these functions.\n")

        return "\n".join(parts)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _partition_by_cache(
        self, work: PerfPathWork,
    ) -> Tuple[Dict[str, Dict[str, Any]], List[str]]:
        cached: Dict[str, Dict[str, Any]] = {}
        novel: List[str] = []
        for func_name in work.path:
            checksum = work.function_checksums.get(func_name, "")
            if not checksum:
                novel.append(func_name)
                continue
            hit = self._cache.get(func_name, checksum)
            if hit is not None:
                cached[func_name] = hit
            else:
                novel.append(func_name)
        return cached, novel

    @staticmethod
    def _annotate_issues(
        issues: List[Dict[str, Any]],
        path: Tuple[str, ...],
    ) -> List[Dict[str, Any]]:
        path_list = list(path)
        out: List[Dict[str, Any]] = []
        for issue in issues:
            if not isinstance(issue, dict):
                continue
            issue.setdefault("category", "performance")
            issue.setdefault("call_path", path_list)
            out.append(issue)
        return out

    @staticmethod
    def _path_id(path: Tuple[str, ...]) -> str:
        if not path:
            return "<empty>"
        if len(path) <= 3:
            return "→".join(path)
        return "→".join(path[:2]) + "→...→" + path[-1]

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
            return "You are a performance engineer analyzing code for optimization opportunities."

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
        aggregated: List[Dict[str, Any]],
    ) -> PerfRunSummary:
        summary = PerfRunSummary(
            selected=selected,
            successful=counts.successful,
            failed=counts.failed,
            duration_seconds=time.monotonic() - start,
            error=None,
            issues=tuple(aggregated),
        )
        await self.session.emit(
            RunCompletedEvent(
                pipeline="perf",
                successful=summary.successful,
                failed=summary.failed,
                cached=0,
                duration_seconds=summary.duration_seconds,
            )
        )
        return summary

    async def _finalize_failed(
        self,
        start: float,
        counts: _MutableCounts,
        selected: int,
        aggregated: List[Dict[str, Any]],
        error_msg: str,
    ) -> PerfRunSummary:
        summary = PerfRunSummary(
            selected=selected,
            successful=counts.successful,
            failed=counts.failed,
            duration_seconds=time.monotonic() - start,
            error=error_msg,
            issues=tuple(aggregated),
        )
        await self.session.emit(
            RunFailedEvent(
                pipeline="perf",
                error=error_msg,
                successful=summary.successful,
                failed=summary.failed,
                cached=0,
                duration_seconds=summary.duration_seconds,
            )
        )
        return summary


# ----------------------------------------------------------------------
# Checksum helper — exposed so runners can build PerfPathWork items
# ----------------------------------------------------------------------


def perf_function_checksum(
    func_name: str,
    file_path: str,
    start_line: int,
    end_line: int,
) -> str:
    """Compute the legacy per-function checksum used as the context-cache key.

    Mirrors `PerfAnalysis._get_function_checksum`: `md5("{file}:{start}-{end}:{name}")[:16]`.
    """
    key = f"{file_path}:{start_line}-{end_line}:{func_name}"
    return hashlib.md5(key.encode()).hexdigest()[:16]
