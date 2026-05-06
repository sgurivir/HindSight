#!/usr/bin/env python3
"""
Context Collection Analyzer (Stage 4a)

Stage-specific iterative analyzer for Context Collection.
Expects: dict with 'primary_function' key

This analyzer searches for JSON objects with the 'primary_function' key,
ignoring arrays (like collection_notes) that may also be present in the response.
"""

import json
from typing import Optional, Any, TYPE_CHECKING

if TYPE_CHECKING:
    from ..llm import Claude

from .base_iterative_analyzer import BaseIterativeAnalyzer
from ....utils.log_util import get_logger

logger = get_logger(__name__)


class ContextCollectionAnalyzer(BaseIterativeAnalyzer):
    """
    Iterative analyzer for Context Collection (Stage 4a).
    
    Expected output: JSON object with 'primary_function' key
    
    This analyzer specifically searches for dict structures containing
    the 'primary_function' key, which is the expected output format
    for context collection. It ignores arrays (like collection_notes)
    that may also be present in the LLM response.
    """
    
    def __init__(self, claude: 'Claude'):
        """
        Initialize the Context Collection analyzer.
        
        Args:
            claude: Claude client instance for LLM communication
        """
        super().__init__(claude)
    
    def extract_json(self, content: str) -> Optional[str]:
        """
        Extract context bundle JSON from LLM response.
        
        Searches for dict with 'primary_function' key.
        Returns FIRST match (by size, largest first), not last.
        This is the key difference from clean_json_response() which returns LAST.
        
        Args:
            content: Raw LLM response content
            
        Returns:
            Extracted JSON string or None if not found
        """
        candidates = self._find_all_json_objects(content)
        
        # Find first dict with 'primary_function' key (candidates are sorted by size, largest first)
        for candidate in candidates:
            try:
                parsed = json.loads(candidate)
                if isinstance(parsed, dict) and 'primary_function' in parsed:
                    logger.info("[ContextCollectionAnalyzer] Found context bundle with 'primary_function' key")
                    return candidate
            except json.JSONDecodeError:
                continue
        
        # Fallback: check if any dict is close (has 'primary_function' misspelled or nested differently)
        # Log what we found for debugging, but do NOT return an invalid dict — let the
        # retry loop send the fallback guidance instead.
        for candidate in candidates:
            try:
                parsed = json.loads(candidate)
                if isinstance(parsed, dict):
                    logger.warning(f"[ContextCollectionAnalyzer] Found dict without 'primary_function' key — "
                                   f"top keys: {list(parsed.keys())[:5]}. Will retry with guidance.")
                    break
            except json.JSONDecodeError:
                continue
        
        # Check if there's an array that contains a dict with 'primary_function'
        # (LLM sometimes wraps the bundle in an array)
        array_candidates = self._find_all_json_arrays(content)
        for candidate in array_candidates:
            try:
                parsed = json.loads(candidate)
                if isinstance(parsed, list):
                    for item in parsed:
                        if isinstance(item, dict) and 'primary_function' in item:
                            logger.warning("[ContextCollectionAnalyzer] Found context bundle wrapped in array - extracting")
                            return json.dumps(item)
            except json.JSONDecodeError:
                continue
        
        logger.warning("[ContextCollectionAnalyzer] No valid context bundle found")
        return None
    
    def validate_json(self, parsed_json: Any) -> bool:
        """
        Validate context bundle has required structure.
        
        A valid context bundle must be a dict with 'primary_function' key.
        
        Args:
            parsed_json: Parsed JSON value
            
        Returns:
            True if valid context bundle, False otherwise
        """
        if not isinstance(parsed_json, dict):
            return False
        return 'primary_function' in parsed_json
    
    def get_fallback_guidance(self) -> str:
        """
        Get context collection-specific guidance for JSON output.

        Returns:
            Guidance message for producing a context bundle
        """
        return (
            "CRITICAL: Your previous response did not contain a valid context bundle. "
            "You MUST respond with ONLY a valid JSON object matching this EXACT structure:\n\n"
            '{"schema_version": "1.0", "primary_function": {"function_name": "ClassName::methodName()", '
            '"class_name": "ClassName", "file_path": "relative/path/to/File.swift", '
            '"file_name": "File.swift", "language": "swift", '
            '"start_line": 45, "end_line": 80, "source": "func methodName() { ... }"}, '
            '"callees": [], "callers": [], "data_types": [], '
            '"constants_and_globals": [], "collection_notes": []}\n\n'
            "The 'primary_function' key wrapping the function data is MANDATORY. "
            "Do NOT put function_name, file_path, or source at the top level. "
            "Your response MUST start with `{` and end with `}`. "
            "No markdown, no arrays, no prose. Return the JSON object now."
        )
