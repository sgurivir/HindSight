"""Stage specifications for the iterative LLM runner.

Every LLM stage in the system (Stage 4a, 4b, Da, Db, Ta, Tb, Tc, perf A/B,
trivial filter, response challenger) is described by a single `StageSpec`:

    StageSpec(
        name="context_collection",          # for logging / dir naming
        system_prompt=...,                  # caller assembles via PromptBuilder
        max_iterations=20,
        supported_tools=frozenset({...}),
        extract_json=callable[str -> Optional[str]],
        validate_json=callable[Any -> bool],
        fallback_guidance=callable[Optional[str] -> str],
    )

The 11 factory functions in this module return frozen `StageSpec`s whose
extract/validate/fallback callables match the legacy iterative analyzers
verbatim. Schemas, examples, normalization quirks (e.g. `is_trivial` ->
`result`) — all preserved.

Pipelines assemble the spec with the stage-specific system prompt at call
time, then pass it to `IterativeRunner.run()`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, FrozenSet, Optional

from hindsight.core.constants import MAX_TOOL_ITERATIONS
from hindsight.utils.log_util import get_logger

from .json_extract import find_all_json_arrays, find_all_json_objects

logger = get_logger(__name__)


ExtractFn = Callable[[str], Optional[str]]
ValidateFn = Callable[[Any], bool]
FallbackFn = Callable[[Optional[str]], str]


@dataclass(frozen=True)
class StageSpec:
    """Describes one LLM stage to the iterative runner.

    `supported_tools` is a frozenset of tool names; when empty, the runner
    treats the stage as tools-disabled and won't even look for tool requests.

    `max_iterations` defaults to `MAX_TOOL_ITERATIONS` (20) when unset.
    """

    name: str
    system_prompt: str
    extract_json: ExtractFn
    validate_json: ValidateFn
    fallback_guidance: FallbackFn
    supported_tools: FrozenSet[str] = field(default_factory=frozenset)
    max_iterations: int = MAX_TOOL_ITERATIONS


# Common tool sets used across stages — keep these in sync with the prompts.
FULL_CONTEXT_TOOLS = frozenset({
    "readFile",
    "runTerminalCmd",
    "getSummaryOfFile",
    "inspectDirectoryHierarchy",
    "list_files",
    "getFileContentByLines",
    "getFileContent",
    "checkFileSize",
    "lookup_knowledge",
    "store_knowledge",
})

ANALYSIS_TOOLS = frozenset({
    "readFile",
    "runTerminalCmd",
    "getFileContentByLines",
    "getFileContent",
    "checkFileSize",
    "list_files",
    "lookup_knowledge",
    "store_knowledge",
})

CALL_TREE_TOOLS = frozenset({
    "readFile",
    "getFileContentByLines",
    "getFileContent",
    "checkFileSize",
    "getSummaryOfFile",
    "list_files",
    "inspectDirectoryHierarchy",
    "runTerminalCmd",
    "lookup_knowledge",
    "store_knowledge",
})


# ======================================================================
# Stage 4a — Code analysis context collection (dict with 'primary_function')
# ======================================================================


def stage_4a_context_collection(system_prompt: str, *, max_iterations: int = MAX_TOOL_ITERATIONS) -> StageSpec:
    """Context collection for per-function code analysis."""
    return StageSpec(
        name="context_collection",
        system_prompt=system_prompt,
        extract_json=_extract_dict_with_key("primary_function", "ContextCollectionAnalyzer"),
        validate_json=_validate_dict_with_key("primary_function"),
        fallback_guidance=_fallback_primary_function,
        supported_tools=FULL_CONTEXT_TOOLS,
        max_iterations=max_iterations,
    )


def _fallback_primary_function(reason: Optional[str]) -> str:
    reason_block = f"Why your previous response was rejected: {reason}.\n\n" if reason else ""
    return (
        "CRITICAL: Your previous response did not contain a valid context bundle.\n\n"
        f"{reason_block}"
        "You MUST respond with ONLY a valid JSON OBJECT containing a `primary_function` "
        "key (a dict describing the function under analysis).\n\n"
        "### Required schema\n"
        "```json\n"
        "{\n"
        '  "primary_function": { "name": "string", "file_path": "string", "source": "string", '
        '"start_line": 0, "end_line": 0 },\n'
        '  "callees": [], "callers": [], "data_types": [], "constants_and_globals": [],\n'
        '  "collection_notes": []\n'
        "}\n"
        "```\n\n"
        "Your response MUST start with `{` and end with `}`. Return JSON ONLY — no "
        "markdown fences, no arrays, no prose."
    )


# ======================================================================
# Stage 4b — Code analysis from context (array of issue dicts)
# ======================================================================


def stage_4b_analysis(system_prompt: str, *, max_iterations: int = MAX_TOOL_ITERATIONS) -> StageSpec:
    """Analysis pass that consumes the Stage 4a context bundle."""
    return StageSpec(
        name="analysis",
        system_prompt=system_prompt,
        extract_json=_extract_issue_array("CodeAnalysisAnalyzer"),
        validate_json=_validate_issue_array,
        fallback_guidance=_fallback_code_issues,
        supported_tools=ANALYSIS_TOOLS,
        max_iterations=max_iterations,
    )


def _fallback_code_issues(reason: Optional[str]) -> str:
    reason_block = f"Why your previous response was rejected: {reason}.\n\n" if reason else ""
    return (
        "CRITICAL: Your previous response did not contain a valid issues array.\n\n"
        f"{reason_block}"
        "You MUST respond with ONLY a valid JSON ARRAY of issue objects. "
        "Each item must be a JSON OBJECT (dict), not a string. "
        "If no issues are found, return exactly `[]` — an empty array is a VALID answer.\n\n"
        "### Required schema (each item)\n"
        "```json\n"
        "{\n"
        '  "file_path": "string", "file_name": "string", "function_name": "string",\n'
        '  "line_number": "string", "severity": "string — high | medium | low",\n'
        '  "issue": "string", "description": "string", "suggestion": "string",\n'
        '  "category": "string", "issueType": "string"\n'
        "}\n"
        "```\n\n"
        "### CORRECT example (one issue)\n"
        "```json\n"
        '[{"file_path": "src/Cache.swift", "file_name": "Cache.swift", '
        '"function_name": "Cache.evict", "line_number": "82", "severity": "high", '
        '"issue": "Use-after-free on evicted entry", '
        '"description": "evict() returns the entry then deallocates it; the caller dereferences the returned pointer.", '
        '"suggestion": "Return a copy or extend the lifetime via a strong reference until the caller is done.", '
        '"category": "memory", "issueType": "memory"}]\n'
        "```\n\n"
        "### CORRECT example (no issues found)\n"
        "```json\n[]\n```\n\n"
        "Your response MUST start with `[` and end with `]`. Return JSON ONLY."
    )


# ======================================================================
# Stage Da — Diff context collection (dict with 'changed_functions' or 'primary_function')
# ======================================================================


def stage_da_diff_context(system_prompt: str, *, max_iterations: int = 20) -> StageSpec:
    """Diff-mode context collection. Accepts either `changed_functions` or
    `primary_function` keys (legacy `DiffContextAnalyzer` accepts both shapes).
    """
    return StageSpec(
        name="diff_context_collection",
        system_prompt=system_prompt,
        extract_json=_extract_diff_context,
        validate_json=_validate_diff_context,
        fallback_guidance=_fallback_diff_context,
        supported_tools=FULL_CONTEXT_TOOLS,
        max_iterations=max_iterations,
    )


def _extract_diff_context(content: str) -> Optional[str]:
    """Mirrors `DiffContextAnalyzer.extract_json` — try changed_functions,
    then primary_function, then any dict, then array-wrapped variants.
    """
    candidates = find_all_json_objects(content)

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict) and "changed_functions" in parsed:
                logger.info("[diff_context] Found bundle with 'changed_functions'")
                return candidate
        except json.JSONDecodeError:
            continue

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict) and "primary_function" in parsed:
                logger.info("[diff_context] Found bundle with 'primary_function'")
                return candidate
        except json.JSONDecodeError:
            continue

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                logger.warning("[diff_context] Falling back to first dict without expected keys")
                return candidate
        except json.JSONDecodeError:
            continue

    for arr in find_all_json_arrays(content):
        try:
            parsed = json.loads(arr)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, list):
            for item in parsed:
                if isinstance(item, dict) and "changed_functions" in item:
                    logger.warning("[diff_context] Bundle wrapped in array — unwrapping")
                    return json.dumps(item)
    return None


def _validate_diff_context(parsed: Any) -> bool:
    if not isinstance(parsed, dict):
        return False
    return "changed_functions" in parsed or "primary_function" in parsed


def _fallback_diff_context(reason: Optional[str]) -> str:
    reason_block = f"Why your previous response was rejected: {reason}.\n\n" if reason else ""
    return (
        "CRITICAL: Your previous response did not contain a valid diff context bundle.\n\n"
        f"{reason_block}"
        "You MUST respond with ONLY a valid JSON OBJECT. The bundle MUST have a top-level "
        "`primary_function` key wrapping the diff's primary function metadata.\n\n"
        "### Required schema\n"
        "```json\n"
        "{\n"
        '  "schema_version": "1.0",\n'
        '  "primary_function": { "function_name": "string", "file_path": "string", '
        '"start_line": 0, "end_line": 0, "source": "string with +/-/space markers", '
        '"changed_lines": [{"line": 0, "marker": "+", "code": "string"}], "is_modified": true },\n'
        '  "callees": [], "callers": [], "data_types": [], "constants_and_globals": [],\n'
        '  "diff_context": { "total_lines_added": 0, "total_lines_removed": 0, '
        '"files_changed_in_diff": [] },\n'
        '  "collection_notes": []\n'
        "}\n"
        "```\n\n"
        "Your response MUST start with `{` and end with `}`. Return JSON ONLY."
    )


# ======================================================================
# Stage Db — Diff analysis (array of issue dicts)
# ======================================================================


def stage_db_diff_analysis(system_prompt: str, *, max_iterations: int = MAX_TOOL_ITERATIONS) -> StageSpec:
    """Diff-mode analysis pass."""
    return StageSpec(
        name="diff_analysis",
        system_prompt=system_prompt,
        extract_json=_extract_issue_array("DiffAnalysisAnalyzer"),
        validate_json=_validate_issue_array,
        fallback_guidance=_fallback_diff_issues,
        supported_tools=ANALYSIS_TOOLS,
        max_iterations=max_iterations,
    )


def _fallback_diff_issues(reason: Optional[str]) -> str:
    reason_block = f"Why your previous response was rejected: {reason}.\n\n" if reason else ""
    return (
        "CRITICAL: Your previous response did not contain a valid diff issues array.\n\n"
        f"{reason_block}"
        "You MUST respond with ONLY a valid JSON ARRAY of issue objects (dicts). "
        "Focus on issues in CHANGED lines (marked `+` in the diff). "
        "If no issues are found in the changed lines, return exactly `[]`.\n\n"
        "### Required schema (each item)\n"
        "```json\n"
        "{\n"
        '  "file_path": "string", "file_name": "string", "function_name": "string",\n'
        '  "line_number": "string (must reference a changed/`+` line)",\n'
        '  "severity": "string — high | medium | low",\n'
        '  "issue": "string", "description": "string", "suggestion": "string",\n'
        '  "category": "string", "issueType": "string"\n'
        "}\n"
        "```\n\n"
        "Your response MUST start with `[` and end with `]`. Return JSON ONLY."
    )


# ======================================================================
# Stage Ta — Trace context collection (dict with 'call_path')
# ======================================================================


def stage_ta_trace_context(system_prompt: str, *, max_iterations: int = MAX_TOOL_ITERATIONS) -> StageSpec:
    return StageSpec(
        name="trace_context_collection",
        system_prompt=system_prompt,
        extract_json=_extract_dict_with_key("call_path", "TraceContextAnalyzer"),
        validate_json=_validate_dict_with_key("call_path"),
        fallback_guidance=_fallback_trace_context,
        supported_tools=FULL_CONTEXT_TOOLS,
        max_iterations=max_iterations,
    )


def _fallback_trace_context(reason: Optional[str]) -> str:
    reason_block = f"Why your previous response was rejected: {reason}.\n\n" if reason else ""
    return (
        "CRITICAL: Your previous response did not contain a valid trace context bundle.\n\n"
        f"{reason_block}"
        "You MUST respond with ONLY a valid JSON OBJECT containing a `call_path` key — "
        "a list of function-name strings ordered top-most caller → leaf.\n\n"
        "### Required schema\n"
        '```json\n{ "call_path": ["string", "string", "string"] }\n```\n\n'
        "Your response MUST start with `{` and end with `}`. Return JSON ONLY."
    )


# ======================================================================
# Stage Tb — Trace analysis (array of issue dicts)
# ======================================================================


def stage_tb_trace_analysis(system_prompt: str, *, max_iterations: int = MAX_TOOL_ITERATIONS) -> StageSpec:
    return StageSpec(
        name="trace_analysis",
        system_prompt=system_prompt,
        extract_json=_extract_issue_array_with_named_keys("TraceAnalysisAnalyzer"),
        validate_json=_validate_issue_array,
        fallback_guidance=_fallback_trace_issues,
        supported_tools=ANALYSIS_TOOLS,
        max_iterations=max_iterations,
    )


def _fallback_trace_issues(reason: Optional[str]) -> str:
    reason_block = f"Why your previous response was rejected: {reason}.\n\n" if reason else ""
    return (
        "CRITICAL: Your previous response did not contain a valid trace issues array.\n\n"
        f"{reason_block}"
        "You MUST respond with ONLY a valid JSON ARRAY of trace-issue objects (dicts). "
        "If no issues are found, return exactly `[]`.\n\n"
        "### Required schema (each item)\n"
        "```json\n"
        "{\n"
        '  "function_name": "string", "file_path": "string", "line_number": "string",\n'
        '  "severity": "string — high | medium | low",\n'
        '  "issue": "string", "description": "string", "suggestion": "string",\n'
        '  "category": "string", "issueType": "string"\n'
        "}\n"
        "```\n\n"
        "Your response MUST start with `[` and end with `]`. Return JSON ONLY."
    )


# ======================================================================
# Stage Tc — Trace solution validator (dict with 'valid' bool)
# ======================================================================


def stage_tc_trace_validator(system_prompt: str, *, max_iterations: int = MAX_TOOL_ITERATIONS) -> StageSpec:
    return StageSpec(
        name="trace_solution_validator",
        system_prompt=system_prompt,
        extract_json=_extract_validator_verdict,
        validate_json=_validate_validator_verdict,
        fallback_guidance=_fallback_validator,
        supported_tools=ANALYSIS_TOOLS,
        max_iterations=max_iterations,
    )


def _extract_validator_verdict(content: str) -> Optional[str]:
    for candidate in find_all_json_objects(content):
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if not isinstance(parsed, dict) or "tool" in parsed:
            continue
        if "valid" in parsed and isinstance(parsed.get("valid"), bool):
            logger.info(
                f"[trace_validator] verdict valid={parsed['valid']} "
                f"low_confidence={parsed.get('low_confidence', False)}"
            )
            return candidate
    return None


def _validate_validator_verdict(parsed: Any) -> bool:
    return isinstance(parsed, dict) and isinstance(parsed.get("valid"), bool)


def _fallback_validator(reason: Optional[str]) -> str:
    reason_block = f"Why your previous response was rejected: {reason}.\n\n" if reason else ""
    return (
        "CRITICAL: Your previous response did not contain a valid verdict.\n\n"
        f"{reason_block}"
        "You MUST respond with ONLY a valid JSON OBJECT containing at minimum a boolean "
        "`valid`. Include `low_confidence: true` if you lack context to judge, plus a "
        "string `reason`.\n\n"
        "### Required schema\n"
        "```json\n"
        '{ "valid": true, "low_confidence": false, "reason": "string" }\n'
        "```\n\n"
        "Your response MUST start with `{` and end with `}`. Return JSON ONLY."
    )


# ======================================================================
# Stage Perf-A — Perf context (dict with 'call_path' or 'functions')
# ======================================================================


def stage_perf_context(system_prompt: str, *, max_iterations: int = MAX_TOOL_ITERATIONS) -> StageSpec:
    return StageSpec(
        name="perf_context_collection",
        system_prompt=system_prompt,
        extract_json=_extract_perf_context,
        validate_json=_validate_perf_context,
        fallback_guidance=_fallback_perf_context,
        supported_tools=FULL_CONTEXT_TOOLS,
        max_iterations=max_iterations,
    )


def _extract_perf_context(content: str) -> Optional[str]:
    for candidate in find_all_json_objects(content):
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict) and ("call_path" in parsed or "functions" in parsed):
            return candidate
    for arr in find_all_json_arrays(content):
        try:
            parsed = json.loads(arr)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, list):
            for item in parsed:
                if isinstance(item, dict) and ("call_path" in item or "functions" in item):
                    return json.dumps(item)
    return None


def _validate_perf_context(parsed: Any) -> bool:
    if not isinstance(parsed, dict):
        return False
    return "functions" in parsed or "call_path" in parsed


def _fallback_perf_context(reason: Optional[str]) -> str:
    reason_block = f"Why your previous response was rejected: {reason}.\n\n" if reason else ""
    return (
        "CRITICAL: Your previous response did not contain a valid performance context bundle.\n\n"
        f"{reason_block}"
        "You MUST respond with ONLY a valid JSON OBJECT including `call_path` (list of "
        "function names) AND `functions` (map keyed by name).\n\n"
        "### Required schema\n"
        "```json\n"
        "{\n"
        '  "call_path": ["FuncA", "FuncB"],\n'
        '  "functions": { "FuncA": { "body": "string", "file": "string", "line": 0 } }\n'
        "}\n"
        "```\n\n"
        "Your response MUST start with `{` and end with `}`. Return JSON ONLY."
    )


# ======================================================================
# Stage Perf-B — Perf analysis (array of issue dicts)
# ======================================================================


def stage_perf_analysis(system_prompt: str, *, max_iterations: int = MAX_TOOL_ITERATIONS) -> StageSpec:
    return StageSpec(
        name="perf_analysis",
        system_prompt=system_prompt,
        extract_json=_extract_issue_array_with_named_keys("PerfAnalysisAnalyzer"),
        validate_json=_validate_issue_array,
        fallback_guidance=_fallback_perf_issues,
        supported_tools=ANALYSIS_TOOLS,
        max_iterations=max_iterations,
    )


def _fallback_perf_issues(reason: Optional[str]) -> str:
    reason_block = f"Why your previous response was rejected: {reason}.\n\n" if reason else ""
    return (
        "CRITICAL: Your previous response did not contain a valid performance issues array.\n\n"
        f"{reason_block}"
        "You MUST respond with ONLY a valid JSON ARRAY of performance-issue objects (dicts). "
        "If no performance issues are found, return exactly `[]`.\n\n"
        "### Required schema (each item)\n"
        "```json\n"
        "{\n"
        '  "file_path": "string", "function_name": "string", "line_number": "string",\n'
        '  "severity": "string — high | medium | low",\n'
        '  "issue": "string", "description": "string", "suggestion": "string",\n'
        '  "category": "string — e.g. allocation | io | sync | loop | cache",\n'
        '  "issueType": "string"\n'
        "}\n"
        "```\n\n"
        "Your response MUST start with `[` and end with `]`. Return JSON ONLY."
    )


# ======================================================================
# Trivial filter (Level 2): dict with 'result' bool
# Response challenger (Level 3): dict with 'result' bool + 'reason'
# ======================================================================


def stage_trivial_filter(system_prompt: str, *, max_iterations: int = MAX_TOOL_ITERATIONS) -> StageSpec:
    return StageSpec(
        name="trivial_filter",
        system_prompt=system_prompt,
        extract_json=_extract_bool_verdict(("trivial",), "TrivialFilterAnalyzer"),
        validate_json=_validate_bool_verdict,
        fallback_guidance=_fallback_trivial,
        supported_tools=frozenset(),  # this stage uses no tools
        max_iterations=max_iterations,
    )


def stage_response_challenger(system_prompt: str, *, max_iterations: int = MAX_TOOL_ITERATIONS) -> StageSpec:
    return StageSpec(
        name="response_challenger",
        system_prompt=system_prompt,
        extract_json=_extract_bool_verdict(("should_filter",), "ResponseChallengerAnalyzer"),
        validate_json=_validate_bool_verdict,
        fallback_guidance=_fallback_response_challenger,
        supported_tools=ANALYSIS_TOOLS,
        max_iterations=max_iterations,
    )


def _fallback_trivial(reason: Optional[str]) -> str:
    reason_block = f"Why your previous response was rejected: {reason}.\n\n" if reason else ""
    return (
        "CRITICAL: Your previous response did not contain a valid verdict.\n\n"
        f"{reason_block}"
        "Respond with ONLY a JSON OBJECT containing a boolean `result`. "
        "`true` → issue is TRIVIAL (filter out); `false` → keep it.\n\n"
        '```json\n{ "result": true }\n```\n\n'
        "Your response MUST start with `{` and end with `}`. Return JSON ONLY."
    )


def _fallback_response_challenger(reason: Optional[str]) -> str:
    reason_block = f"Why your previous response was rejected: {reason}.\n\n" if reason else ""
    return (
        "CRITICAL: Your previous response did not contain a valid verdict.\n\n"
        f"{reason_block}"
        "Respond with ONLY a JSON OBJECT with a boolean `result` and a string `reason`. "
        "`result: true` → filter out (false positive). `result: false` → keep the issue.\n\n"
        '```json\n{ "result": true, "reason": "string — brief justification" }\n```\n\n'
        "Your response MUST start with `{` and end with `}`. Return JSON ONLY."
    )


# ======================================================================
# File summary (dict with 'summary' key)
# ======================================================================


def stage_file_summary(system_prompt: str, *, max_iterations: int = MAX_TOOL_ITERATIONS) -> StageSpec:
    """File / directory summary stage used by `FileOrDirectorySummaryGenerator`.

    Returns `{"summary": "<2-3 line description>"}`. Uses the full context
    toolset so the model can read files, list directories, etc., before
    answering.
    """
    return StageSpec(
        name="file_summary",
        system_prompt=system_prompt,
        extract_json=_extract_dict_with_key("summary", "FileSummary"),
        validate_json=_validate_dict_with_key("summary"),
        fallback_guidance=_fallback_file_summary,
        supported_tools=FULL_CONTEXT_TOOLS,
        max_iterations=max_iterations,
    )


def _fallback_file_summary(reason: Optional[str]) -> str:
    reason_block = f"Why your previous response was rejected: {reason}.\n\n" if reason else ""
    return (
        "CRITICAL: Your previous response did not contain a valid summary.\n\n"
        f"{reason_block}"
        "You MUST respond with ONLY a JSON OBJECT containing a `summary` key whose "
        "value is a 2-3 line string describing what the file does.\n\n"
        '```json\n{ "summary": "string — 2-3 line description" }\n```\n\n'
        "Your response MUST start with `{` and end with `}`. Return JSON ONLY — no "
        "markdown fences, no prose, no tool calls."
    )


# ======================================================================
# Call-tree-at-once (whole subtree in prompt; produces issues array)
# ======================================================================


def stage_call_tree_code(system_prompt: str, *, max_iterations: int = MAX_TOOL_ITERATIONS) -> StageSpec:
    """One-shot call-tree analysis stage for the code analyzer."""
    return StageSpec(
        name="call_tree_analysis",
        system_prompt=system_prompt,
        extract_json=_extract_issue_array("CallTreeAnalyzer"),
        validate_json=_validate_issue_array,
        fallback_guidance=_fallback_code_issues,
        supported_tools=CALL_TREE_TOOLS,
        max_iterations=max_iterations,
    )


def stage_call_tree_diff(system_prompt: str, *, max_iterations: int = MAX_TOOL_ITERATIONS) -> StageSpec:
    """One-shot call-tree analysis stage for the diff analyzer."""
    return StageSpec(
        name="diff_call_tree_analysis",
        system_prompt=system_prompt,
        extract_json=_extract_issue_array("DiffCallTreeAnalyzer"),
        validate_json=_validate_issue_array,
        fallback_guidance=_fallback_diff_issues,
        supported_tools=CALL_TREE_TOOLS,
        max_iterations=max_iterations,
    )


def stage_call_tree_context(system_prompt: str, *, max_iterations: int = MAX_TOOL_ITERATIONS) -> StageSpec:
    """Context-collection stage that precedes call-tree analysis (Step 1).

    The model walks the deterministically-built call tree, retrieves prior
    learnings via `lookup_knowledge`, resolves what the tree omits (data-type
    definitions, constant values, stubbed node bodies, function contracts) with
    the read tools, and records durable, general knowledge via `store_knowledge`.

    Output is a dict with an `additional_context` key — a plain-English
    description of what was gathered. The analysis stage (Step 2) receives
    this prose appended to its prompt under the line "Additional content which
    you may find useful for analysis", on top of the unchanged tree. The stage
    is tool-heavy by design (that is the whole point: warm the knowledge store
    and gather the definitions the tree can't carry), so it uses the full
    context toolset.
    """
    return StageSpec(
        name="call_tree_context_collection",
        system_prompt=system_prompt,
        extract_json=_extract_dict_with_key("additional_context", "CallTreeContextAnalyzer"),
        validate_json=_validate_dict_with_key("additional_context"),
        fallback_guidance=_fallback_call_tree_context,
        supported_tools=FULL_CONTEXT_TOOLS,
        max_iterations=max_iterations,
    )


def _fallback_call_tree_context(reason: Optional[str]) -> str:
    reason_block = f"Why your previous response was rejected: {reason}.\n\n" if reason else ""
    return (
        "CRITICAL: Your previous response did not contain a valid context summary.\n\n"
        f"{reason_block}"
        "You MUST respond with ONLY a valid JSON OBJECT containing an `additional_context` key "
        "whose value is an ENGLISH prose description (a few short paragraphs) of the reusable "
        "facts you gathered: data-type definitions, values/meaning of key constants, behavior "
        "of any stubbed functions, and cross-cutting invariants (threading, ownership, "
        "lifecycle, ordering). Do NOT list bug findings. An empty string is valid if the tree "
        "already contained everything.\n\n"
        "### Required schema\n"
        "```json\n"
        '{ "additional_context": "string — plain-English description; refer to real types, '
        'functions, files, and line numbers" }\n'
        "```\n\n"
        "Your response MUST start with `{` and end with `}`. Return JSON ONLY — no markdown, no prose outside the JSON."
    )


# ======================================================================
# Shared extract/validate building blocks
# ======================================================================


def _extract_dict_with_key(required_key: str, who: str) -> ExtractFn:
    """Builder: extract first dict containing `required_key`. Used by stages
    where the bundle has a single distinctive top-level key.
    """

    def extractor(content: str) -> Optional[str]:
        for candidate in find_all_json_objects(content):
            try:
                parsed = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict) and required_key in parsed:
                logger.info(f"[{who}] Found dict with '{required_key}'")
                return candidate
        # Try array-wrapped variant.
        for arr in find_all_json_arrays(content):
            try:
                parsed = json.loads(arr)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, list):
                for item in parsed:
                    if isinstance(item, dict) and required_key in item:
                        logger.warning(f"[{who}] Bundle wrapped in array — unwrapping")
                        return json.dumps(item)
        return None

    return extractor


def _validate_dict_with_key(required_key: str) -> ValidateFn:
    def validator(parsed: Any) -> bool:
        return isinstance(parsed, dict) and required_key in parsed

    return validator


def _extract_issue_array(who: str) -> ExtractFn:
    """Builder for array-of-issue-dicts extractors.

    Matches the legacy logic: prefer arrays of dicts (largest first); skip
    pure-string arrays (`collection_notes`); for mixed arrays return only the
    dict items; fall back to a dict with `results` key.
    """

    def extractor(content: str) -> Optional[str]:
        for candidate in find_all_json_arrays(content):
            try:
                parsed = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            if not isinstance(parsed, list):
                continue
            if len(parsed) == 0:
                logger.info(f"[{who}] Found empty issues array")
                return candidate
            if all(isinstance(item, dict) for item in parsed):
                logger.info(f"[{who}] Found issues array with {len(parsed)} dicts")
                return candidate
            if all(isinstance(item, str) for item in parsed):
                continue  # skip arrays of strings
            dict_items = [item for item in parsed if isinstance(item, dict)]
            if dict_items:
                logger.warning(f"[{who}] Mixed array — extracting {len(dict_items)} dict items")
                return json.dumps(dict_items)

        for candidate in find_all_json_objects(content):
            try:
                parsed = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict) and "results" in parsed and isinstance(parsed["results"], list):
                logger.info(f"[{who}] Found issues in 'results' key of dict")
                return json.dumps(parsed["results"])
        return None

    return extractor


def _extract_issue_array_with_named_keys(who: str) -> ExtractFn:
    """Like `_extract_issue_array` but also recognizes `issues`/`results`/`findings`
    keys on a wrapping dict (used by perf + trace analyzers).
    """
    base = _extract_issue_array(who)

    def extractor(content: str) -> Optional[str]:
        primary = base(content)
        if primary is not None:
            return primary
        for candidate in find_all_json_objects(content):
            try:
                parsed = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            if not isinstance(parsed, dict):
                continue
            for key in ("issues", "results", "findings"):
                if key in parsed and isinstance(parsed[key], list):
                    logger.info(f"[{who}] Found issues in '{key}' key")
                    return json.dumps(parsed[key])
        return None

    return extractor


def _validate_issue_array(parsed: Any) -> bool:
    if not isinstance(parsed, list):
        return False
    if len(parsed) == 0:
        return True
    return all(isinstance(item, dict) for item in parsed)


def _extract_bool_verdict(aliases: tuple[str, ...], who: str) -> ExtractFn:
    """Builder: extract dict with `result: bool`, normalizing common aliases
    (`is_trivial`, `trivial`, `should_filter`) to `result`.
    """

    def extractor(content: str) -> Optional[str]:
        for candidate in find_all_json_objects(content):
            try:
                parsed = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            if not isinstance(parsed, dict):
                continue
            if "result" in parsed and isinstance(parsed.get("result"), bool):
                logger.info(f"[{who}] verdict result={parsed['result']}")
                return candidate
            if "is_trivial" in parsed and isinstance(parsed.get("is_trivial"), bool):
                parsed["result"] = parsed.pop("is_trivial")
                logger.info(f"[{who}] verdict (is_trivial normalized) result={parsed['result']}")
                return json.dumps(parsed)
            for alias in aliases:
                if alias in parsed and isinstance(parsed.get(alias), bool):
                    parsed["result"] = parsed.pop(alias)
                    logger.info(f"[{who}] verdict ('{alias}' normalized) result={parsed['result']}")
                    return json.dumps(parsed)
        # Fallback: first item of an array of verdicts.
        for arr in find_all_json_arrays(content):
            try:
                parsed = json.loads(arr)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, list) and parsed:
                first = parsed[0]
                if isinstance(first, dict) and "result" in first:
                    logger.info(f"[{who}] Verdict in array, taking first item")
                    return json.dumps(first)
        return None

    return extractor


def _validate_bool_verdict(parsed: Any) -> bool:
    return isinstance(parsed, dict) and isinstance(parsed.get("result"), bool)
