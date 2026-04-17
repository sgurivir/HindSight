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
    
    def get_fallback_guidance(self) -> str:
        """
        Get diff context collection-specific guidance for JSON output.
        
        Returns:
            Guidance message for producing a diff context bundle
        """
        return (
            "CRITICAL: Your previous response did not contain a valid diff context bundle. "
            "You MUST respond with ONLY a valid JSON diff context bundle object. "
            "Your response MUST start with `{` and end with `}`. "
            "The JSON object MUST contain a 'changed_functions' key. "
            "No markdown, no arrays, no prose. Return the JSON object now."
        )
