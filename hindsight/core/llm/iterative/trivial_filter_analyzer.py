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
    
    def get_fallback_guidance(self, validation_reason: Optional[str] = None) -> str:
        """
        Get trivial filter-specific guidance for JSON output.

        Args:
            validation_reason: Optional description of what was wrong with the
                previous response.

        Returns:
            Guidance message that includes the canonical schema and CORRECT
            examples for both verdicts.
        """
        reason_block = (
            f"Why your previous response was rejected: {validation_reason}.\n\n"
            if validation_reason
            else ""
        )
        return (
            "CRITICAL: Your previous response did not contain a valid verdict.\n\n"
            f"{reason_block}"
            "You MUST respond with ONLY a valid JSON OBJECT representing your decision. "
            "It MUST contain a boolean `result`.\n\n"
            "### Required schema\n"
            "```json\n"
            '{ "result": true }\n'
            "```\n\n"
            "Semantics:\n"
            "- `result: true`  → the issue is TRIVIAL (filter it out).\n"
            "- `result: false` → the issue is NOT trivial (keep it for further analysis).\n\n"
            "### CORRECT example (trivial)\n"
            "```json\n"
            '{"result": true}\n'
            "```\n\n"
            "### CORRECT example (not trivial)\n"
            "```json\n"
            '{"result": false}\n'
            "```\n\n"
            "Your response MUST start with `{` and end with `}`. "
            "Return JSON ONLY — no markdown fences, no prose."
        )
