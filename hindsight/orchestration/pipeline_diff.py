"""Diff analysis pipeline — Stage Da → Db per affected function + call-tree mode.

Mirror of `pipeline_code.py` but tuned for the diff workflow. Per the design
contract:

  - The runner (`GitSimpleCommitAnalyzer`) builds `prompt_data` dicts for each
    affected function (it has the AST artifacts, file content provider, and
    repo checkout dir needed for that). The pipeline accepts those dicts and
    drives the LLM stages.
  - For call-tree mode, the runner builds the diff-marked tree dicts; the
    pipeline runs one LLM call per root.
  - Per-function Stage Da bundles are cached on disk under
    `{artifacts}/diff_context_bundles/{md5_of_func@file[:8]}.json` (same shape
    as legacy `DiffAnalysis.run_diff_context_collection`).

The fault-tolerance contract from `pipeline_code.py` applies verbatim:
per-function failures isolated, write-through to disk, `RunFailedEvent` on
outer crash, soft-fail on cache/publisher errors, LLM-output sanitization
at the sink.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from hindsight.llm import (
    IterativeRunner,
    stage_call_tree_diff,
    stage_da_diff_context,
    stage_db_diff_analysis,
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
from .result_sink import AsyncResultSink
from .session import AnalysisSession
from .worker import bounded_gather

logger = get_logger(__name__)

# Diff stage prompt files live in `hindsight/core/prompts/` alongside the
# code-analysis ones. We read them lazily so this module can be imported
# without spinning up the prompts package (which has a circular import with
# `hindsight.core.llm`).
_DIFF_CONTEXT_PROMPT_FILE = "diffContextCollectionProcess.md"
_DIFF_ANALYSIS_PROMPT_FILE = "diffAnalysisProcess.md"

# Re-used hook shapes from pipeline_code.
IssueFilter = Callable[[List[Dict[str, Any]], Optional[str]], List[Dict[str, Any]]]
TokenCallback = Callable[[int, int], None]


# Default neighborhood for the "issue line is near a changed line" check.
# The diffAnalysisProcess prompt tells the model to report issues on `+` lines
# but allows reporting on an unchanged line *directly caused by an adjacent +
# change*. A neighborhood of 2 captures that "adjacent" relationship without
# admitting unrelated pre-existing issues.
DIFF_LINE_NEIGHBORHOOD = 2


def _parse_line_number(value: Any) -> Optional[Tuple[int, int]]:
    """Parse an issue's `line_number` field into an inclusive (start, end) range.

    Accepts `"45"`, `"45-48"`, or ints. Returns None when the value isn't a
    plain line reference (LLMs occasionally emit variable names — those should
    be kept unfiltered so we don't accidentally hide real findings).
    """
    if isinstance(value, int):
        return (value, value)
    s = str(value or "").strip()
    if not s:
        return None
    if "-" in s:
        parts = s.split("-", 1)
        try:
            start = int(parts[0].strip())
            end = int(parts[1].strip())
            return (start, end) if start <= end else (end, start)
        except ValueError:
            return None
    try:
        n = int(s)
        return (n, n)
    except ValueError:
        return None


def _filter_issues_to_changed_lines(
    issues: List[Dict[str, Any]],
    changed_lines_by_file: Dict[str, set],
    *,
    neighborhood: int = DIFF_LINE_NEIGHBORHOOD,
) -> Tuple[List[Dict[str, Any]], int]:
    """Drop issues whose `line_number` is outside the diff's changed lines.

    The LLM is instructed to focus on `+` (added) lines; this filter enforces
    that programmatically by checking each reported `line_number` against the
    actual changed-lines set for the issue's file (with `neighborhood` lines of
    tolerance for issues that the prompt allows on lines directly adjacent to
    a `+` change).

    Returns (kept_issues, dropped_count). An issue is *kept* (defensively) when
    its `line_number` can't be parsed, or when the file isn't in the diff at
    all (probably a tool-found auxiliary file — the prompt asks the model to
    focus on the function under analysis, but we don't want to drop real
    cross-file findings here).
    """
    if not changed_lines_by_file:
        return list(issues), 0

    kept: List[Dict[str, Any]] = []
    dropped = 0
    for issue in issues:
        if not isinstance(issue, dict):
            continue
        file_path = issue.get("file_path", "") or ""
        line_range = _parse_line_number(issue.get("line_number"))

        # If we can't parse, be defensive and keep.
        if line_range is None:
            kept.append(issue)
            continue

        changed = changed_lines_by_file.get(file_path)
        if changed is None:
            # File not in diff at all — drop. The diff analyzer should never
            # report issues outside changed files; if the LLM did, it's the
            # exact pre-existing-issue case we're filtering against.
            dropped += 1
            logger.debug(
                f"diff filter: dropping issue on '{file_path}:{issue.get('line_number')}' "
                "— file not in diff"
            )
            continue

        start, end = line_range
        # Any changed line within [start - neighborhood, end + neighborhood]?
        lo = start - neighborhood
        hi = end + neighborhood
        if any(lo <= cl <= hi for cl in changed):
            kept.append(issue)
        else:
            dropped += 1
            logger.debug(
                f"diff filter: dropping issue on '{file_path}:{issue.get('line_number')}' "
                f"— not within ±{neighborhood} of any changed line"
            )

    return kept, dropped


def _build_changed_lines_map(
    prompt_data: Dict[str, Any],
) -> Dict[str, set]:
    """Per-function: extract `{file_path: {changed_line_numbers}}` from a
    `prompt_data` work item built by `_build_function_diff_prompt`."""
    file_path = prompt_data.get("file_path") or ""
    changed_lines = prompt_data.get("changed_lines") or []
    if not file_path or not changed_lines:
        return {}
    try:
        return {file_path: {int(x) for x in changed_lines if isinstance(x, (int, str))}}
    except (TypeError, ValueError):
        return {}


def _build_changed_lines_map_from_diff_context(
    diff_context: Dict[str, Any],
) -> Dict[str, set]:
    """Call-tree: extract `{file_path: {added_line_numbers}}` from
    `diff_context["changed_lines_per_file"]`. Only `added` lines are used —
    `removed` line numbers refer to the old file and don't map onto the new
    source the model sees."""
    per_file = diff_context.get("changed_lines_per_file") or {}
    out: Dict[str, set] = {}
    for file_path, sections in per_file.items():
        if not isinstance(sections, dict):
            continue
        added = sections.get("added") or []
        try:
            line_set = {int(x) for x in added if isinstance(x, (int, str))}
        except (TypeError, ValueError):
            line_set = set()
        if line_set:
            out[file_path] = line_set
    return out


@dataclass(frozen=True)
class DiffFunctionWork:
    """One affected-function work item built by the diff runner."""

    prompt_data: Dict[str, Any]      # built via the analyzer's _build_function_diff_prompt
    function_name: str
    file_path: str
    function_checksum: str           # used as publisher cache key


@dataclass(frozen=True)
class DiffCallTreeWork:
    """One call-tree work item — root + diff-marked tree dict."""

    tree_dict: Dict[str, Any]        # output of CallTree.to_dict() + injected diff markers
    diff_context: Dict[str, Any]     # {'all_changed_files': [...], 'changed_lines_per_file': {...}}
    root_name: str
    root_file: str
    root_checksum: str


@dataclass(frozen=True)
class DiffRunSummary:
    """Result of a `DiffPipeline.analyze_diff*` call."""

    pipeline: str = "diff"
    selected: int = 0
    successful: int = 0
    failed: int = 0
    cached: int = 0
    duration_seconds: float = 0.0
    error: Optional[str] = None

    @property
    def total_units(self) -> int:
        return self.successful + self.failed + self.cached


@dataclass
class _MutableCounts:
    successful: int = 0
    failed: int = 0
    cached: int = 0


@dataclass
class DiffPipeline:
    """Per-affected-function and call-tree diff analysis driver."""

    session: AnalysisSession
    sink: AsyncResultSink
    issue_filter: Optional[IssueFilter] = None
    token_callback: Optional[TokenCallback] = None

    # ------------------------------------------------------------------
    # Public API — single function
    # ------------------------------------------------------------------

    async def analyze_function(
        self,
        prompt_data: Dict[str, Any],
    ) -> Optional[List[Dict[str, Any]]]:
        """Stage Da → Db for one affected function.

        Returns the issues list, an empty list when nothing is reported, or
        None on a hard failure. Caller is responsible for publishing.
        """
        func_name = prompt_data.get("function", "unknown")
        file_path = prompt_data.get("file_path", "unknown")
        bundle_checksum = self._bundle_checksum(func_name, file_path)

        bundle = await self._run_stage_da(prompt_data, bundle_checksum)
        if bundle is None:
            logger.warning(f"Stage Da returned no bundle for {func_name}; skipping Db")
            return None

        return await self._run_stage_db(bundle, func_name, file_path)

    # ------------------------------------------------------------------
    # Public API — single call tree
    # ------------------------------------------------------------------

    async def analyze_call_tree(
        self,
        tree_dict: Dict[str, Any],
        diff_context: Dict[str, Any],
        root_name: str,
    ) -> Optional[List[Dict[str, Any]]]:
        """One LLM run over the whole diff-marked subtree.

        Returns the list of issues (each tagged with `defect_function`), or
        None on hard failure. Caller groups + publishes (see
        `_publish_call_tree_groups`).
        """
        from hindsight.llm.prompts import PromptBuilder  # lazy — see pipeline_code

        user_prompts = list(self.session.ctx.user_provided_prompts) or None
        try:
            system_prompt, user_prompt = await asyncio.to_thread(
                PromptBuilder.build_diff_call_tree_prompt,
                tree_dict=tree_dict,
                diff_context=diff_context,
                config=self.session.ctx.raw_config,
                user_provided_prompts=user_prompts,
            )
        except Exception as exc:  # noqa: BLE001 — prompt build can fail on edge cases
            logger.error(f"build_diff_call_tree_prompt raised for {root_name}: {exc}")
            return None

        runner = IterativeRunner(
            self.session.llm, conversation_logger=self.session.conversation_logger
        )
        outcome = await runner.run(
            stage_call_tree_diff(system_prompt),
            user_prompt=user_prompt,
            tools=self.session.tools,
            context_info=root_name,
            token_callback=self._token_usage_relay(),
        )

        if outcome.error or outcome.text is None:
            logger.warning(
                f"Diff call-tree stage failed for {root_name} "
                f"(error={outcome.error}, iterations={outcome.iterations})"
            )
            return None
        return self._parse_issue_list(outcome.text)

    # ------------------------------------------------------------------
    # Public API — top-level fan-out (per function)
    # ------------------------------------------------------------------

    async def analyze_diff_per_function(
        self,
        work_items: List[DiffFunctionWork],
        *,
        num_to_analyze: Optional[int] = None,
    ) -> DiffRunSummary:
        """Drive Stage Da+Db across every affected function in parallel.

        Emits `run_started → fn_started/fn_complete/fn_failed* → run_completed`
        events through the session. On outer crash, emits `RunFailedEvent`
        with whatever partial counts are available.
        """
        t0 = time.monotonic()
        counts = _MutableCounts()

        try:
            items = list(work_items)
            if num_to_analyze is not None and num_to_analyze < len(items):
                items = items[:num_to_analyze]
            selected = len(items)
            await self.session.emit(RunStartedEvent(pipeline="diff", total_units=selected))

            if selected == 0:
                return await self._finalize_and_emit_completed(t0, counts, selected)

            await self._fan_out_per_function(items, counts)
            return await self._finalize_and_emit_completed(t0, counts, selected)

        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — outer guard
            return await self._finalize_and_emit_failed(t0, counts, exc)

    # ------------------------------------------------------------------
    # Public API — top-level fan-out (call tree)
    # ------------------------------------------------------------------

    async def analyze_diff_call_tree(
        self,
        work_items: List[DiffCallTreeWork],
        *,
        num_to_analyze: Optional[int] = None,
    ) -> DiffRunSummary:
        """Drive one LLM run per call-tree root.

        Each root's issues are grouped by `defect_function` and published
        separately so they land under the correct function record (matches
        legacy layout in `results/diff_analysis/`).
        """
        t0 = time.monotonic()
        counts = _MutableCounts()

        try:
            items = list(work_items)
            if num_to_analyze is not None and num_to_analyze < len(items):
                items = items[:num_to_analyze]
            selected = len(items)
            await self.session.emit(RunStartedEvent(pipeline="diff", total_units=selected))

            if selected == 0:
                return await self._finalize_and_emit_completed(t0, counts, selected)

            await self._fan_out_call_tree(items, counts)
            return await self._finalize_and_emit_completed(t0, counts, selected)

        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — outer guard
            return await self._finalize_and_emit_failed(t0, counts, exc)

    # ------------------------------------------------------------------
    # Per-function fan-out
    # ------------------------------------------------------------------

    async def _fan_out_per_function(
        self,
        items: List[DiffFunctionWork],
        counts: _MutableCounts,
    ) -> None:
        total = len(items)
        indexed = list(enumerate(items, 1))

        async def _do_one(item: Tuple[int, DiffFunctionWork]) -> None:
            idx, work = item
            await self._process_function_unit(work, idx=idx, total=total, counts=counts)

        await bounded_gather(
            indexed,
            _do_one,
            max_concurrency=self.session.ctx.max_workers,
            rate_limiter=self.session.rate_limiter,
        )

    async def _process_function_unit(
        self,
        work: DiffFunctionWork,
        *,
        idx: int,
        total: int,
        counts: _MutableCounts,
    ) -> None:
        await self.session.emit(
            FunctionStartEvent(
                pipeline="diff",
                function_name=work.function_name,
                file_path=work.file_path,
                index=idx,
                total=total,
            )
        )
        t0 = time.monotonic()

        cached = await self.sink.check_existing(
            file_path=work.file_path,
            function=work.function_name,
            checksum=work.function_checksum,
        )
        if cached is not None:
            await self._republish_cached(cached, work, idx=idx, total=total, t0=t0, counts=counts)
            return

        try:
            issues = await self.analyze_function(work.prompt_data)
        except Exception as exc:  # noqa: BLE001 — per-function isolation
            logger.exception(f"analyze_function raised for {work.function_name}: {exc}")
            issues = None

        if issues is None:
            counts.failed += 1
            await self.session.emit(
                FunctionFailedEvent(
                    pipeline="diff",
                    function_name=work.function_name,
                    file_path=work.file_path,
                    error="LLM analysis produced no usable result",
                    stage="analyze_function",
                )
            )
            return

        # Programmatic enforcement of the prompt's "issues must be on changed
        # lines" rule. The LLM is asked in diffAnalysisProcess.md to only
        # report issues caused by `+` lines; this drops anything outside the
        # function's actual changed-lines set (with a small neighborhood).
        changed_map = _build_changed_lines_map(work.prompt_data)
        issues, dropped = _filter_issues_to_changed_lines(issues, changed_map)
        if dropped:
            logger.info(
                f"diff filter: dropped {dropped} issue(s) outside changed lines "
                f"for {work.function_name}"
            )

        filtered = await self._apply_issue_filter(issues, work.prompt_data)
        outcome = await self.sink.publish(
            file_path=work.file_path,
            function=work.function_name,
            checksum=work.function_checksum,
            issues=filtered,
        )
        if not outcome.ok:
            counts.failed += 1
            await self.session.emit(
                FunctionFailedEvent(
                    pipeline="diff",
                    function_name=work.function_name,
                    file_path=work.file_path,
                    error=f"publish failed: {outcome.error}",
                    stage="publish",
                )
            )
            return

        counts.successful += 1
        await self.session.emit(
            FunctionCompleteEvent(
                pipeline="diff",
                function_name=work.function_name,
                file_path=work.file_path,
                issues=filtered,
                duration_seconds=time.monotonic() - t0,
                cached=False,
                index=idx,
                total=total,
            )
        )

    async def _republish_cached(
        self,
        cached: Dict[str, Any],
        work: DiffFunctionWork,
        *,
        idx: int,
        total: int,
        t0: float,
        counts: _MutableCounts,
    ) -> None:
        cached_issues_raw = cached.get("results", []) if isinstance(cached, dict) else []
        cached_issues = [i for i in cached_issues_raw if isinstance(i, dict)]
        filtered = await self._apply_issue_filter(cached_issues, None)
        outcome = await self.sink.publish(
            file_path=work.file_path,
            function=work.function_name,
            checksum=work.function_checksum,
            issues=filtered,
        )
        if not outcome.ok:
            counts.failed += 1
            await self.session.emit(
                FunctionFailedEvent(
                    pipeline="diff",
                    function_name=work.function_name,
                    file_path=work.file_path,
                    error=f"republish failed: {outcome.error}",
                    stage="publish_cached",
                )
            )
            return
        counts.cached += 1
        await self.session.emit(
            FunctionCompleteEvent(
                pipeline="diff",
                function_name=work.function_name,
                file_path=work.file_path,
                issues=filtered,
                duration_seconds=time.monotonic() - t0,
                cached=True,
                index=idx,
                total=total,
            )
        )

    # ------------------------------------------------------------------
    # Call-tree fan-out
    # ------------------------------------------------------------------

    async def _fan_out_call_tree(
        self,
        items: List[DiffCallTreeWork],
        counts: _MutableCounts,
    ) -> None:
        total = len(items)
        indexed = list(enumerate(items, 1))

        async def _do_one(item: Tuple[int, DiffCallTreeWork]) -> None:
            idx, work = item
            await self._process_call_tree_root(work, idx=idx, total=total, counts=counts)

        await bounded_gather(
            indexed,
            _do_one,
            max_concurrency=self.session.ctx.max_workers,
            rate_limiter=self.session.rate_limiter,
        )

    async def _process_call_tree_root(
        self,
        work: DiffCallTreeWork,
        *,
        idx: int,
        total: int,
        counts: _MutableCounts,
    ) -> None:
        await self.session.emit(
            FunctionStartEvent(
                pipeline="diff",
                function_name=work.root_name,
                file_path=work.root_file,
                index=idx,
                total=total,
            )
        )
        t0 = time.monotonic()

        try:
            issues = await self.analyze_call_tree(
                work.tree_dict, work.diff_context, work.root_name
            )
        except Exception as exc:  # noqa: BLE001 — per-root isolation
            logger.exception(f"diff call-tree raised for {work.root_name}: {exc}")
            issues = None

        if issues is None:
            counts.failed += 1
            await self.session.emit(
                FunctionFailedEvent(
                    pipeline="diff",
                    function_name=work.root_name,
                    file_path=work.root_file,
                    error="diff call-tree analysis produced no usable result",
                    stage="analyze_call_tree",
                )
            )
            return

        # Filter to changed lines using the file-level diff context attached
        # to the work item. Call-tree mode covers many files in one LLM run,
        # so we use the cross-file `changed_lines_per_file` map.
        changed_map = _build_changed_lines_map_from_diff_context(work.diff_context)
        issues, dropped = _filter_issues_to_changed_lines(issues, changed_map)
        if dropped:
            logger.info(
                f"diff filter: dropped {dropped} issue(s) outside changed lines "
                f"for call-tree root {work.root_name}"
            )

        groups = self._group_call_tree_issues(work, issues)
        await self._publish_call_tree_groups(groups, work)

        counts.successful += 1
        all_issues = [i for g in groups.values() for i in g["issues"]]
        await self.session.emit(
            FunctionCompleteEvent(
                pipeline="diff",
                function_name=work.root_name,
                file_path=work.root_file,
                issues=all_issues,
                duration_seconds=time.monotonic() - t0,
                cached=False,
                index=idx,
                total=total,
            )
        )

    async def _publish_call_tree_groups(
        self,
        groups: Dict[str, Dict[str, Any]],
        work: DiffCallTreeWork,
    ) -> None:
        for defect_fn, group in groups.items():
            filtered = await self._apply_issue_filter(group["issues"], None)
            outcome = await self.sink.publish(
                file_path=group["file"],
                function=defect_fn,
                checksum=group["checksum"],
                issues=filtered,
            )
            if not outcome.ok:
                logger.error(
                    f"diff call-tree publish failed for {defect_fn}: {outcome.error}"
                )
        if not groups:
            # Record an empty entry for the root so the cache says "analyzed".
            await self.sink.publish(
                file_path=work.root_file,
                function=work.root_name,
                checksum=work.root_checksum,
                issues=[],
            )

    # ------------------------------------------------------------------
    # Stage runners
    # ------------------------------------------------------------------

    async def _run_stage_da(
        self,
        prompt_data: Dict[str, Any],
        bundle_checksum: str,
    ) -> Optional[Dict[str, Any]]:
        """Stage Da — Diff Context Collection.

        Cached on disk at `{artifacts}/diff_context_bundles/{checksum[:8]}.json`,
        same shape as legacy `DiffAnalysis.run_diff_context_collection`.
        """
        bundle = await self._load_cached_bundle(bundle_checksum)
        if bundle is not None:
            return bundle

        try:
            system_prompt = await asyncio.to_thread(self._read_prompt, _DIFF_CONTEXT_PROMPT_FILE)
        except Exception as exc:  # noqa: BLE001
            logger.error(f"Could not load Stage Da prompt: {exc}")
            return None

        user_message = self._build_function_user_message(prompt_data)
        user_message += (
            "\n\nCollect all context needed for this function diff and return a "
            "JSON diff code collection as described in the system prompt."
        )

        runner = IterativeRunner(
            self.session.llm, conversation_logger=self.session.conversation_logger
        )
        ctx_info = prompt_data.get("function", "unknown")
        outcome = await runner.run(
            stage_da_diff_context(system_prompt),
            user_prompt=user_message,
            tools=self.session.tools,
            context_info=ctx_info,
            token_callback=self._token_usage_relay(),
        )

        if outcome.error or outcome.text is None:
            logger.warning(
                f"Stage Da failed (error={outcome.error}, iterations={outcome.iterations})"
            )
            return None

        bundle = self._parse_context_bundle(outcome.text)
        if bundle is None:
            return None
        await self._save_bundle(bundle, bundle_checksum)
        return bundle

    async def _run_stage_db(
        self,
        bundle: Dict[str, Any],
        function_name: str,
        file_path: str,
    ) -> Optional[List[Dict[str, Any]]]:
        """Stage Db — Diff Analysis from Context."""
        try:
            system_prompt = await asyncio.to_thread(self._read_prompt, _DIFF_ANALYSIS_PROMPT_FILE)
        except Exception as exc:  # noqa: BLE001
            logger.error(f"Could not load Stage Db prompt: {exc}")
            return None

        user_message_parts = [
            "## Diff Code for Analysis\n",
            f"**Function**: `{function_name}` in `{file_path}`\n",
            "The following diff code contains everything needed for your analysis.\n",
            "```json",
            json.dumps(bundle, indent=2, ensure_ascii=False),
            "```\n",
            "Analyze the changed lines (marked with +) and return a JSON array of issues.\n",
            "🔥 CRITICAL: Return ONLY a valid JSON array starting with [ and ending with ]. "
            "If no issues, return [].",
        ]
        user_message = "\n".join(user_message_parts)

        runner = IterativeRunner(
            self.session.llm, conversation_logger=self.session.conversation_logger
        )
        outcome = await runner.run(
            stage_db_diff_analysis(system_prompt),
            user_prompt=user_message,
            tools=self.session.tools,
            context_info=function_name,
            token_callback=self._token_usage_relay(),
        )

        if outcome.error or outcome.text is None:
            logger.warning(
                f"Stage Db failed (error={outcome.error}, iterations={outcome.iterations})"
            )
            return None
        return self._parse_issue_list(outcome.text)

    # ------------------------------------------------------------------
    # User-message construction (per-function Stage Da/Db)
    # ------------------------------------------------------------------

    @staticmethod
    def _build_function_user_message(prompt_data: Dict[str, Any]) -> str:
        """Render `prompt_data` into the Stage-Da user message.

        Preserves the legacy `DiffAnalysis._build_function_analysis_user_message`
        verbatim — same section ordering, same headings — so the prompts the
        model sees are byte-identical across the migration.
        """
        func_name = prompt_data.get("function", "unknown")
        file_path = prompt_data.get("file_path", "unknown")
        code = prompt_data.get("code", "")
        changed_lines = prompt_data.get("changed_lines", []) or []
        affected_reason = prompt_data.get("affected_reason", "modified")

        data_types_used = prompt_data.get("data_types_used", []) or []
        constants_used = prompt_data.get("constants_used", {}) or {}
        invoked_functions = prompt_data.get("invoked_functions", []) or []
        invoking_functions = prompt_data.get("invoking_functions", []) or []
        diff_context = prompt_data.get("diff_context", {}) or {}
        all_changed_files = diff_context.get("all_changed_files", []) or []

        parts: List[str] = []
        parts.append("## Function Being Analyzed\n")
        parts.append(f"**Function**: `{func_name}`")
        parts.append(f"**File**: `{file_path}`")
        parts.append(f"**Affected Reason**: {affected_reason}")
        if changed_lines:
            parts.append(f"**Changed Lines**: {', '.join(map(str, changed_lines))}")
        parts.append("")

        parts.append("### Function Code")
        parts.append("```")
        parts.append(code)
        parts.append("```")
        parts.append("")

        if data_types_used:
            parts.append("## Data Types Used")
            parts.append("The following data types are used by this function:")
            for dt in data_types_used:
                parts.append(f"- `{dt}`")
            parts.append("")

        if constants_used:
            parts.append("## Constants Used")
            parts.append("The following constants are used by this function:")
            for const_name, const_value in constants_used.items():
                parts.append(f"- `{const_name}`: {const_value}")
            parts.append("")

        if invoked_functions:
            parts.append("## Functions Called by This Function")
            parts.append(
                "**Note**: All invoked functions are shown. [MODIFIED] indicates the "
                "function was changed in this diff."
            )
            parts.append("")
            for func in invoked_functions:
                status = "[MODIFIED]" if func.get("is_modified", False) else "[UNCHANGED]"
                parts.append(
                    f"### {func.get('name', 'unknown')} "
                    f"({func.get('file', 'unknown')}:{func.get('start', '?')}-"
                    f"{func.get('end', '?')}) {status}"
                )
                if func.get("code"):
                    parts.append("```")
                    parts.append(func.get("code", ""))
                    parts.append("```")
                parts.append("")

        if invoking_functions:
            parts.append("## Functions That Call This Function")
            parts.append(
                "**Note**: All invoking functions are shown. [MODIFIED] indicates the "
                "function was changed in this diff."
            )
            parts.append("")
            for func in invoking_functions:
                status = "[MODIFIED]" if func.get("is_modified", False) else "[UNCHANGED]"
                parts.append(
                    f"### {func.get('name', 'unknown')} "
                    f"({func.get('file', 'unknown')}:{func.get('start', '?')}-"
                    f"{func.get('end', '?')}) {status}"
                )
                if func.get("code"):
                    parts.append("```")
                    parts.append(func.get("code", ""))
                    parts.append("```")
                parts.append("")

        if all_changed_files:
            parts.append("## Wider Change Context")
            parts.append(
                f"This function is part of a commit that modifies "
                f"{len(all_changed_files)} files:"
            )
            for f in all_changed_files:
                marker = "(this file)" if f == file_path else ""
                parts.append(f"- `{f}` {marker}")
            parts.append("")

        parts.append("## Analysis Instructions")
        parts.append("")
        parts.append("1. Focus on the changed lines (marked with + or -)")
        parts.append("2. Consider how changes affect the function's behavior")
        parts.append("3. Check if changes are consistent with related functions")
        parts.append("4. Report issues ONLY on changed lines when possible")
        parts.append("")
        parts.append(
            "🎯 **IMPORTANT**: When reporting line numbers, focus on the actually "
            "changed lines (lines with + prefix) to ensure your findings can be "
            "properly commented on in the pull request."
        )
        parts.append("")
        parts.append(
            "🔥 **CRITICAL JSON OUTPUT REMINDER**: Your final response MUST be a valid "
            "JSON array starting with `[` and ending with `]`. No markdown, no "
            "explanatory text - ONLY the JSON array."
        )
        return "\n".join(parts)

    # ------------------------------------------------------------------
    # Stage Da bundle disk cache
    # ------------------------------------------------------------------

    @staticmethod
    def _bundle_checksum(function_name: str, file_path: str) -> str:
        """Same scheme legacy `DiffAnalysis.run_diff_context_collection` used:
        md5 of `{function_name}@{file_path}`, then take the first 8 hex chars
        for the filename.
        """
        return hashlib.md5(f"{function_name}@{file_path}".encode("utf-8")).hexdigest()

    def _bundle_path(self, bundle_checksum: str) -> str:
        return os.path.join(
            self.session.ctx.diff_context_bundles_dir, f"{bundle_checksum[:8]}.json"
        )

    async def _load_cached_bundle(self, bundle_checksum: str) -> Optional[Dict[str, Any]]:
        path = self._bundle_path(bundle_checksum)
        if not await asyncio.to_thread(os.path.exists, path):
            return None
        try:
            raw = await asyncio.to_thread(self._read_text, path)
            data = json.loads(raw)
        except Exception as exc:  # noqa: BLE001 — soft-fail on bad cache
            logger.warning(f"Failed to read cached diff bundle at {path}: {exc}")
            return None
        if not isinstance(data, dict):
            logger.warning(f"Cached diff bundle at {path} is not a dict; deleting")
            await asyncio.to_thread(self._unlink_silent, path)
            return None
        return data

    async def _save_bundle(self, bundle: Dict[str, Any], bundle_checksum: str) -> None:
        path = self._bundle_path(bundle_checksum)
        try:
            await asyncio.to_thread(self._write_bundle_sync, path, bundle)
        except Exception as exc:  # noqa: BLE001 — cache write must not fail the run
            logger.warning(f"Could not save diff bundle to {path}: {exc}")

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

    @staticmethod
    def _read_prompt(filename: str) -> str:
        """Read a prompt .md file from `hindsight/core/prompts/`."""
        prompts_dir = Path(__file__).resolve().parent.parent / "core" / "prompts"
        return (prompts_dir / filename).read_text(encoding="utf-8")

    # ------------------------------------------------------------------
    # LLM-output parsing
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_context_bundle(text: str) -> Optional[Dict[str, Any]]:
        """Stage Da output should be a dict. Soft-parse with array unwrap."""
        try:
            value = json.loads(text)
        except json.JSONDecodeError as exc:
            logger.warning(f"Stage Da JSON decode failed: {exc}")
            return None
        if isinstance(value, list):
            candidate = next(
                (b for b in value if isinstance(b, dict) and (
                    "changed_functions" in b or "primary_function" in b
                )),
                None,
            )
            return candidate
        if not isinstance(value, dict):
            logger.warning(f"Stage Da returned unexpected type {type(value).__name__}")
            return None
        return value

    @staticmethod
    def _parse_issue_list(text: str) -> List[Dict[str, Any]]:
        """Parse a Stage Db / diff-call-tree result into a list of issue dicts."""
        try:
            value = json.loads(text)
        except json.JSONDecodeError as exc:
            logger.warning(f"Diff issue-list JSON decode failed: {exc}")
            return []
        if isinstance(value, list):
            return [i for i in value if isinstance(i, dict)]
        if isinstance(value, dict):
            if "results" in value and isinstance(value["results"], list):
                return [i for i in value["results"] if isinstance(i, dict)]
            return [value]
        return []

    # ------------------------------------------------------------------
    # Call-tree issue grouping (matches code analyzer's logic + legacy
    # `_publish_call_tree_issues` in git_simple_diff_analyzer)
    # ------------------------------------------------------------------

    @staticmethod
    def _group_call_tree_issues(
        work: DiffCallTreeWork,
        issues: List[Dict[str, Any]],
    ) -> Dict[str, Dict[str, Any]]:
        """Group issues by `defect_function`, re-pin out-of-tree to root.

        Returns `{defect_fn: {'file': ..., 'checksum': ..., 'issues': [...]}}`.
        """
        node_lookup: Dict[str, Tuple[str, str]] = {}
        for node in work.tree_dict.get("nodes", []) or []:
            name = node.get("function", "")
            if not name:
                continue
            node_lookup[name] = (
                node.get("file", "") or work.root_file,
                node.get("checksum", "") or work.root_checksum,
            )

        groups: Dict[str, Dict[str, Any]] = {}
        for raw in issues:
            if not isinstance(raw, dict):
                continue
            defect_fn = (
                raw.get("defect_function")
                or raw.get("function_name")
                or work.root_name
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

            if defect_fn not in node_lookup:
                defect_fn = work.root_name
            file_path, checksum = node_lookup.get(
                defect_fn, (work.root_file, work.root_checksum)
            )
            slot = groups.setdefault(
                defect_fn,
                {"file": file_path, "checksum": checksum, "issues": []},
            )
            slot["issues"].append(normalized)
        return groups

    # ------------------------------------------------------------------
    # Hooks shared with pipeline_code
    # ------------------------------------------------------------------

    async def _apply_issue_filter(
        self,
        issues: List[Dict[str, Any]],
        function_context: Optional[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        if not self.issue_filter or not issues:
            return issues
        context_str: Optional[str] = None
        if isinstance(function_context, dict):
            context_str = function_context.get("code")
        try:
            return await asyncio.to_thread(self.issue_filter, issues, context_str)
        except Exception as exc:  # noqa: BLE001 — never fail the run
            logger.warning(f"issue_filter raised; keeping unfiltered: {exc}")
            return issues

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

    async def _finalize_and_emit_completed(
        self,
        start: float,
        counts: _MutableCounts,
        selected: int,
    ) -> DiffRunSummary:
        summary = DiffRunSummary(
            selected=selected,
            successful=counts.successful,
            failed=counts.failed,
            cached=counts.cached,
            duration_seconds=time.monotonic() - start,
            error=None,
        )
        await self.session.emit(
            RunCompletedEvent(
                pipeline="diff",
                successful=summary.successful,
                failed=summary.failed,
                cached=summary.cached,
                duration_seconds=summary.duration_seconds,
            )
        )
        return summary

    async def _finalize_and_emit_failed(
        self,
        start: float,
        counts: _MutableCounts,
        exc: BaseException,
    ) -> DiffRunSummary:
        error_msg = f"{type(exc).__name__}: {exc}"
        logger.exception(f"DiffPipeline crashed: {error_msg}")
        summary = DiffRunSummary(
            successful=counts.successful,
            failed=counts.failed,
            cached=counts.cached,
            duration_seconds=time.monotonic() - start,
            error=error_msg,
        )
        await self.session.emit(
            RunFailedEvent(
                pipeline="diff",
                error=error_msg,
                successful=summary.successful,
                failed=summary.failed,
                cached=summary.cached,
                duration_seconds=summary.duration_seconds,
            )
        )
        return summary
