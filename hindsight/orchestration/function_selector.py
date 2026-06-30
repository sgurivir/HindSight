"""Pure functions for selecting which functions to analyze.

Replaces the `_should_analyze_function*` helpers on `CodeAnalysisRunner`.
Same precedence as the legacy code:

  function_filter (--function-filter JSON) > file_filter (--file-filter)
  > directory filters > length filter

Pure functions only — no I/O, no shared state. The session resolves
`--function-filter` / `--file-filter` JSON into the `FunctionFilters` dataclass
once, then passes that to `select_functions(call_graph_data, filters)`.

Length filtering deliberately stays here (not in the AST layer): a function
must clear `min_function_body_length` AND not exceed `max_function_body_length`
before it counts as analyzable. The legacy code applies these in
`_should_analyze_function` and we preserve that.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, FrozenSet, Iterable, List, Optional, Tuple

from hindsight.core.constants import MAX_FUNCTION_BODY_LENGTH, MIN_FUNCTION_BODY_LENGTH
from hindsight.utils.filtered_file_finder import FilteredFileFinder
from hindsight.utils.log_util import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class FunctionFilters:
    """All the inputs `select_functions` needs to make a yes/no decision.

    Built once per session from the resolved CLI flags + config JSON.
    """

    file_filter: Tuple[str, ...] = ()
    include_directories: Tuple[str, ...] = ()
    exclude_directories: Tuple[str, ...] = ()
    exclude_files: Tuple[str, ...] = ()
    verified_functions: FrozenSet[str] = frozenset()    # from --function-filter JSON
    filtered_functions: FrozenSet[str] = frozenset()    # functions in --file-filter files
    filtered_classes: FrozenSet[str] = frozenset()      # classes in --file-filter files
    min_function_body_length: int = MIN_FUNCTION_BODY_LENGTH
    max_function_body_length: int = MAX_FUNCTION_BODY_LENGTH


@dataclass(frozen=True)
class FunctionWorkItem:
    """One unit of work for the per-function pipeline.

    The pipeline pulls `func_entry` (the call-graph function record) plus the
    enclosing `file_entry` (which gives the file path even when the function
    record's context lacks one). `line_count` lets the pipeline sort by size
    so longer functions go first (matches legacy behavior).
    """

    func_entry: Dict[str, Any]
    file_entry: Dict[str, Any]
    line_count: int

    @property
    def function_name(self) -> str:
        return str(self.func_entry.get("function", "unknown"))

    @property
    def primary_file(self) -> str:
        return str(self.func_entry.get("context", {}).get("file", "") or self.file_entry.get("file", ""))


# ----------------------------------------------------------------------
# Public API
# ----------------------------------------------------------------------


def select_functions(
    call_graph_data: List[Dict[str, Any]],
    filters: FunctionFilters,
) -> List[FunctionWorkItem]:
    """Return the functions to analyze, sorted longest-first.

    Iterates the call-graph file entries, populates `context.file` if missing
    (some old AST artifacts omit it), applies the filtering precedence, and
    sorts the survivors by line count descending.

    Tolerant of malformed entries — anything that isn't shaped as a dict is
    silently skipped rather than raised. The point is to make best-effort
    progress when the AST artifacts are imperfect; logging records the skips.
    """
    if not isinstance(call_graph_data, list):
        logger.warning(
            f"select_functions: call_graph_data is not a list ({type(call_graph_data).__name__})"
        )
        return []

    work: List[FunctionWorkItem] = []
    skipped_malformed = 0
    skipped_filtered = 0
    skipped_length = 0

    for file_entry in call_graph_data:
        if not isinstance(file_entry, dict):
            skipped_malformed += 1
            continue
        file_path = file_entry.get("file", "")
        for func_entry in file_entry.get("functions", []) or []:
            if not isinstance(func_entry, dict):
                skipped_malformed += 1
                continue
            # Backfill the file context if missing — some AST artifacts elide it.
            if "context" not in func_entry or not isinstance(func_entry["context"], dict):
                func_entry["context"] = {}
            if not func_entry["context"].get("file"):
                func_entry["context"]["file"] = file_path

            if not _should_analyze(func_entry, filters):
                skipped_filtered += 1
                continue

            length = get_function_line_count(func_entry)
            if not _passes_length_filter(length, filters, func_entry):
                skipped_length += 1
                continue

            work.append(FunctionWorkItem(func_entry=func_entry, file_entry=file_entry, line_count=length))

    work.sort(key=lambda w: w.line_count, reverse=True)
    logger.info(
        f"select_functions: {len(work)} selected (skipped: {skipped_malformed} malformed, "
        f"{skipped_filtered} filtered, {skipped_length} length)"
    )
    return work


def get_function_line_count(func_entry: Dict[str, Any]) -> int:
    """Best-effort line count for a function record.

    Tries the same fallback chain as the legacy
    `CodeAnalysisRunner._get_function_line_count`. Returns 0 when nothing
    matches; the caller decides whether that fails the length filter.
    """
    try:
        for key in ("line_count", "lines", "num_lines"):
            v = func_entry.get(key)
            if isinstance(v, int) and v > 0:
                return v
        for nested in ("context", "function"):
            inner = func_entry.get(nested)
            if isinstance(inner, dict):
                for key in ("line_count", "lines", "num_lines"):
                    v = inner.get(key)
                    if isinstance(v, int) and v > 0:
                        return v
                start = inner.get("start_line") or inner.get("startLine") or inner.get("start")
                end = inner.get("end_line") or inner.get("endLine") or inner.get("end")
                if isinstance(start, int) and isinstance(end, int) and end >= start:
                    return end - start + 1
        # Top-level start/end as a last resort.
        start = func_entry.get("start_line") or func_entry.get("start")
        end = func_entry.get("end_line") or func_entry.get("end")
        if isinstance(start, int) and isinstance(end, int) and end >= start:
            return end - start + 1
    except Exception as exc:  # noqa: BLE001 — best-effort
        logger.debug(f"get_function_line_count fallback: {exc}")
    return 0


# ----------------------------------------------------------------------
# Internals
# ----------------------------------------------------------------------


def _should_analyze(func_entry: Dict[str, Any], filters: FunctionFilters) -> bool:
    """Apply the precedence: verified > file_filter > directory filters."""
    if filters.verified_functions:
        return _matches_verified(func_entry, filters.verified_functions)
    if filters.file_filter:
        return _matches_file_filter(func_entry, filters)
    return _matches_directory_filters(func_entry, filters)


def _matches_verified(func_entry: Dict[str, Any], verified: FrozenSet[str]) -> bool:
    name = (
        func_entry.get("name")
        or func_entry.get("function_name")
        or func_entry.get("function")
    )
    return bool(name) and name in verified


def _matches_file_filter(func_entry: Dict[str, Any], filters: FunctionFilters) -> bool:
    """Match by function/class name in the filtered lists, else by file path."""
    if filters.filtered_functions or filters.filtered_classes:
        name = (
            func_entry.get("name")
            or func_entry.get("function_name")
            or func_entry.get("function")
        )
        class_name = (
            func_entry.get("class_name")
            or func_entry.get("className")
            or func_entry.get("data_type_name")
        )
        if name and name in filters.filtered_functions:
            return True
        if class_name and class_name in filters.filtered_classes:
            return True
        # Fall through to file-path matching when name-based matching failed.

    file_path = _extract_file_path(func_entry)
    if not file_path:
        # Can't determine file → conservative include (legacy behavior).
        return True
    normalized = file_path.lstrip("./")
    for needle in filters.file_filter:
        n = needle.lstrip("./")
        if normalized == n or normalized.endswith("/" + n):
            return True
    return False


def _matches_directory_filters(func_entry: Dict[str, Any], filters: FunctionFilters) -> bool:
    """Apply include/exclude directory + exclude file filters."""
    file_path = _extract_file_path(func_entry)
    if not file_path:
        return True
    return FilteredFileFinder.should_analyze_by_directory_filters(
        file_path.lstrip("./"),
        list(filters.include_directories),
        list(filters.exclude_directories),
        list(filters.exclude_files),
    )


def _passes_length_filter(
    line_count: int,
    filters: FunctionFilters,
    func_entry: Dict[str, Any],
) -> bool:
    """Functions outside [min, max] are skipped. A zero count is treated as
    "unknown" and passes — matches legacy behavior of not gating on it.
    """
    if line_count <= 0:
        return True
    if line_count < filters.min_function_body_length:
        return False
    if line_count > filters.max_function_body_length:
        return False
    return True


def _extract_file_path(func_entry: Dict[str, Any]) -> str:
    """Same fallback chain the legacy `_extract_file_path_from_json` used."""
    file_path = func_entry.get("file")
    if isinstance(func_entry.get("context"), dict):
        file_path = file_path or func_entry["context"].get("file")
    if isinstance(func_entry.get("fileContext"), dict):
        file_path = file_path or func_entry["fileContext"].get("file")
    if not file_path and isinstance(func_entry.get("function"), dict):
        nested = func_entry["function"]
        if isinstance(nested.get("context"), dict):
            file_path = nested["context"].get("file")
        file_path = file_path or nested.get("file")
    if not file_path and isinstance(func_entry.get("invoking"), list) and func_entry["invoking"]:
        first = func_entry["invoking"][0]
        if isinstance(first, dict) and isinstance(first.get("context"), dict):
            file_path = first["context"].get("file")
    return str(file_path or "")
