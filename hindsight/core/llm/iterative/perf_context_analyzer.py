#!/usr/bin/env python3
"""
Performance Context Collection Analyzer (Stage A)

Stage-specific iterative analyzer for performance context collection.
Expects: dict with 'call_path' and 'functions' keys.
"""

import json
from typing import Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from ..llm import Claude

from .base_iterative_analyzer import BaseIterativeAnalyzer
from ....utils.log_util import get_logger

logger = get_logger(__name__)


class PerfContextAnalyzer(BaseIterativeAnalyzer):
    """
    Iterative analyzer for Performance Context Collection (Stage A).

    Expected output: JSON object with 'call_path' and 'functions' keys.
    """

    def __init__(self, claude: "Claude"):
        super().__init__(claude)

    def extract_json(self, content: str) -> Optional[str]:
        """
        Extract perf context bundle JSON from LLM response.
        Searches for dict with 'call_path' or 'functions' key.
        """
        candidates = self._find_all_json_objects(content)

        for candidate in candidates:
            try:
                parsed = json.loads(candidate)
                if isinstance(parsed, dict) and ("call_path" in parsed or "functions" in parsed):
                    logger.info("[PerfContextAnalyzer] Found context bundle with expected keys")
                    return candidate
            except json.JSONDecodeError:
                continue

        # Check arrays for wrapped bundles
        array_candidates = self._find_all_json_arrays(content)
        for candidate in array_candidates:
            try:
                parsed = json.loads(candidate)
                if isinstance(parsed, list):
                    for item in parsed:
                        if isinstance(item, dict) and ("call_path" in item or "functions" in item):
                            logger.warning("[PerfContextAnalyzer] Found bundle wrapped in array - extracting")
                            return json.dumps(item)
            except json.JSONDecodeError:
                continue

        logger.warning("[PerfContextAnalyzer] No valid perf context bundle found")
        return None

    def validate_json(self, parsed_json: Any) -> bool:
        """Validate context bundle has required structure."""
        if not isinstance(parsed_json, dict):
            return False
        return "functions" in parsed_json or "call_path" in parsed_json

    def get_fallback_guidance(self) -> str:
        """Get perf context collection-specific guidance."""
        return (
            "CRITICAL: Your previous response did not contain a valid performance context bundle. "
            "You MUST respond with ONLY a valid JSON object matching this structure:\n\n"
            '{"call_path": ["FuncA", "FuncB", "FuncC"], '
            '"functions": {"FuncA": {"body": "...", "file": "...", "line": 42}, ...}, '
            '"data_types_used": {...}, '
            '"resource_patterns": {"allocations": [], "io_operations": [], "synchronization": [], "loops": []}, '
            '"additional_context": {}}\n\n'
            "Your response MUST start with `{` and end with `}`. "
            "No markdown, no arrays, no prose. Return the JSON object now."
        )
