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

    def get_fallback_guidance(self, validation_reason: Optional[str] = None) -> str:
        """Get perf context collection-specific guidance, with full schema and example."""
        reason_block = (
            f"Why your previous response was rejected: {validation_reason}.\n\n"
            if validation_reason
            else ""
        )
        return (
            "CRITICAL: Your previous response did not contain a valid performance context bundle.\n\n"
            f"{reason_block}"
            "You MUST respond with ONLY a valid JSON OBJECT matching the schema below. "
            "The bundle MUST include `call_path` (list of function names) AND `functions` "
            "(map keyed by name).\n\n"
            "### Required schema\n"
            "```json\n"
            "{\n"
            '  "call_path": ["FuncA", "FuncB", "FuncC"],\n'
            '  "functions": {\n'
            '    "FuncA": {\n'
            '      "body": "string — full source",\n'
            '      "file": "string", "line": 0,\n'
            '      "data_types_used": ["TypeA"],\n'
            '      "resource_patterns": {\n'
            '        "allocations": [], "io_operations": [], "synchronization": [],\n'
            '        "loops": [], "caching": []\n'
            "      },\n"
            '      "threading_context": "string"\n'
            "    }\n"
            "  },\n"
            '  "data_types_used": { "TypeA": { "definition_summary": "string", "file": "string" } },\n'
            '  "data_flow": { "FuncA→FuncB": "string" },\n'
            '  "constants_and_globals": ["string"],\n'
            '  "additional_context": {}\n'
            "}\n"
            "```\n\n"
            "### CORRECT minimal example\n"
            "```json\n"
            '{"call_path": ["A", "B"], '
            '"functions": {"A": {"body": "func A() { B() }", "file": "src/A.swift", "line": 10, '
            '"data_types_used": [], "resource_patterns": {"allocations": [], "io_operations": [], '
            '"synchronization": [], "loops": [], "caching": []}, "threading_context": "main"}, '
            '"B": {"body": "func B() {}", "file": "src/B.swift", "line": 20, '
            '"data_types_used": [], "resource_patterns": {"allocations": [], "io_operations": [], '
            '"synchronization": [], "loops": [], "caching": []}, "threading_context": "main"}}, '
            '"data_types_used": {}, "data_flow": {}, '
            '"constants_and_globals": [], "additional_context": {}}\n'
            "```\n\n"
            "Your response MUST start with `{` and end with `}`. "
            "Return JSON ONLY — no markdown fences, no arrays, no prose."
        )
