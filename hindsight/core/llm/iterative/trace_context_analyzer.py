#!/usr/bin/env python3
"""
Trace Context Collection Analyzer (Stage A)

Stage-specific iterative analyzer for trace context collection.
Expects: dict with 'call_path' key (a list of function names in the callstack).
"""

import json
from typing import Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from ..llm import Claude

from .base_iterative_analyzer import BaseIterativeAnalyzer
from ....utils.log_util import get_logger

logger = get_logger(__name__)


class TraceContextAnalyzer(BaseIterativeAnalyzer):
    """
    Iterative analyzer for Trace Context Collection (Stage A).

    Expected output: JSON object with 'call_path' key.
    """

    def __init__(self, claude: "Claude"):
        super().__init__(claude)

    def extract_json(self, content: str) -> Optional[str]:
        """
        Extract trace context bundle JSON from LLM response.
        Searches for dict with 'call_path' key (largest first).
        """
        candidates = self._find_all_json_objects(content)

        for candidate in candidates:
            try:
                parsed = json.loads(candidate)
                if isinstance(parsed, dict) and "call_path" in parsed:
                    logger.info("[TraceContextAnalyzer] Found context bundle with 'call_path' key")
                    return candidate
            except json.JSONDecodeError:
                continue

        logger.warning("[TraceContextAnalyzer] No valid trace context bundle found")
        return None

    def validate_json(self, parsed_json: Any) -> bool:
        """Validate context bundle has required structure."""
        return isinstance(parsed_json, dict) and "call_path" in parsed_json

    def get_fallback_guidance(self, validation_reason: Optional[str] = None) -> str:
        """Get trace context collection-specific guidance, with full schema and example."""
        reason_block = (
            f"Why your previous response was rejected: {validation_reason}.\n\n"
            if validation_reason
            else ""
        )
        return (
            "CRITICAL: Your previous response did not contain a valid trace context bundle.\n\n"
            f"{reason_block}"
            "You MUST respond with ONLY a valid JSON OBJECT containing a `call_path` key. "
            "`call_path` MUST be a list of function-name strings (the trace callstack, "
            "ordered top-most caller → leaf).\n\n"
            "### Required schema\n"
            "```json\n"
            '{ "call_path": ["string", "string", "string"] }\n'
            "```\n\n"
            "### CORRECT example\n"
            "```json\n"
            '{"call_path": ["AppDelegate.applicationDidFinishLaunching", '
            '"WindowController.show", "ViewController.viewDidLoad"]}\n'
            "```\n\n"
            "Your response MUST start with `{` and end with `}`. "
            "Return JSON ONLY — no markdown fences, no arrays, no prose."
        )
