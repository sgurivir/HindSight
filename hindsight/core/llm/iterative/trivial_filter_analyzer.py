#!/usr/bin/env python3
"""
Trivial Filter Analyzer (Level 2 Filtering)

Stage-specific iterative analyzer for LLM-based Trivial Issue Filter.
Expects: dict with 'result' (boolean) key

This analyzer identifies trivial issues that should be filtered out
before the more expensive Level 3 response challenger analysis.
"""

import json
from typing import Optional, Any, TYPE_CHECKING

if TYPE_CHECKING:
    from ..llm import Claude

from .base_iterative_analyzer import BaseIterativeAnalyzer
from ....utils.log_util import get_logger

logger = get_logger(__name__)


class TrivialFilterAnalyzer(BaseIterativeAnalyzer):
    """
    Iterative analyzer for Trivial Issue Filter (Level 2 Filtering).
    
    Expected output: JSON object with 'result' (boolean) key
    
    The 'result' field indicates whether the issue is trivial:
    - result: true  -> Issue is trivial (filter out)
    - result: false -> Issue is not trivial (keep for further analysis)
    """
    
    def __init__(self, claude: 'Claude'):
        """
        Initialize the Trivial Filter analyzer.
        
        Args:
            claude: Claude client instance for LLM communication
        """
        super().__init__(claude)
    
    def extract_json(self, content: str) -> Optional[str]:
        """
        Extract trivial filter verdict JSON from LLM response.
        
        Searches for dict with 'result' key (boolean verdict).
        Returns FIRST valid match (by size, largest first).
        
        Args:
            content: Raw LLM response content
            
        Returns:
            Extracted JSON string or None if not found
        """
        candidates = self._find_all_json_objects(content)
        
        for candidate in candidates:
            try:
                parsed = json.loads(candidate)
                if isinstance(parsed, dict):
                    # Must have 'result' key with boolean value
                    if 'result' in parsed and isinstance(parsed.get('result'), bool):
                        logger.info(f"[TrivialFilterAnalyzer] Found verdict dict with result={parsed['result']}")
                        return candidate
                    
                    # Check for common variations
                    if 'is_trivial' in parsed and isinstance(parsed.get('is_trivial'), bool):
                        # Normalize to 'result' key
                        parsed['result'] = parsed.pop('is_trivial')
                        logger.info(f"[TrivialFilterAnalyzer] Found verdict dict with is_trivial (normalized to result={parsed['result']})")
                        return json.dumps(parsed)
                    
                    if 'trivial' in parsed and isinstance(parsed.get('trivial'), bool):
                        # Normalize to 'result' key
                        parsed['result'] = parsed.pop('trivial')
                        logger.info(f"[TrivialFilterAnalyzer] Found verdict dict with trivial (normalized to result={parsed['result']})")
                        return json.dumps(parsed)
                        
            except json.JSONDecodeError:
                continue
        
        # Fallback: check for arrays containing verdict dicts
        array_candidates = self._find_all_json_arrays(content)
        for candidate in array_candidates:
            try:
                parsed = json.loads(candidate)
                if isinstance(parsed, list) and len(parsed) > 0:
                    first_item = parsed[0]
                    if isinstance(first_item, dict) and 'result' in first_item:
                        logger.info("[TrivialFilterAnalyzer] Found verdict in array, extracting first item")
                        return json.dumps(first_item)
            except json.JSONDecodeError:
                continue
        
        logger.warning("[TrivialFilterAnalyzer] No valid verdict dict found")
        return None
    
    def validate_json(self, parsed_json: Any) -> bool:
        """
        Validate trivial filter verdict structure.
        
        A valid verdict must be a dict with 'result' key containing a boolean.
        
        Args:
            parsed_json: Parsed JSON value
            
        Returns:
            True if valid verdict dict, False otherwise
        """
        if not isinstance(parsed_json, dict):
            return False
        
        # Must have 'result' key with boolean value
        if 'result' not in parsed_json:
            return False
        
        if not isinstance(parsed_json.get('result'), bool):
            return False
        
        return True
    
    def get_fallback_guidance(self) -> str:
        """
        Get trivial filter-specific guidance for JSON output.
        
        Returns:
            Guidance message for producing a verdict dict
        """
        return (
            "CRITICAL: Your previous response did not contain a valid verdict. "
            "You MUST respond with ONLY a valid JSON object containing your decision. "
            "Your response MUST be a JSON object with 'result' (boolean) key. "
            "Example for trivial issue: {\"result\": true} "
            "Example for non-trivial issue: {\"result\": false} "
            "No markdown, no prose, no explanatory text. Return the JSON object now."
        )
