#!/usr/bin/env python3
"""
Trace Relevance Filter

Filters trace-analysis issues that are not relevant to the original
callstack/trace. Each issue gets a single LLM call (no tools); the verdict
shape is `{"result": bool}` — same as `stage_trivial_filter`, which we reuse.
"""

import json
import os
from datetime import datetime
from typing import Dict, Any, List
from pathlib import Path

from ..core.constants import DEFAULT_MAX_TOKENS, DEFAULT_LLM_MODEL, DEFAULT_LLM_API_END_POINT
from ..utils.log_util import get_logger
from ..utils.output_directory_provider import get_output_directory_provider


class TraceRelevanceFilter:
    """LLM-driven classifier: is each issue relevant to the original trace?

    Drops issues the model marks as not relevant; saves them to
    `trace_dropped_issues/` for audit.
    """

    def __init__(self, api_key: str, config: Dict[str, Any]):
        self.logger = get_logger(__name__)
        self.api_key = api_key
        self.config = config
        self.dropped_issues_dir = None
        self._setup_dropped_issues_directory()
        self.system_prompt = self._load_system_prompt()

    def _setup_dropped_issues_directory(self) -> None:
        try:
            output_provider = get_output_directory_provider()
            output_base_dir = output_provider.get_repo_artifacts_dir()
            self.dropped_issues_dir = os.path.join(output_base_dir, "trace_dropped_issues")
            os.makedirs(self.dropped_issues_dir, exist_ok=True)
            self.logger.info(f"Trace dropped issues directory created: {self.dropped_issues_dir}")
        except Exception as exc:
            self.logger.error(f"Failed to create trace dropped issues directory: {exc}")
            self.dropped_issues_dir = None

    def _load_system_prompt(self) -> str:
        prompt_path = (
            Path(__file__).parent.parent / "core" / "prompts" / "traceRelevanceFilterPrompt.md"
        )
        try:
            with open(prompt_path, "r", encoding="utf-8") as fh:
                return fh.read()
        except Exception as exc:
            self.logger.error(f"Failed to load trace relevance prompt: {exc}; using fallback")
            # Lazy import to avoid the legacy circular-import between
            # `core.prompts` and `core.llm`.
            from ..core.prompts.fallback_prompts import FALLBACK_TRACE_RELEVANCE_FILTER_SYSTEM
            return FALLBACK_TRACE_RELEVANCE_FILTER_SYSTEM

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _save_dropped_issue(
        self,
        issue: Dict[str, Any],
        original_trace: List[str],
        filter_result: Dict[str, Any],
    ) -> None:
        if not self.dropped_issues_dir:
            return
        try:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            text = issue.get("issue", "unknown_issue")[:50]
            safe = "".join(c for c in text if c.isalnum() or c in ("_", "-", " ")).replace(" ", "_")
            filepath = os.path.join(
                self.dropped_issues_dir, f"dropped_trace_issue_{ts}_{safe}.json"
            )
            record = {
                "timestamp": datetime.now().isoformat(),
                "original_issue": issue,
                "original_trace": original_trace,
                "filter_result": filter_result,
                "reason": "Issue classified as irrelevant to original trace by LLM filter",
            }
            with open(filepath, "w", encoding="utf-8") as fh:
                json.dump(record, fh, indent=2, ensure_ascii=False)
        except Exception as exc:
            self.logger.error(f"Failed to save dropped trace issue: {exc}")

    # ------------------------------------------------------------------
    # Filtering
    # ------------------------------------------------------------------

    def filter_issues(
        self,
        issues: List[Dict[str, Any]],
        original_trace: List[str],
    ) -> List[Dict[str, Any]]:
        """Return only the issues the LLM judges relevant to `original_trace`.

        Per-issue failures or invalid verdicts keep the issue — defensive
        behavior matching the legacy code (`return True on error`).
        """
        if not issues:
            return issues

        from hindsight.llm import (
            SyncStageRunner,
            make_client_config_from_dict,
            stage_trivial_filter,
        )

        self.logger.info(f"Filtering {len(issues)} issues for trace relevance...")
        try:
            client_config = make_client_config_from_dict(
                api_key=self.api_key,
                config=self.config,
                default_api_url=DEFAULT_LLM_API_END_POINT,
                default_model=DEFAULT_LLM_MODEL,
                default_max_tokens=DEFAULT_MAX_TOKENS,
            )
        except Exception as exc:
            self.logger.error(f"Could not build client config: {exc}; keeping all issues")
            return list(issues)

        trace_text = "\n".join(original_trace) if original_trace else "No trace provided"
        user_prompts: List[str] = []
        for issue in issues:
            issue_json = json.dumps(issue, indent=2, ensure_ascii=False)
            user_prompts.append(
                "Analyze this trace analysis issue for relevance to the original callstack:\n\n"
                f"ORIGINAL CALLSTACK/TRACE:\n{trace_text}\n\n"
                f"ISSUE TO EVALUATE:\n{issue_json}\n\n"
                "Determine if this issue is relevant to the original callstack/trace above."
            )

        # Reuse stage_trivial_filter because both expect a `{"result": bool}` verdict.
        # Semantics here: `result=true` means IRRELEVANT (drop) — matches legacy.
        try:
            verdicts = SyncStageRunner(client_config).run_many(
                stage_trivial_filter(self.system_prompt, max_iterations=3),
                user_prompts,
                max_iterations=3,
            )
        except Exception as exc:
            self.logger.error(f"SyncStageRunner failed; keeping all issues: {exc}")
            return list(issues)

        filtered: List[Dict[str, Any]] = []
        dropped = 0
        for idx, (issue, verdict) in enumerate(zip(issues, verdicts), start=1):
            # Legacy semantics: the trace-relevance prompt returns
            # `{result: true}` to mean RELEVANT (keep), `false` to mean
            # IRRELEVANT (drop). Be defensive when verdict is missing.
            is_relevant = True
            if isinstance(verdict, dict) and isinstance(verdict.get("result"), bool):
                is_relevant = verdict["result"]
            if is_relevant:
                filtered.append(issue)
            else:
                dropped += 1
                self.logger.info(f"Dropping irrelevant issue {idx}/{len(issues)}")
                self._save_dropped_issue(issue, original_trace, verdict or {})

        self.logger.info(
            f"Trace relevance filtering complete: {len(filtered)} kept, {dropped} dropped"
        )
        return filtered

    # ------------------------------------------------------------------
    # Legacy single-issue API (kept for any direct callers)
    # ------------------------------------------------------------------

    def is_relevant_to_trace(
        self,
        issue: Dict[str, Any],
        original_trace: List[str],
    ) -> bool:
        """Single-issue relevance check. Convenience wrapper over `filter_issues`."""
        filtered = self.filter_issues([issue], original_trace)
        return len(filtered) == 1
