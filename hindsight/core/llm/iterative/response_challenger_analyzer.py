#!/usr/bin/env python3
"""
Response Challenger Analyzer (Level 3 Filtering)

Stage-specific iterative analyzer for Response Challenger.
Expects: dict with 'result' (boolean) and 'reason' (string) keys

This analyzer validates issues by having an LLM act as a senior software engineer
to verify if issues are legitimate bugs/optimizations worth pursuing.
"""

import json
from typing import Optional, Any, TYPE_CHECKING

if TYPE_CHECKING:
    from ..llm import Claude

from .base_iterative_analyzer import BaseIterativeAnalyzer
from ....utils.log_util import get_logger

logger = get_logger(__name__)


class ResponseChallengerAnalyzer(BaseIterativeAnalyzer):
    """
    Iterative analyzer for Response Challenger (Level 3 Filtering).
    
    Expected output: JSON object with 'result' (boolean) and 'reason' (string) keys
    
    The 'result' field indicates whether the issue should be filtered out:
    - result: true  -> Issue is a false positive or not worth pursuing (filter out)
    - result: false -> Issue is legitimate and worth fixing (keep)
    
    The 'reason' field provides the explanation for the decision.
    """
    
    def __init__(self, claude: 'Claude'):
        """
        Initialize the Response Challenger analyzer.
        
        Args:
            claude: Claude client instance for LLM communication
        """
        super().__init__(claude)
    
    def extract_json(self, content: str) -> Optional[str]:
        """
        Extract response challenger verdict JSON from LLM response.
        
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
                        logger.info(f"[ResponseChallengerAnalyzer] Found verdict dict with result={parsed['result']}")
                        return candidate
                    
                    # Check for common variations
                    if 'is_trivial' in parsed and isinstance(parsed.get('is_trivial'), bool):
                        # Normalize to 'result' key
                        parsed['result'] = parsed.pop('is_trivial')
                        logger.info(f"[ResponseChallengerAnalyzer] Found verdict dict with is_trivial (normalized to result={parsed['result']})")
                        return json.dumps(parsed)
                    
                    if 'should_filter' in parsed and isinstance(parsed.get('should_filter'), bool):
                        # Normalize to 'result' key
                        parsed['result'] = parsed.pop('should_filter')
                        logger.info(f"[ResponseChallengerAnalyzer] Found verdict dict with should_filter (normalized to result={parsed['result']})")
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
                        logger.info("[ResponseChallengerAnalyzer] Found verdict in array, extracting first item")
                        return json.dumps(first_item)
            except json.JSONDecodeError:
                continue
        
        logger.warning("[ResponseChallengerAnalyzer] No valid verdict dict found")
        return None
    
    def validate_json(self, parsed_json: Any) -> bool:
        """
        Validate response challenger verdict structure.
        
        A valid verdict must be a dict with 'result' key containing a boolean.
        The 'reason' key is optional but recommended.
        
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
        Get response challenger-specific guidance for JSON output.

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
            "It MUST contain a boolean `result` and a string `reason`.\n\n"
            "### Required schema\n"
            "```json\n"
            "{\n"
            '  "result": true,\n'
            '  "reason": "string — brief justification (one or two sentences)"\n'
            "}\n"
            "```\n\n"
            "Semantics:\n"
            "- `result: true`  → filter out the issue (false positive / not worth fixing).\n"
            "- `result: false` → keep the issue (legitimate, actionable bug or optimization).\n\n"
            "### CORRECT example (filter out)\n"
            "```json\n"
            '{"result": true, "reason": "This is a false positive: the value is validated by the caller."}\n'
            "```\n\n"
            "### CORRECT example (keep)\n"
            "```json\n"
            '{"result": false, "reason": "This is a real concurrency bug — the lock is released before mutation completes."}\n'
            "```\n\n"
            "Your response MUST start with `{` and end with `}`. "
            "Return JSON ONLY — no markdown fences, no prose."
        )
