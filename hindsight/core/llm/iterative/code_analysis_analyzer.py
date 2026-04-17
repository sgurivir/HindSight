#!/usr/bin/env python3
"""
Code Analysis Analyzer (Stage 4b)

Stage-specific iterative analyzer for Code Analysis.
Expects: array of issue dicts (not array of strings)

This analyzer searches for JSON arrays containing dictionaries (issue objects),
ignoring arrays of strings (like collection_notes) that may also be present.
"""

import json
from typing import Optional, Any, TYPE_CHECKING

if TYPE_CHECKING:
    from ..llm import Claude

from .base_iterative_analyzer import BaseIterativeAnalyzer
from ....utils.log_util import get_logger

logger = get_logger(__name__)


class CodeAnalysisAnalyzer(BaseIterativeAnalyzer):
    """
    Iterative analyzer for Code Analysis (Stage 4b).
    
    Expected output: JSON array of issue objects (dicts)
    
    This analyzer specifically searches for arrays containing dictionaries,
    which represent issue objects. It ignores arrays of strings (like
    collection_notes or other metadata arrays) that may also be present
    in the LLM response.
    """
    
    def __init__(self, claude: 'Claude'):
        """
        Initialize the Code Analysis analyzer.
        
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
                        logger.info("[CodeAnalysisAnalyzer] Found empty issues array")
                        return candidate
                    
                    # Array of dicts is valid (issue objects)
                    if all(isinstance(item, dict) for item in parsed):
                        logger.info(f"[CodeAnalysisAnalyzer] Found issues array with {len(parsed)} issue dicts")
                        return candidate
                    
                    # Array of strings is NOT valid (skip it - likely collection_notes)
                    if all(isinstance(item, str) for item in parsed):
                        logger.debug("[CodeAnalysisAnalyzer] Skipping array of strings (likely collection_notes)")
                        continue
                    
                    # Mixed array - check if it has at least some dicts
                    dict_items = [item for item in parsed if isinstance(item, dict)]
                    if dict_items:
                        logger.warning(f"[CodeAnalysisAnalyzer] Found mixed array, extracting {len(dict_items)} dict items")
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
                        logger.info("[CodeAnalysisAnalyzer] Found issues in 'results' key of dict")
                        return json.dumps(results)
            except json.JSONDecodeError:
                continue
        
        logger.warning("[CodeAnalysisAnalyzer] No valid issues array found")
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
    
    def get_fallback_guidance(self) -> str:
        """
        Get code analysis-specific guidance for JSON output.
        
        Returns:
            Guidance message for producing an issues array
        """
        return (
            "CRITICAL: Your previous response did not contain a valid issues array. "
            "You MUST respond with ONLY a valid JSON array of issue objects. "
            "Your response MUST start with `[` and end with `]`. "
            "Each item in the array must be a JSON object (dict) representing an issue. "
            "If no issues found, return exactly: [] "
            "No markdown, no prose, no explanatory text. Return the JSON array now."
        )
