#!/usr/bin/env python3
"""
Non-Actionable Issue Filter (Level 1.5 Filtering)

Deterministic heuristic filter that removes issues with no real impact or
no actionable solution. Uses regex/string matching — no LLM calls required.
Always enabled regardless of LLM filtering configuration.
"""

import json
import os
import re
from datetime import datetime
from typing import List, Dict, Any, Optional, Tuple

from ..utils.log_util import get_logger


# Case-insensitive patterns for detecting non-actionable impact fields
_IMPACT_NON_ACTIONABLE_PATTERNS = [
    re.compile(r'^\s*none\.?\s*$', re.IGNORECASE),
    re.compile(r'\bno\s+(measurable|real[- ]world|performance|significant)\s+(impact|effect)\b', re.IGNORECASE),
    re.compile(r'^\s*negligible\.?\s*$', re.IGNORECASE),
    re.compile(r'\bimpact\s+is\s+(none|negligible|minimal|zero)\b', re.IGNORECASE),
    re.compile(r'^\s*no\s+impact\.?\s*$', re.IGNORECASE),
    re.compile(r'^\s*minimal\.?\s*$', re.IGNORECASE),
]

# Case-insensitive patterns for detecting non-actionable solution fields
_SOLUTION_NON_ACTIONABLE_PATTERNS = [
    re.compile(r'\bno\s+action\s+(required|needed)\b', re.IGNORECASE),
    re.compile(r'\bno\s+change\s+(required|needed)\b', re.IGNORECASE),
    re.compile(r'\bno\s+fix\s+(required|needed)\b', re.IGNORECASE),
    re.compile(r'\bno\s+optimization\s+(required|needed|necessary)\b', re.IGNORECASE),
    re.compile(r'\bno\s+modification\s+(required|needed)\b', re.IGNORECASE),
    re.compile(r'^\s*not\s+applicable\.?\s*$', re.IGNORECASE),
    re.compile(r'^\s*n/?a\.?\s*$', re.IGNORECASE),
    re.compile(r'^\s*none\.?\s*$', re.IGNORECASE),
]

# Patterns for detecting non-issues in the issue text itself
_ISSUE_NON_ACTIONABLE_PATTERNS = [
    re.compile(r'\bno\s+issue(s)?\s+(found|identified|detected)\b', re.IGNORECASE),
    re.compile(r'\bworking\s+as\s+intended\b', re.IGNORECASE),
    re.compile(r'\b(expected|normal|standard)\s+behavior\b', re.IGNORECASE),
    re.compile(r'\bno\s+(performance\s+)?(problem|issue|concern)(s)?\b', re.IGNORECASE),
    re.compile(r'\bthis\s+is\s+(normal|expected|standard)\b', re.IGNORECASE),
    re.compile(r'\bno\s+optimization\s+(opportunity|opportunities)\b', re.IGNORECASE),
]


class NonActionableIssueFilter:
    """
    Level 1.5 filter that removes issues with no actionable impact or solution.

    Uses deterministic string/regex matching to identify and drop issues that
    the LLM reported despite having no real findings. This filter always runs
    (no API key or LLM calls needed) and catches patterns like:
    - "Impact: None"
    - "Potential Solution: No action required"
    - Empty or vacuous issue descriptions
    """

    def __init__(self, dropped_issues_dir: Optional[str] = None):
        """
        Initialize the non-actionable issue filter.

        Args:
            dropped_issues_dir: Directory to save dropped issues for auditability
        """
        self.logger = get_logger(__name__)
        self.dropped_issues_dir = dropped_issues_dir
        self._setup_dropped_issues_directory()
        self.logger.info("NonActionableIssueFilter initialized (deterministic heuristic filter)")

    def _setup_dropped_issues_directory(self) -> None:
        """Setup directory for saving dropped issues."""
        if self.dropped_issues_dir:
            try:
                os.makedirs(self.dropped_issues_dir, exist_ok=True)
            except Exception as e:
                self.logger.error(f"Failed to create dropped issues directory: {e}")
                self.dropped_issues_dir = None

    def filter_issues(self, issues: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Filter out non-actionable issues using heuristic pattern matching.

        Args:
            issues: List of issue dictionaries to filter

        Returns:
            Filtered list with non-actionable issues removed
        """
        if not issues:
            return issues

        filtered = []
        dropped_count = 0

        for issue in issues:
            is_non_actionable, reason = self._is_non_actionable(issue)
            if is_non_actionable:
                dropped_count += 1
                self.logger.info(f"Non-actionable filter dropping issue: {reason}")
                self._save_dropped_issue(issue, reason)
            else:
                filtered.append(issue)

        if dropped_count > 0:
            self.logger.info(
                f"NonActionableIssueFilter: dropped {dropped_count} non-actionable issues, "
                f"{len(filtered)} remaining"
            )

        return filtered

    def _is_non_actionable(self, issue: Dict[str, Any]) -> Tuple[bool, str]:
        """
        Check if an issue is non-actionable based on heuristic patterns.

        Args:
            issue: Issue dictionary with fields like 'impact', 'potentialSolution', 'issue'

        Returns:
            Tuple of (should_drop, reason_string)
        """
        impact = str(issue.get('impact', '') or '').strip()
        solution = str(issue.get('potentialSolution', '') or '').strip()
        issue_text = str(issue.get('issue', '') or '').strip()

        # Check 1: Impact field indicates no real impact
        for pattern in _IMPACT_NON_ACTIONABLE_PATTERNS:
            if pattern.search(impact):
                return True, f"Impact field matches non-actionable pattern: '{impact[:100]}'"

        # Check 2: Solution field indicates no action needed
        for pattern in _SOLUTION_NON_ACTIONABLE_PATTERNS:
            if pattern.search(solution):
                return True, f"Solution field matches non-actionable pattern: '{solution[:100]}'"

        # Check 3: Both impact and solution are empty/whitespace
        if not impact and not solution:
            return True, "Both impact and potentialSolution fields are empty"

        # Check 4: Issue text itself indicates no problem
        for pattern in _ISSUE_NON_ACTIONABLE_PATTERNS:
            if pattern.search(issue_text):
                return True, f"Issue text matches non-actionable pattern: '{issue_text[:100]}'"

        return False, ""

    def _save_dropped_issue(self, issue: Dict[str, Any], reason: str) -> None:
        """Save a dropped issue to disk for auditability."""
        if not self.dropped_issues_dir:
            return

        try:
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
            issue_text = str(issue.get('issue', 'unknown'))[:40]
            safe_name = re.sub(r'[^\w\-]', '_', issue_text)
            filename = f"non_actionable_{timestamp}_{safe_name}.json"
            filepath = os.path.join(self.dropped_issues_dir, filename)

            record = {
                "timestamp": datetime.now().isoformat(),
                "reason": reason,
                "dropped_issue": issue,
            }

            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(record, f, indent=2, ensure_ascii=False)

        except Exception as e:
            self.logger.debug(f"Failed to save dropped issue record: {e}")
