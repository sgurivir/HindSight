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

    def get_fallback_guidance(self) -> str:
        """Get trace context collection-specific guidance."""
        return (
            "CRITICAL: Your previous response did not contain a valid trace context bundle. "
            "You MUST respond with ONLY a valid JSON object containing a 'call_path' key. "
            "The 'call_path' value must be a list of function names representing the callstack.\n\n"
            'Example: {"call_path": ["FuncA", "FuncB", "FuncC"]}\n\n'
            "Your response MUST start with `{` and end with `}`. "
            "No markdown, no arrays, no prose. Return the JSON object now."
        )
