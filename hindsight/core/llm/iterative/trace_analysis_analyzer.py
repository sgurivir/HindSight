#!/usr/bin/env python3
"""
Trace Analysis Analyzer (Stage B)

Stage-specific iterative analyzer for trace analysis.
Expects: array of performance issue dicts.
"""

import json
from typing import Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from ..llm import Claude

from .base_iterative_analyzer import BaseIterativeAnalyzer
from ....utils.log_util import get_logger

logger = get_logger(__name__)


class TraceAnalysisAnalyzer(BaseIterativeAnalyzer):
    """
    Iterative analyzer for Trace Analysis (Stage B).

    Expected output: JSON array of performance issue objects (dicts).
    """

    def __init__(self, claude: "Claude"):
        super().__init__(claude)

    def extract_json(self, content: str) -> Optional[str]:
        """
        Extract trace issues array from LLM response.
        Searches for array of dicts (not array of strings).
        """
        candidates = self._find_all_json_arrays(content)

        for candidate in candidates:
            try:
                parsed = json.loads(candidate)
                if isinstance(parsed, list):
                    if len(parsed) == 0:
                        logger.info("[TraceAnalysisAnalyzer] Found empty issues array (no trace issues)")
                        return candidate

                    if all(isinstance(item, dict) for item in parsed):
                        logger.info(f"[TraceAnalysisAnalyzer] Found issues array with {len(parsed)} items")
                        return candidate

                    if all(isinstance(item, str) for item in parsed):
                        continue

                    dict_items = [item for item in parsed if isinstance(item, dict)]
                    if dict_items:
                        logger.warning(f"[TraceAnalysisAnalyzer] Mixed array, extracting {len(dict_items)} dicts")
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
                            logger.info(f"[TraceAnalysisAnalyzer] Found issues in '{key}' key")
                            return json.dumps(parsed[key])
            except json.JSONDecodeError:
                continue

        logger.warning("[TraceAnalysisAnalyzer] No valid trace issues array found")
        return None

    def validate_json(self, parsed_json: Any) -> bool:
        """Validate issues array structure."""
        if not isinstance(parsed_json, list):
            return False
        if len(parsed_json) == 0:
            return True
        return all(isinstance(item, dict) for item in parsed_json)

    def get_fallback_guidance(self, validation_reason: Optional[str] = None) -> str:
        """Get trace analysis-specific guidance, with full schema and example."""
        reason_block = (
            f"Why your previous response was rejected: {validation_reason}.\n\n"
            if validation_reason
            else ""
        )
        return (
            "CRITICAL: Your previous response did not contain a valid trace issues array.\n\n"
            f"{reason_block}"
            "You MUST respond with ONLY a valid JSON ARRAY of trace-issue objects. "
            "Each item must be a JSON OBJECT (dict), not a string. "
            "If no issues are found in the trace, return exactly `[]` — empty is VALID.\n\n"
            "### Required schema (each item)\n"
            "```json\n"
            "{\n"
            '  "function_name": "string", "file_path": "string", "line_number": "string",\n'
            '  "severity": "string — high | medium | low",\n'
            '  "issue": "string", "description": "string", "suggestion": "string",\n'
            '  "category": "string", "issueType": "string"\n'
            "}\n"
            "```\n\n"
            "### CORRECT example\n"
            "```json\n"
            '[{"function_name": "DBManager.fetchAll", "file_path": "src/DBManager.swift", '
            '"line_number": "88", "severity": "high", '
            '"issue": "Synchronous DB fetch on the main thread", '
            '"description": "fetchAll() blocks the main thread for ~120ms.", '
            '"suggestion": "Move to background queue and surface results asynchronously.", '
            '"category": "io", "issueType": "io"}]\n'
            "```\n\n"
            "### CORRECT example (no issues)\n"
            "```json\n"
            "[]\n"
            "```\n\n"
            "Your response MUST start with `[` and end with `]`. "
            "Return JSON ONLY — no markdown fences, no prose."
        )
