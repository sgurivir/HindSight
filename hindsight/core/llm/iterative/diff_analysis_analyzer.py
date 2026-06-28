#!/usr/bin/env python3
"""
Diff Analysis Analyzer (Stage Db)

Stage-specific iterative analyzer for Diff Analysis.
Expects: array of issue dicts (not array of strings)

This analyzer searches for JSON arrays containing dictionaries (issue objects),
ignoring arrays of strings that may also be present in the response.
Used by GitSimpleDiffAnalyzer for the second stage of diff analysis.
"""

import json
from typing import Optional, Any, TYPE_CHECKING

if TYPE_CHECKING:
    from ..llm import Claude

from .base_iterative_analyzer import BaseIterativeAnalyzer
from ....utils.log_util import get_logger

logger = get_logger(__name__)


class DiffAnalysisAnalyzer(BaseIterativeAnalyzer):
    """
    Iterative analyzer for Diff Analysis (Stage Db).
    
    Expected output: JSON array of issue objects (dicts)
    
    This analyzer specifically searches for arrays containing dictionaries,
    which represent issue objects. It ignores arrays of strings that may
    also be present in the LLM response.
    
    This is essentially the same as CodeAnalysisAnalyzer but with
    diff-specific logging and guidance messages.
    """
    
    def __init__(self, claude: 'Claude'):
        """
        Initialize the Diff Analysis analyzer.
        
        Args:
            claude: Claude client instance for LLM communication
        """
        super().__init__(claude)
    
    def extract_json(self, content: str) -> Optional[str]:
        """
        Extract issues array JSON from LLM response.
        
        Searches for array of dicts (not array of strings).
        Returns FIRST valid match (by size, largest first).
        
        Args:
            content: Raw LLM response content
            
        Returns:
            Extracted JSON string or None if not found
        """
        candidates = self._find_all_json_arrays(content)
        
        for candidate in candidates:
            try:
                parsed = json.loads(candidate)
                if isinstance(parsed, list):
                    # Empty array is valid (no issues found)
                    if len(parsed) == 0:
                        logger.info("[DiffAnalysisAnalyzer] Found empty issues array")
                        return candidate
                    
                    # Array of dicts is valid (issue objects)
                    if all(isinstance(item, dict) for item in parsed):
                        logger.info(f"[DiffAnalysisAnalyzer] Found issues array with {len(parsed)} issue dicts")
                        return candidate
                    
                    # Array of strings is NOT valid (skip it)
                    if all(isinstance(item, str) for item in parsed):
                        logger.debug("[DiffAnalysisAnalyzer] Skipping array of strings")
                        continue
                    
                    # Mixed array - check if it has at least some dicts
                    dict_items = [item for item in parsed if isinstance(item, dict)]
                    if dict_items:
                        logger.warning(f"[DiffAnalysisAnalyzer] Found mixed array, extracting {len(dict_items)} dict items")
                        return json.dumps(dict_items)
                        
            except json.JSONDecodeError:
                continue
        
        # Fallback: check if there's a dict with 'results' key containing an array
        object_candidates = self._find_all_json_objects(content)
        for candidate in object_candidates:
            try:
                parsed = json.loads(candidate)
                if isinstance(parsed, dict) and 'results' in parsed:
                    results = parsed['results']
                    if isinstance(results, list):
                        logger.info("[DiffAnalysisAnalyzer] Found issues in 'results' key of dict")
                        return json.dumps(results)
            except json.JSONDecodeError:
                continue
        
        logger.warning("[DiffAnalysisAnalyzer] No valid issues array found")
        return None
    
    def validate_json(self, parsed_json: Any) -> bool:
        """
        Validate issues array structure.
        
        A valid issues array must be a list where all items are dicts.
        Empty array is also valid (no issues found).
        
        Args:
            parsed_json: Parsed JSON value
            
        Returns:
            True if valid issues array, False otherwise
        """
        if not isinstance(parsed_json, list):
            return False
        
        # Empty array is valid (no issues found)
        if len(parsed_json) == 0:
            return True
        
        # All items must be dicts (issue objects)
        return all(isinstance(item, dict) for item in parsed_json)
    
    def get_fallback_guidance(self, validation_reason: Optional[str] = None) -> str:
        """
        Get diff analysis-specific guidance for JSON output.

        Args:
            validation_reason: Optional description of what was wrong with the
                previous response. Embedded verbatim so the LLM knows exactly
                what to fix.

        Returns:
            Guidance message that includes the canonical issue-array schema
            and CORRECT examples (one issue + empty array).
        """
        reason_block = (
            f"Why your previous response was rejected: {validation_reason}.\n\n"
            if validation_reason
            else ""
        )
        return (
            "CRITICAL: Your previous response did not contain a valid diff issues array.\n\n"
            f"{reason_block}"
            "You MUST respond with ONLY a valid JSON ARRAY of issue objects. "
            "Each item must be a JSON OBJECT (dict), not a string. "
            "Focus on issues in CHANGED lines (marked with `+` prefix in the diff). "
            "If no issues are found in the changed lines, return exactly `[]` — empty is VALID.\n\n"
            "### Required schema (each item)\n"
            "```json\n"
            "{\n"
            '  "file_path": "string — path/to/file.ext",\n'
            '  "file_name": "string — file.ext",\n'
            '  "function_name": "string — functionName",\n'
            '  "line_number": "string — \\"123\\" (must reference a changed/`+` line)",\n'
            '  "severity": "string — high | medium | low",\n'
            '  "issue": "string — brief description",\n'
            '  "description": "string — detailed explanation tied to the diff change",\n'
            '  "suggestion": "string — how to fix",\n'
            '  "category": "string — e.g. logicBug | concurrency | memory | api",\n'
            '  "issueType": "string — e.g. logicBug | concurrency | memory | api"\n'
            "}\n"
            "```\n\n"
            "### CORRECT example (one issue introduced by the diff)\n"
            "```json\n"
            '[{"file_path": "src/Auth.swift", "file_name": "Auth.swift", '
            '"function_name": "Auth.signIn", "line_number": "57", "severity": "high", '
            '"issue": "Token comparison made non-constant-time by new fast-path", '
            '"description": "Newly added `if token == expected` short-circuits and leaks timing.", '
            '"suggestion": "Use a constant-time comparator on every code path.", '
            '"category": "concurrency", "issueType": "concurrency"}]\n'
            "```\n\n"
            "### CORRECT example (no issues in the diff)\n"
            "```json\n"
            "[]\n"
            "```\n\n"
            "Your response MUST start with `[` and end with `]`. "
            "Return JSON ONLY — no markdown fences, no prose, no analysis text outside the array."
        )
