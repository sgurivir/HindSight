#!/usr/bin/env python3
"""
Performance Analysis Analyzer (Stage B)

Stage-specific iterative analyzer for performance analysis.
Expects: array of performance issue dicts.
"""

import json
from typing import Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from ..llm import Claude

from .base_iterative_analyzer import BaseIterativeAnalyzer
from ....utils.log_util import get_logger

logger = get_logger(__name__)


class PerfAnalysisAnalyzer(BaseIterativeAnalyzer):
    """
    Iterative analyzer for Performance Analysis (Stage B).

    Expected output: JSON array of performance issue objects (dicts).
    """

    def __init__(self, claude: "Claude"):
        super().__init__(claude)

    def extract_json(self, content: str) -> Optional[str]:
        """
        Extract perf issues array from LLM response.
        Searches for array of dicts (not array of strings).
        """
        candidates = self._find_all_json_arrays(content)

        for candidate in candidates:
            try:
                parsed = json.loads(candidate)
                if isinstance(parsed, list):
                    if len(parsed) == 0:
                        logger.info("[PerfAnalysisAnalyzer] Found empty issues array (no perf issues)")
                        return candidate

                    if all(isinstance(item, dict) for item in parsed):
                        logger.info(f"[PerfAnalysisAnalyzer] Found issues array with {len(parsed)} items")
                        return candidate

                    if all(isinstance(item, str) for item in parsed):
                        continue

                    dict_items = [item for item in parsed if isinstance(item, dict)]
                    if dict_items:
                        logger.warning(f"[PerfAnalysisAnalyzer] Mixed array, extracting {len(dict_items)} dicts")
                        return json.dumps(dict_items)
            except json.JSONDecodeError:
                continue

        # Fallback: dict with 'issues' or 'results' key
        object_candidates = self._find_all_json_objects(content)
        for candidate in object_candidates:
            try:
                parsed = json.loads(candidate)
                if isinstance(parsed, dict):
                    for key in ("issues", "results", "findings"):
                        if key in parsed and isinstance(parsed[key], list):
                            logger.info(f"[PerfAnalysisAnalyzer] Found issues in '{key}' key")
                            return json.dumps(parsed[key])
            except json.JSONDecodeError:
                continue

        logger.warning("[PerfAnalysisAnalyzer] No valid perf issues array found")
        return None

    def validate_json(self, parsed_json: Any) -> bool:
        """Validate issues array structure."""
        if not isinstance(parsed_json, list):
            return False
        if len(parsed_json) == 0:
            return True
        return all(isinstance(item, dict) for item in parsed_json)

    def get_fallback_guidance(self) -> str:
        """Get perf analysis-specific guidance."""
        return (
            "CRITICAL: Your previous response did not contain a valid performance issues array. "
            "You MUST respond with ONLY a valid JSON array of issue objects. "
            "Your response MUST start with `[` and end with `]`. "
            "Each item must be a JSON object with keys: file_path, function_name, line_number, "
            "severity, issue, description, suggestion, category, issueType. "
            "If no performance issues found, return exactly: [] "
            "No markdown, no prose. Return the JSON array now."
        )
