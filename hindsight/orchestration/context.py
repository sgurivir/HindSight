"""Per-session configuration object.

Replaces the loose `dict[str, Any]` config that gets passed everywhere in the
legacy code. Built once when a session is created from a raw config dict and
a repo path; consumed read-only thereafter.

Why a frozen dataclass and not pydantic: the rest of the codebase uses
dataclasses + typing, and pydantic would force every config-loading callsite
to construct a model. Pydantic earns its keep at the FastAPI request boundary,
not for internal config carrying. The fault-tolerant defaults in
`AnalysisContext.from_config` give us the same "tolerate missing keys" win
without the dependency.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Tuple

from hindsight.core.constants import (
    CALL_TREE_ANALYSIS_ENABLED,
    CALL_TREE_ANALYSIS_MAX_CHARS,
    CALL_TREE_ANALYSIS_MAX_DEPTH,
    CALL_TREE_ANALYSIS_MAX_NODES,
    CODE_ANALYZER_DEFAULT_WORKERS,
    DEFAULT_LLM_API_END_POINT,
    DEFAULT_LLM_MODEL,
    DEFAULT_MAX_TOKENS,
    DEFAULT_NUM_FUNCTIONS_TO_ANALYZE,
    LLM_PROVIDER_RATE_LIMIT,
    LLM_PROVIDER_RATE_WINDOW_SECONDS,
    MAX_FUNCTION_BODY_LENGTH,
    MIN_FUNCTION_BODY_LENGTH,
)
from hindsight.utils.log_util import get_logger
from hindsight.utils.output_directory_provider import get_output_directory_provider

logger = get_logger(__name__)


def _as_str_tuple(value: Any) -> Tuple[str, ...]:
    """Tolerate None, list, tuple, or stray scalar — always return a tuple of strs."""
    if value is None:
        return ()
    if isinstance(value, (list, tuple, set)):
        return tuple(str(x) for x in value)
    return (str(value),)


@dataclass(frozen=True)
class AnalysisContext:
    """Immutable typed configuration shared across one session.

    Built from a loose `dict` config so the existing CLI/JSON config plumbing
    keeps working — just one validation point at session creation.
    """

    # Identity / paths --------------------------------------------------
    repo_path: str
    repo_name: str
    output_base_dir: str
    artifacts_dir: str            # {output_base_dir}/{repo_name}
    code_insights_dir: str        # {artifacts_dir}/code_insights — AST artifacts (read-only)
    results_dir: str              # {artifacts_dir}/results
    context_bundles_dir: str      # {artifacts_dir}/context_bundles      (stage-4a cache)
    diff_context_bundles_dir: str # {artifacts_dir}/diff_context_bundles (stage-Da cache)

    # LLM ---------------------------------------------------------------
    api_key: str
    api_url: str
    model: str
    max_tokens: int

    # Concurrency / rate --------------------------------------------------
    max_workers: int
    rate_limit: int
    rate_window_seconds: int

    # Pipeline behavior ---------------------------------------------------
    enable_call_tree: bool
    call_tree_max_depth: int
    call_tree_max_chars: int
    call_tree_max_nodes: int

    # Function filters -----------------------------------------------------
    file_filter: Tuple[str, ...]
    include_directories: Tuple[str, ...]
    exclude_directories: Tuple[str, ...]
    exclude_files: Tuple[str, ...]
    min_function_body_length: int
    max_function_body_length: int
    num_functions_to_analyze: int

    # Prompts / misc -------------------------------------------------------
    user_provided_prompts: Tuple[str, ...]
    raw_config: Mapping[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    @classmethod
    def from_config(
        cls,
        *,
        repo_path: str,
        config: Mapping[str, Any],
        output_base_dir: str,
        api_key: str = "",
        artifacts_dir: Optional[str] = None,
    ) -> "AnalysisContext":
        """Build a context from a CLI-style config dict.

        Tolerant of missing keys — every field falls back to a sane default
        from `hindsight.core.constants`. Path fields are derived from
        `output_base_dir` + the repo basename so they line up with what
        `OutputDirectoryProvider` would produce (preserves the on-disk layout
        all existing reports/tools depend on).

        `artifacts_dir` may be passed explicitly when the calling analyzer has
        an unusual layout (e.g. the diff analyzer puts everything under
        `{out_dir}/{repo}_diff_analysis/analysis/` instead of
        `{out_dir}/{repo}/`). When omitted, the default
        `{output_base_dir}/{repo_name}/` is used.
        """
        repo_name = os.path.basename(repo_path.rstrip("/")) or "repo"
        resolved_artifacts_dir = (
            artifacts_dir if artifacts_dir is not None else os.path.join(output_base_dir, repo_name)
        )

        config_get = config.get if isinstance(config, Mapping) else lambda *_args, **_kw: None

        return cls(
            repo_path=repo_path,
            repo_name=repo_name,
            output_base_dir=output_base_dir,
            artifacts_dir=resolved_artifacts_dir,
            code_insights_dir=os.path.join(resolved_artifacts_dir, "code_insights"),
            results_dir=os.path.join(resolved_artifacts_dir, "results"),
            context_bundles_dir=os.path.join(resolved_artifacts_dir, "context_bundles"),
            diff_context_bundles_dir=os.path.join(resolved_artifacts_dir, "diff_context_bundles"),
            api_key=api_key or "",
            api_url=str(config_get("api_end_point", DEFAULT_LLM_API_END_POINT)),
            model=str(config_get("model", DEFAULT_LLM_MODEL)),
            max_tokens=int(config_get("max_tokens", DEFAULT_MAX_TOKENS)),
            max_workers=int(
                config_get("code_analyzer_workers", CODE_ANALYZER_DEFAULT_WORKERS)
            ),
            rate_limit=int(config_get("code_analyzer_rate_limit", LLM_PROVIDER_RATE_LIMIT)),
            rate_window_seconds=int(
                config_get("code_analyzer_rate_window_seconds", LLM_PROVIDER_RATE_WINDOW_SECONDS)
            ),
            enable_call_tree=bool(
                config_get("call_tree_analysis_enabled", CALL_TREE_ANALYSIS_ENABLED)
            ),
            call_tree_max_depth=int(
                config_get("call_tree_max_depth", CALL_TREE_ANALYSIS_MAX_DEPTH)
            ),
            call_tree_max_chars=int(
                config_get("call_tree_max_chars", CALL_TREE_ANALYSIS_MAX_CHARS)
            ),
            call_tree_max_nodes=int(
                config_get("call_tree_max_nodes", CALL_TREE_ANALYSIS_MAX_NODES)
            ),
            file_filter=_as_str_tuple(config_get("file_filter")),
            include_directories=_as_str_tuple(config_get("include_directories")),
            exclude_directories=_as_str_tuple(config_get("exclude_directories")),
            exclude_files=_as_str_tuple(config_get("exclude_files")),
            min_function_body_length=int(
                config_get("min_function_body_length", MIN_FUNCTION_BODY_LENGTH)
            ),
            max_function_body_length=int(
                config_get("max_function_body_length", MAX_FUNCTION_BODY_LENGTH)
            ),
            num_functions_to_analyze=int(
                config_get("num_functions_to_analyze", DEFAULT_NUM_FUNCTIONS_TO_ANALYZE)
            ),
            user_provided_prompts=_as_str_tuple(config_get("user_provided_prompts")),
            raw_config=dict(config),
        )

    # ------------------------------------------------------------------
    # Convenience
    # ------------------------------------------------------------------

    def ensure_directories(self) -> None:
        """Create the directories this session will write to (idempotent)."""
        for path in (
            self.artifacts_dir,
            self.results_dir,
            self.context_bundles_dir,
            self.diff_context_bundles_dir,
        ):
            try:
                Path(path).mkdir(parents=True, exist_ok=True)
            except Exception as exc:  # noqa: BLE001 — best-effort directory creation
                logger.warning(f"Could not create directory {path}: {exc}")
