#!/usr/bin/env python3
"""
Diff Context Analyzer (Stage Da)

Stage-specific iterative analyzer for Diff Context Collection.
Expects: dict with 'changed_functions' key

This analyzer searches for JSON objects with the 'changed_functions' key,
ignoring arrays that may also be present in the response.
Used by GitSimpleDiffAnalyzer for the first stage of diff analysis.
"""

import json
from typing import Optional, Any, TYPE_CHECKING

if TYPE_CHECKING:
    from ..llm import Claude

from .base_iterative_analyzer import BaseIterativeAnalyzer
from ....utils.log_util import get_logger

logger = get_logger(__name__)


class DiffContextAnalyzer(BaseIterativeAnalyzer):
    """
    Iterative analyzer for Diff Context Collection (Stage Da).
    
    Expected output: JSON object with 'changed_functions' key
    
    This analyzer specifically searches for dict structures containing
    the 'changed_functions' key, which is the expected output format
    for diff context collection. It ignores arrays that may also be
    present in the LLM response.
    """
    
    def __init__(self, claude: 'Claude'):
        """
        Initialize the Diff Context analyzer.
        
        Args:
            claude: Claude client instance for LLM communication
        """
        super().__init__(claude)
    
    def extract_json(self, content: str) -> Optional[str]:
        """
        Extract diff context bundle JSON from LLM response.
        
        Searches for dict with 'changed_functions' key.
        Returns FIRST match (by size, largest first), not last.
        
        Args:
            content: Raw LLM response content
            
        Returns:
            Extracted JSON string or None if not found
        """
        candidates = self._find_all_json_objects(content)
        
        # Find first dict with 'changed_functions' key (candidates are sorted by size, largest first)
        for candidate in candidates:
            try:
                parsed = json.loads(candidate)
                if isinstance(parsed, dict) and 'changed_functions' in parsed:
                    logger.info("[DiffContextAnalyzer] Found diff context bundle with 'changed_functions' key")
                    return candidate
            except json.JSONDecodeError:
                continue
        
        # Alternative key: 'primary_function' (some diff contexts use this structure)
        for candidate in candidates:
            try:
                parsed = json.loads(candidate)
                if isinstance(parsed, dict) and 'primary_function' in parsed:
                    logger.info("[DiffContextAnalyzer] Found diff context bundle with 'primary_function' key")
                    return candidate
            except json.JSONDecodeError:
                continue
        
        # Fallback: any dict (might be partial or different structure)
        for candidate in candidates:
            try:
                parsed = json.loads(candidate)
                if isinstance(parsed, dict):
                    logger.warning("[DiffContextAnalyzer] Found dict without expected keys - using as fallback")
                    return candidate
            except json.JSONDecodeError:
                continue
        
        # Check if there's an array that contains a dict with 'changed_functions'
        # (LLM sometimes wraps the bundle in an array)
        array_candidates = self._find_all_json_arrays(content)
        for candidate in array_candidates:
            try:
                parsed = json.loads(candidate)
                if isinstance(parsed, list):
                    for item in parsed:
                        if isinstance(item, dict) and 'changed_functions' in item:
                            logger.warning("[DiffContextAnalyzer] Found diff context bundle wrapped in array - extracting")
                            return json.dumps(item)
            except json.JSONDecodeError:
                continue
        
        logger.warning("[DiffContextAnalyzer] No valid diff context bundle found")
        return None
    
    def validate_json(self, parsed_json: Any) -> bool:
        """
        Validate diff context bundle has required structure.
        
        A valid diff context bundle must be a dict with 'changed_functions' key
        or 'primary_function' key (alternative structure).
        
        Args:
            parsed_json: Parsed JSON value
            
        Returns:
            True if valid diff context bundle, False otherwise
        """
        if not isinstance(parsed_json, dict):
            return False
        
        # Primary expected key
        if 'changed_functions' in parsed_json:
            return True
        
        # Alternative structure (some diff contexts use this)
        if 'primary_function' in parsed_json:
            return True
        
        return False
    
    def get_fallback_guidance(self, validation_reason: Optional[str] = None) -> str:
        """
        Get diff context collection-specific guidance for JSON output.

        Args:
            validation_reason: Optional description of what was wrong with the
                previous response. Embedded verbatim so the LLM knows exactly
                what to fix.

        Returns:
            Guidance message that includes the canonical schema and a CORRECT
            example, plus a WRONG example highlighting the missing-wrapper
            failure mode.
        """
        reason_block = (
            f"Why your previous response was rejected: {validation_reason}.\n\n"
            if validation_reason
            else ""
        )
        return (
            "CRITICAL: Your previous response did not contain a valid diff context bundle.\n\n"
            f"{reason_block}"
            "You MUST respond with ONLY a valid JSON OBJECT matching the schema below. "
            "The bundle MUST have a top-level `primary_function` key (the diff's primary "
            "function), wrapping all of the function metadata.\n\n"
            "### Required schema\n"
            "```json\n"
            "{\n"
            '  "schema_version": "1.0",\n'
            '  "primary_function": {\n'
            '    "function_name": "string", "class_name": "string | null",\n'
            '    "file_path": "string", "file_name": "string", "language": "string",\n'
            '    "start_line": 0, "end_line": 0,\n'
            '    "source": "string — full verbatim source with +/-/space markers per line",\n'
            '    "changed_lines": [ { "line": 0, "marker": "+ | -", "code": "string" } ],\n'
            '    "is_modified": true\n'
            "  },\n"
            '  "callees": [ { "function_name": "string", "file_path": "string", '
            '"start_line": 0, "end_line": 0, "source": "string", "is_modified": false, '
            '"changed_lines": [], "affected_reason": "string", '
            '"call_sites": [ { "line": 0, "expression": "string" } ] } ],\n'
            '  "callers": [ { "function_name": "string", "file_path": "string", '
            '"start_line": 0, "end_line": 0, "source": "string", "is_modified": false, '
            '"changed_lines": [], "affected_reason": "string" } ],\n'
            '  "data_types": [ { "type_name": "string", "kind": "string", '
            '"file_path": "string", "start_line": 0, "end_line": 0, "source": "string" } ],\n'
            '  "constants_and_globals": [ { "name": "string", "file_path": "string", '
            '"line": 0, "source": "string" } ],\n'
            '  "diff_context": { "total_lines_added": 0, "total_lines_removed": 0, '
            '"files_changed_in_diff": [ "string" ] },\n'
            '  "collection_notes": [ "string" ]\n'
            "}\n"
            "```\n\n"
            "### CORRECT minimal example\n"
            "```json\n"
            '{"schema_version": "1.0", "primary_function": {"function_name": "Auth.signIn", '
            '"class_name": "Auth", "file_path": "src/Auth.swift", "file_name": "Auth.swift", '
            '"language": "swift", "start_line": 50, "end_line": 70, '
            '"source": " 50: func signIn(...) {\\n+51:   if token == expected { ... }\\n}", '
            '"changed_lines": [{"line": 51, "marker": "+", "code": "if token == expected { ... }"}], '
            '"is_modified": true}, '
            '"callees": [], "callers": [], "data_types": [], "constants_and_globals": [], '
            '"diff_context": {"total_lines_added": 1, "total_lines_removed": 0, '
            '"files_changed_in_diff": ["src/Auth.swift"]}, "collection_notes": []}\n'
            "```\n\n"
            "### WRONG (do NOT do this — missing `primary_function` wrapper)\n"
            "```json\n"
            '{"function_name": "Auth.signIn", "file_path": "src/Auth.swift", '
            '"start_line": 50, "end_line": 70, "source": "..."}\n'
            "```\n\n"
            "Your response MUST start with `{` and end with `}`. "
            "Return JSON ONLY — no markdown fences, no arrays, no prose."
        )
