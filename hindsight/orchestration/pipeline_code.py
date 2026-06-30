"""Code analysis pipeline — per-function (Stage 4a → 4b) + call-tree mode.

This is the heart of the new orchestration stack. One `CodePipeline` per
session per run. The public surface mirrors the FastAPI handshake from the
plan:

    pipeline = CodePipeline(session, sink, ast_index=ast)
    summary = await pipeline.analyze_repo(call_graph_data, filters)
    # OR for fine-grained control:
    issues = await pipeline.analyze_function(work_item)
    # OR call-tree-at-once:
    issues = await pipeline.analyze_call_tree(root, builder)

Events flow through `session.emit(...)` — pipelines never raise into their
caller. A subscriber/iterator sees:

    run_started → function_started → function_complete*  → run_completed
                                   ↘  function_failed*  ↗
                                                       ↘ run_failed
                                                          (only if the
                                                           pipeline itself
                                                           crashed)

Partial results are durable: every `function_complete` corresponds to an
`AsyncResultSink.publish()` call that persisted the issues to disk before the
event was emitted. A FastAPI client that disconnects mid-run can come back
and read the partial state from `~/llm_artifacts/{repo}/results/code_analysis/`.

Fault tolerance contract:
  - Stage A failure for one function → that function's Stage B is skipped,
    `function_failed` is emitted, the run continues.
  - Stage B failure for one function → same.
  - LLM returning malformed JSON → stage's `extract_json`/`validate_json`
    already retries with fallback guidance; eventually treated as a soft
    failure if the budget runs out.
  - Publisher failure → emitted as `function_failed` with stage="publish".
  - Issue-filter failure → unfiltered issues are published (logged warning).
  - Outer crash (config issue, unexpected exception) → `run_failed`.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from hindsight.llm import (
    IterativeRunner,
    stage_4a_context_collection,
    stage_4b_analysis,
    stage_call_tree_code,
)
from hindsight.utils.hash_util import HashUtil
from hindsight.utils.log_util import get_logger

from .events import (
    FunctionCompleteEvent,
    FunctionFailedEvent,
    FunctionStartEvent,
    RunCompletedEvent,
    RunFailedEvent,
    RunStartedEvent,
)
from .function_selector import FunctionFilters, FunctionWorkItem, select_functions
from .result_sink import AsyncResultSink
from .session import AnalysisSession
from .worker import bounded_gather

logger = get_logger(__name__)


# `issue_filter(issues, function_context) -> filtered_issues`. Sync because
# the legacy UnifiedIssueFilter is sync; the pipeline runs it in to_thread.
IssueFilter = Callable[[List[Dict[str, Any]], Optional[str]], List[Dict[str, Any]]]

# `token_callback(input_tokens, output_tokens)`. Fire-and-forget; exceptions
# from the callback are swallowed so instrumentation can't crash analysis.
TokenCallback = Callable[[int, int], None]


@dataclass(frozen=True)
class CodeRunSummary:
    """Result of a `CodePipeline.analyze_repo()` call."""

    pipeline: str = "code"
    selected: int = 0
    successful: int = 0      # new analyses that published successfully
    failed: int = 0          # analyses that failed at any stage
    cached: int = 0          # cache hits served without an LLM call
    duration_seconds: float = 0.0
    error: Optional[str] = None  # set iff the run itself aborted

    @property
    def total_units(self) -> int:
        return self.successful + self.failed + self.cached


@dataclass
class _MutableCounts:
    """Mutable counts used inside `analyze_repo`. Frozen `CodeRunSummary` is
    constructed from this at the end of the run."""

    successful: int = 0
    failed: int = 0
    cached: int = 0


@dataclass
class CodePipeline:
    """Per-function and call-tree code analysis driver.

    Holds references to the session (LLM client + tools + event fan-out),
    the result sink (write-through publisher), and optional hooks for the
    UnifiedIssueFilter and TokenTracker that the legacy runner uses.
    """

    session: AnalysisSession
    sink: AsyncResultSink
    ast_index: Any = None                  # RepoAstIndex; optional, only used by Stage 4a prompts
    issue_filter: Optional[IssueFilter] = None
    token_callback: Optional[TokenCallback] = None

    # ------------------------------------------------------------------
    # Public API — single function
    # ------------------------------------------------------------------

    async def analyze_function(
        self,
        work_item: FunctionWorkItem,
    ) -> Optional[List[Dict[str, Any]]]:
        """Run Stage 4a → 4b for one function.

        Returns the (raw, un-filtered) issues list, an empty list when the
        LLM is confident no defects exist, or None when either stage fails
        irrecoverably. The caller decides whether to publish.
        """
        func_entry = work_item.func_entry
        function_name = work_item.function_name
        file_path = work_item.primary_file
        bundle_checksum = self._bundle_checksum(file_path, function_name)

        bundle = await self._run_stage_4a(func_entry, bundle_checksum)
        if bundle is None:
            logger.warning(f"Stage 4a returned no bundle for {function_name}; skipping 4b")
            return None

        return await self._run_stage_4b(bundle, function_name)

    # ------------------------------------------------------------------
    # Public API — single call tree (one LLM run over the whole subtree)
    # ------------------------------------------------------------------

    async def analyze_call_tree(
        self,
        root_name: str,
        builder: Any,
    ) -> Optional[List[Dict[str, Any]]]:
        """One LLM run rooted at `root_name` with the whole subtree in the prompt.

        Returns the list of issues (each one tagged with `defect_function`
        identifying which node in the tree it pertains to), or None on a
        hard failure. The caller is responsible for grouping/publishing —
        `_publish_call_tree_issues` does this when called from `analyze_repo`.
        """
        tree = await asyncio.to_thread(builder.build, root_name)
        if tree is None:
            logger.warning(f"Call-tree builder returned None for root {root_name}")
            return None
        return await self._run_call_tree_stage(tree, root_name)

    # ------------------------------------------------------------------
    # Public API — full repo (dispatches per-function vs call-tree)
    # ------------------------------------------------------------------

    async def analyze_repo(
        self,
        call_graph_data: List[Dict[str, Any]],
        filters: FunctionFilters,
        *,
        num_to_analyze: Optional[int] = None,
        call_tree_builder: Any = None,
    ) -> CodeRunSummary:
        """Top-level fan-out across the selected functions or call-tree roots.

        Args:
            call_graph_data: Loaded merged_call_graph.json contents.
            filters: Result of resolving --file-filter / --function-filter /
                --include/exclude options to a typed object.
            num_to_analyze: Optional cap on the work-list size (legacy
                `--num-functions-to-analyze`).
            call_tree_builder: When provided AND `session.ctx.enable_call_tree`
                is True, drives the call-tree mode. Built externally so the
                CLI rewire keeps owning the builder's lifecycle.

        Returns:
            A `CodeRunSummary`. The run is "ok" iff `summary.error is None`;
            per-function failures still appear in `summary.failed` and do not
            mark the whole run as failed.

        Events emitted via `self.session.emit`:
            RunStartedEvent → FunctionStartEvent / FunctionCompleteEvent /
            FunctionFailedEvent (per item) → RunCompletedEvent (or
            RunFailedEvent on outer crash).
        """
        t0 = time.monotonic()
        counts = _MutableCounts()
        selected = 0

        try:
            use_call_tree = bool(
                self.session.ctx.enable_call_tree and call_tree_builder is not None
            )

            if use_call_tree:
                roots = self._select_call_tree_roots(call_graph_data, filters, num_to_analyze)
                selected = len(roots)
            else:
                work = select_functions(call_graph_data, filters)
                if num_to_analyze is not None and num_to_analyze < len(work):
                    work = work[:num_to_analyze]
                selected = len(work)

            await self.session.emit(RunStartedEvent(pipeline="code", total_units=selected))

            if selected == 0:
                summary = self._finalize_summary(t0, counts, selected, error=None)
                await self.session.emit(
                    RunCompletedEvent(
                        pipeline="code",
                        successful=summary.successful,
                        failed=summary.failed,
                        cached=summary.cached,
                        duration_seconds=summary.duration_seconds,
                    )
                )
                return summary

            if use_call_tree:
                await self._run_call_tree_fan_out(roots, call_tree_builder, counts)
            else:
                await self._run_per_function_fan_out(work, counts)

            summary = self._finalize_summary(t0, counts, selected, error=None)
            await self.session.emit(
                RunCompletedEvent(
                    pipeline="code",
                    successful=summary.successful,
                    failed=summary.failed,
                    cached=summary.cached,
                    duration_seconds=summary.duration_seconds,
                )
            )
            return summary

        except asyncio.CancelledError:
            # Propagate cancellation so the surrounding task can finish cleanly.
            raise
        except Exception as exc:  # noqa: BLE001 — outer-guard: anything else becomes RunFailedEvent
            error_msg = f"{type(exc).__name__}: {exc}"
            logger.exception(f"CodePipeline.analyze_repo crashed: {error_msg}")
            summary = self._finalize_summary(t0, counts, selected, error=error_msg)
            await self.session.emit(
                RunFailedEvent(
                    pipeline="code",
                    error=error_msg,
                    successful=summary.successful,
                    failed=summary.failed,
                    cached=summary.cached,
                    duration_seconds=summary.duration_seconds,
                )
            )
            return summary

    # ------------------------------------------------------------------
    # Per-function fan-out
    # ------------------------------------------------------------------

    async def _run_per_function_fan_out(
        self,
        work: List[FunctionWorkItem],
        counts: _MutableCounts,
    ) -> None:
        indexed = list(enumerate(work, 1))
        total = len(work)

        async def _do_one(item: Tuple[int, FunctionWorkItem]) -> None:
            idx, w = item
            await self._process_function_unit(w, idx=idx, total=total, counts=counts)

        await bounded_gather(
            indexed,
            _do_one,
            max_concurrency=self.session.ctx.max_workers,
            rate_limiter=self.session.rate_limiter,
        )

    async def _process_function_unit(
        self,
        w: FunctionWorkItem,
        *,
        idx: int,
        total: int,
        counts: _MutableCounts,
    ) -> None:
        """One unit of per-function work: cache check, analyze, publish, emit."""
        function_name = w.function_name
        file_path = w.primary_file
        function_checksum = self._function_source_checksum(w.func_entry, file_path)

        await self.session.emit(
            FunctionStartEvent(
                pipeline="code",
                function_name=function_name,
                file_path=file_path,
                index=idx,
                total=total,
            )
        )
        t0 = time.monotonic()

        # Cache check (write-through means we can republish without re-running).
        cached_result = await self.sink.check_existing(
            file_path=file_path, function=function_name, checksum=function_checksum
        )
        if cached_result is not None:
            await self._republish_cached(
                cached_result,
                file_path=file_path,
                function_name=function_name,
                function_checksum=function_checksum,
                idx=idx,
                total=total,
                start_time=t0,
                counts=counts,
            )
            return

        # New analysis.
        try:
            issues = await self.analyze_function(w)
        except Exception as exc:  # noqa: BLE001 — per-function isolation
            logger.exception(f"analyze_function raised for {function_name}: {exc}")
            issues = None

        if issues is None:
            counts.failed += 1
            await self.session.emit(
                FunctionFailedEvent(
                    pipeline="code",
                    function_name=function_name,
                    file_path=file_path,
                    error="LLM analysis produced no usable result",
                    stage="analyze_function",
                )
            )
            return

        filtered = await self._apply_issue_filter(issues, w.func_entry)

        outcome = await self.sink.publish(
            file_path=file_path,
            function=function_name,
            checksum=function_checksum,
            issues=filtered,
        )
        if not outcome.ok:
            counts.failed += 1
            await self.session.emit(
                FunctionFailedEvent(
                    pipeline="code",
                    function_name=function_name,
                    file_path=file_path,
                    error=f"publish failed: {outcome.error}",
                    stage="publish",
                )
            )
            return

        counts.successful += 1
        await self.session.emit(
            FunctionCompleteEvent(
                pipeline="code",
                function_name=function_name,
                file_path=file_path,
                issues=filtered,
                duration_seconds=time.monotonic() - t0,
                cached=False,
                index=idx,
                total=total,
            )
        )

    async def _republish_cached(
        self,
        cached_result: Dict[str, Any],
        *,
        file_path: str,
        function_name: str,
        function_checksum: str,
        idx: int,
        total: int,
        start_time: float,
        counts: _MutableCounts,
    ) -> None:
        """Re-publish a cached result so this run's publisher index is complete.

        The legacy code does this so the in-memory `results_publisher` has every
        function (cached or freshly analyzed) when the report generator queries
        it. Cache-derived issues are also filtered (Level-1 category filter
        only — the legacy unified filter scopes Level 1 to cached results too).
        """
        cached_issues_raw = cached_result.get("results", []) if isinstance(cached_result, dict) else []
        cached_issues = [i for i in cached_issues_raw if isinstance(i, dict)]
        filtered = await self._apply_issue_filter(cached_issues, None)

        outcome = await self.sink.publish(
            file_path=file_path,
            function=function_name,
            checksum=function_checksum,
            issues=filtered,
        )
        if not outcome.ok:
            # Publish failure on cached path is unusual but still soft-fail.
            counts.failed += 1
            await self.session.emit(
                FunctionFailedEvent(
                    pipeline="code",
                    function_name=function_name,
                    file_path=file_path,
                    error=f"republish failed: {outcome.error}",
                    stage="publish_cached",
                )
            )
            return

        counts.cached += 1
        await self.session.emit(
            FunctionCompleteEvent(
                pipeline="code",
                function_name=function_name,
                file_path=file_path,
                issues=filtered,
                duration_seconds=time.monotonic() - start_time,
                cached=True,
                index=idx,
                total=total,
            )
        )

    # ------------------------------------------------------------------
    # Call-tree fan-out
    # ------------------------------------------------------------------

    def _select_call_tree_roots(
        self,
        call_graph_data: List[Dict[str, Any]],
        filters: FunctionFilters,
        num_to_analyze: Optional[int],
    ) -> List[str]:
        """Roots = every interesting function (matches legacy behavior)."""
        work = select_functions(call_graph_data, filters)
        roots = sorted({w.function_name for w in work if w.function_name})
        if num_to_analyze is not None and num_to_analyze < len(roots):
            roots = roots[:num_to_analyze]
        return roots

    async def _run_call_tree_fan_out(
        self,
        roots: List[str],
        builder: Any,
        counts: _MutableCounts,
    ) -> None:
        indexed = list(enumerate(roots, 1))
        total = len(roots)

        async def _do_one(item: Tuple[int, str]) -> None:
            idx, root = item
            await self._process_call_tree_root(root, builder, idx=idx, total=total, counts=counts)

        await bounded_gather(
            indexed,
            _do_one,
            max_concurrency=self.session.ctx.max_workers,
            rate_limiter=self.session.rate_limiter,
        )

    async def _process_call_tree_root(
        self,
        root_name: str,
        builder: Any,
        *,
        idx: int,
        total: int,
        counts: _MutableCounts,
    ) -> None:
        tree = await asyncio.to_thread(builder.build, root_name)
        if tree is None:
            counts.failed += 1
            await self.session.emit(
                FunctionFailedEvent(
                    pipeline="code",
                    function_name=root_name,
                    file_path="",
                    error="could not build call tree",
                    stage="build_tree",
                )
            )
            return

        file_path = tree.root_file
        await self.session.emit(
            FunctionStartEvent(
                pipeline="code",
                function_name=root_name,
                file_path=file_path,
                index=idx,
                total=total,
            )
        )
        t0 = time.monotonic()

        try:
            issues = await self._run_call_tree_stage(tree, root_name)
        except Exception as exc:  # noqa: BLE001 — per-root isolation
            logger.exception(f"call-tree analyze raised for {root_name}: {exc}")
            issues = None

        if issues is None:
            counts.failed += 1
            await self.session.emit(
                FunctionFailedEvent(
                    pipeline="code",
                    function_name=root_name,
                    file_path=file_path,
                    error="call-tree analysis produced no usable result",
                    stage="analyze_call_tree",
                )
            )
            return

        groups = self._group_call_tree_issues(tree, issues)
        total_published = await self._publish_call_tree_groups(groups, tree)

        counts.successful += 1
        await self.session.emit(
            FunctionCompleteEvent(
                pipeline="code",
                function_name=root_name,
                file_path=file_path,
                issues=[i for g in groups.values() for i in g["issues"]],
                duration_seconds=time.monotonic() - t0,
                cached=False,
                index=idx,
                total=total,
            )
        )

    async def _publish_call_tree_groups(
        self,
        groups: Dict[str, Dict[str, Any]],
        tree: Any,
    ) -> int:
        """Publish each `defect_function`-keyed group separately.

        Returns total published issue count for logging. If a group's publish
        fails we log + continue (the rest of the tree's groups still land).
        """
        published = 0
        for defect_fn, group in groups.items():
            file_path: str = group["file"]
            checksum: str = group["checksum"]
            issues: List[Dict[str, Any]] = group["issues"]
            filtered = await self._apply_issue_filter(issues, None)
            outcome = await self.sink.publish(
                file_path=file_path,
                function=defect_fn,
                checksum=checksum,
                issues=filtered,
            )
            if outcome.ok:
                published += len(filtered)
            else:
                logger.error(
                    f"call-tree publish failed for {defect_fn}: {outcome.error}"
                )

        # Even when zero issues found, record an empty entry under the root so
        # the cache reflects "this root was analyzed → no defects".
        if not groups:
            await self.sink.publish(
                file_path=tree.root_file,
                function=tree.root,
                checksum=tree.root_checksum,
                issues=[],
            )
        return published

    # ------------------------------------------------------------------
    # Stage runners
    # ------------------------------------------------------------------

    async def _run_stage_4a(
        self,
        func_entry: Dict[str, Any],
        bundle_checksum: str,
    ) -> Optional[Dict[str, Any]]:
        """Stage 4a — Context Collection.

        Cached on disk under `{artifacts}/context_bundles/{checksum[:8]}.json`.
        Malformed cached bundles are deleted and re-run.
        """
        bundle = await self._load_cached_bundle(bundle_checksum)
        if bundle is not None:
            return bundle

        system_prompt, user_prompt = await asyncio.to_thread(
            self._build_stage_4a_prompts, func_entry
        )

        runner = IterativeRunner(
            self.session.llm, conversation_logger=self.session.conversation_logger
        )
        ctx_info = os.path.basename(func_entry.get("context", {}).get("file", "") or "")
        outcome = await runner.run(
            stage_4a_context_collection(system_prompt),
            user_prompt=user_prompt,
            tools=self.session.tools,
            context_info=ctx_info,
            token_callback=self._token_usage_relay(),
        )

        if outcome.error or outcome.text is None:
            logger.warning(
                f"Stage 4a failed (error={outcome.error}, iterations={outcome.iterations})"
            )
            return None

        bundle = self._parse_context_bundle(outcome.text)
        if bundle is None:
            return None

        await self._save_bundle(bundle, bundle_checksum)
        return bundle

    async def _run_stage_4b(
        self,
        context_bundle: Dict[str, Any],
        function_name: str,
    ) -> Optional[List[Dict[str, Any]]]:
        """Stage 4b — Analysis from Context."""
        # Local import — `hindsight.core.prompts` has a circular import with
        # `hindsight.core.llm`; importing it lazily here lets the cycle resolve
        # through the existing legacy entry point before we touch it.
        from hindsight.llm.prompts import PromptBuilder

        system_prompt, user_prompt = await asyncio.to_thread(
            PromptBuilder.build_analysis_from_context_prompt,
            context_bundle=context_bundle,
            config=self.session.ctx.raw_config,
        )

        runner = IterativeRunner(
            self.session.llm, conversation_logger=self.session.conversation_logger
        )
        outcome = await runner.run(
            stage_4b_analysis(system_prompt),
            user_prompt=user_prompt,
            tools=self.session.tools,
            context_info=function_name,
            token_callback=self._token_usage_relay(),
        )

        if outcome.error or outcome.text is None:
            logger.warning(
                f"Stage 4b failed (error={outcome.error}, iterations={outcome.iterations})"
            )
            return None

        return self._parse_issue_list(outcome.text)

    async def _run_call_tree_stage(
        self,
        tree: Any,
        root_name: str,
    ) -> Optional[List[Dict[str, Any]]]:
        from hindsight.llm.prompts import PromptBuilder  # lazy: see _run_stage_4b note

        tree_dict = tree.to_dict()
        user_prompts = list(self.session.ctx.user_provided_prompts) or None

        system_prompt, user_prompt = await asyncio.to_thread(
            PromptBuilder.build_call_tree_prompt,
            tree_dict=tree_dict,
            config=self.session.ctx.raw_config,
            user_provided_prompts=user_prompts,
        )

        runner = IterativeRunner(
            self.session.llm, conversation_logger=self.session.conversation_logger
        )
        outcome = await runner.run(
            stage_call_tree_code(system_prompt),
            user_prompt=user_prompt,
            tools=self.session.tools,
            context_info=root_name,
            token_callback=self._token_usage_relay(),
        )

        if outcome.error or outcome.text is None:
            logger.warning(
                f"Call-tree stage failed for {root_name} "
                f"(error={outcome.error}, iterations={outcome.iterations})"
            )
            return None

        return self._parse_issue_list(outcome.text)

    # ------------------------------------------------------------------
    # Prompt assembly (Stage 4a needs AST index data)
    # ------------------------------------------------------------------

    def _build_stage_4a_prompts(self, func_entry: Dict[str, Any]) -> Tuple[str, str]:
        """Build (system, user) prompts for Stage 4a.

        Runs in `to_thread` because PromptBuilder reads .md files. The AST
        index's properties are lazy-loaded but cached, so repeated calls are
        cheap once warmed.
        """
        from hindsight.llm.prompts import PromptBuilder  # lazy: see _run_stage_4b note

        json_content = json.dumps(func_entry, ensure_ascii=False)
        user_prompts = list(self.session.ctx.user_provided_prompts) or None
        merged_functions = getattr(self.ast_index, "merged_functions", None) if self.ast_index else None
        merged_types = getattr(self.ast_index, "merged_types", None) if self.ast_index else None
        merged_call_graph = getattr(self.ast_index, "merged_call_graph", None) if self.ast_index else None
        return PromptBuilder.build_context_collection_prompt(
            json_content=json_content,
            config=self.session.ctx.raw_config,
            merged_functions_data=merged_functions,
            merged_data_types_data=merged_types,
            merged_call_graph_data=merged_call_graph,
            user_provided_prompts=user_prompts,
        )

    # ------------------------------------------------------------------
    # Disk cache for Stage 4a bundles
    # ------------------------------------------------------------------

    @staticmethod
    def _bundle_checksum(file_path: str, function_name: str) -> str:
        """MD5 hex of `{file}:{function}` — matches the legacy bundle cache key."""
        key = f"{file_path}:{function_name}"
        return hashlib.md5(key.encode("utf-8")).hexdigest()

    def _bundle_path(self, bundle_checksum: str) -> str:
        return os.path.join(self.session.ctx.context_bundles_dir, f"{bundle_checksum[:8]}.json")

    async def _load_cached_bundle(self, bundle_checksum: str) -> Optional[Dict[str, Any]]:
        """Load + validate a cached Stage 4a bundle, deleting it if malformed."""
        path = self._bundle_path(bundle_checksum)
        if not await asyncio.to_thread(os.path.exists, path):
            return None
        try:
            raw = await asyncio.to_thread(self._read_text, path)
            data = json.loads(raw)
        except Exception as exc:  # noqa: BLE001 — soft-fail on bad cache
            logger.warning(f"Failed to read cached bundle at {path}: {exc}")
            return None
        if not isinstance(data, dict) or "primary_function" not in data:
            logger.warning(
                f"Cached bundle at {path} is malformed (no 'primary_function'); deleting"
            )
            await asyncio.to_thread(self._unlink_silent, path)
            return None
        return data

    async def _save_bundle(self, bundle: Dict[str, Any], bundle_checksum: str) -> None:
        path = self._bundle_path(bundle_checksum)
        try:
            await asyncio.to_thread(self._write_bundle_sync, path, bundle)
        except Exception as exc:  # noqa: BLE001 — cache write must not fail the run
            logger.warning(f"Could not save bundle to {path}: {exc}")

    @staticmethod
    def _read_text(path: str) -> str:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()

    @staticmethod
    def _unlink_silent(path: str) -> None:
        try:
            os.remove(path)
        except OSError:
            pass

    @staticmethod
    def _write_bundle_sync(path: str, bundle: Dict[str, Any]) -> None:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(bundle, fh, indent=2, ensure_ascii=False)

    # ------------------------------------------------------------------
    # Parsing helpers — tolerant to LLM imperfection
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_context_bundle(text: str) -> Optional[Dict[str, Any]]:
        """Stage 4a output is supposed to be a dict with `primary_function`.

        Stage spec's `extract_json` has already done the structural search;
        this is just `json.loads` with array-unwrapping for the case where the
        LLM emitted `[bundle]` instead of `bundle`.
        """
        try:
            value = json.loads(text)
        except json.JSONDecodeError as exc:
            logger.warning(f"Stage 4a JSON decode failed: {exc}")
            return None
        if isinstance(value, list):
            candidate = next(
                (b for b in value if isinstance(b, dict) and "primary_function" in b),
                None,
            )
            if candidate:
                return candidate
            logger.warning("Stage 4a returned a list with no valid bundle inside")
            return None
        if not isinstance(value, dict):
            logger.warning(f"Stage 4a returned unexpected type {type(value).__name__}")
            return None
        return value

    @staticmethod
    def _parse_issue_list(text: str) -> List[Dict[str, Any]]:
        """Parse a Stage-4b/call-tree LLM result text into a list of issue dicts.

        Empty list is a valid result ("no defects"). Dict with `results` key
        is unwrapped (some prompts produce that shape).
        """
        try:
            value = json.loads(text)
        except json.JSONDecodeError as exc:
            logger.warning(f"Issue-list JSON decode failed: {exc}")
            return []
        if isinstance(value, list):
            return [i for i in value if isinstance(i, dict)]
        if isinstance(value, dict):
            if "results" in value and isinstance(value["results"], list):
                return [i for i in value["results"] if isinstance(i, dict)]
            return [value]
        return []

    # ------------------------------------------------------------------
    # Call-tree issue grouping
    # ------------------------------------------------------------------

    @staticmethod
    def _group_call_tree_issues(
        tree: Any,
        issues: List[Dict[str, Any]],
    ) -> Dict[str, Dict[str, Any]]:
        """Group call-tree issues by `defect_function`.

        Mirrors legacy `_publish_call_tree_issues` so the on-disk layout under
        `results/code_analysis/` matches: each defect lands under the function
        node that actually contains it, with that node's checksum.

        Out-of-tree defect functions fall back to the root.
        """
        node_lookup: Dict[str, Tuple[str, str]] = {
            n.function: (n.file, n.checksum) for n in tree.nodes
        }
        groups: Dict[str, Dict[str, Any]] = {}
        for raw in issues:
            if not isinstance(raw, dict):
                continue
            defect_fn = (
                raw.get("defect_function")
                or raw.get("function_name")
                or tree.root
            )
            normalized = dict(raw)
            normalized.setdefault("function_name", defect_fn)
            if "file_path" not in normalized:
                normalized["file_path"] = raw.get("defect_file", "")
            if "file_name" not in normalized and normalized.get("file_path"):
                normalized["file_name"] = os.path.basename(normalized["file_path"])
            if "line_number" not in normalized:
                normalized["line_number"] = str(raw.get("defect_line_number", ""))
            if "issueType" not in normalized:
                normalized["issueType"] = normalized.get("category", "logicBug")
            if "severity" not in normalized:
                normalized["severity"] = "medium"

            # Out-of-tree defect → re-pin to root so the publish key is valid.
            if defect_fn not in node_lookup:
                defect_fn = tree.root
            file_path, checksum = node_lookup.get(
                defect_fn, (tree.root_file, tree.root_checksum)
            )
            slot = groups.setdefault(
                defect_fn,
                {"file": file_path, "checksum": checksum, "issues": []},
            )
            slot["issues"].append(normalized)
        return groups

    # ------------------------------------------------------------------
    # Issue filter (UnifiedIssueFilter is sync; wrap in to_thread)
    # ------------------------------------------------------------------

    async def _apply_issue_filter(
        self,
        issues: List[Dict[str, Any]],
        func_entry: Optional[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        if not self.issue_filter or not issues:
            return issues
        function_context: Optional[str] = None
        if isinstance(func_entry, dict):
            function_context = (
                func_entry.get("code")
                or func_entry.get("context", {}).get("function_context")
            )
        try:
            return await asyncio.to_thread(self.issue_filter, issues, function_context)
        except Exception as exc:  # noqa: BLE001 — filter must never fail the run
            logger.warning(f"issue_filter raised; keeping unfiltered issues: {exc}")
            return issues

    # ------------------------------------------------------------------
    # Token bookkeeping
    # ------------------------------------------------------------------

    def _token_usage_relay(self) -> Optional[Callable[[Any, int], None]]:
        """Build a `token_usage_callback` for IterativeRunner that forwards
        per-iteration usage to the orchestrator's `token_callback`.

        Returns None if no callback was provided; the runner short-circuits.
        """
        if self.token_callback is None:
            return None

        forward = self.token_callback

        def _relay(response: Any, _iteration: int) -> None:
            try:
                forward(response.input_tokens, response.output_tokens)
            except Exception as exc:  # noqa: BLE001 — instrumentation must not crash analysis
                logger.debug(f"token_callback raised: {exc}")

        return _relay

    # ------------------------------------------------------------------
    # Source checksum (matches legacy HashUtil call)
    # ------------------------------------------------------------------

    def _function_source_checksum(
        self,
        func_entry: Dict[str, Any],
        file_path: str,
    ) -> str:
        """Source-content checksum used as the publisher cache key.

        Matches legacy `HashUtil.checksum_for_function_source(repo, file, s, e)`.
        Falls back to an empty string when start/end are missing — the publisher
        treats that as a cache miss (correct behavior for malformed entries).
        """
        ctx = func_entry.get("context", {}) if isinstance(func_entry, dict) else {}
        start = int(ctx.get("start", 0) or 0)
        end = int(ctx.get("end", 0) or 0)
        try:
            return HashUtil.checksum_for_function_source(
                self.session.ctx.repo_path, file_path, start, end
            )
        except Exception as exc:  # noqa: BLE001 — soft-fail to a stable miss
            logger.debug(f"checksum_for_function_source failed: {exc}")
            return ""

    # ------------------------------------------------------------------
    # Summary builder
    # ------------------------------------------------------------------

    @staticmethod
    def _finalize_summary(
        start: float,
        counts: _MutableCounts,
        selected: int,
        *,
        error: Optional[str],
    ) -> CodeRunSummary:
        return CodeRunSummary(
            selected=selected,
            successful=counts.successful,
            failed=counts.failed,
            cached=counts.cached,
            duration_seconds=time.monotonic() - start,
            error=error,
        )
