#!/usr/bin/env python3
"""
Trace Solution Validator Analyzer (Stage C)

Stage-specific iterative analyzer for the Stage C solution validator.
Expects: dict with 'valid' (bool) key; optional 'low_confidence' (bool) and 'reason' (str).
"""

import json
from typing import Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from ..llm import Claude

from .base_iterative_analyzer import BaseIterativeAnalyzer
from ....utils.log_util import get_logger

logger = get_logger(__name__)


class TraceSolutionValidatorAnalyzer(BaseIterativeAnalyzer):
    """
    Iterative analyzer for Stage C solution validation.

    Expected output: JSON object with 'valid' (bool) key. May also include
    'low_confidence' (bool) and 'reason' (str).
    """

    def __init__(self, claude: "Claude"):
        super().__init__(claude)

    def extract_json(self, content: str) -> Optional[str]:
        """Extract the validator verdict dict from LLM response."""
        candidates = self._find_all_json_objects(content)

        for candidate in candidates:
            try:
                parsed = json.loads(candidate)
                if not isinstance(parsed, dict):
                    continue
                if 'tool' in parsed:
                    continue
                if 'valid' in parsed and isinstance(parsed.get('valid'), bool):
                    logger.info(
                        f"[TraceSolutionValidatorAnalyzer] Found verdict dict "
                        f"valid={parsed['valid']} low_confidence={parsed.get('low_confidence', False)}"
                    )
                    return candidate
            except json.JSONDecodeError:
                continue

        logger.warning("[TraceSolutionValidatorAnalyzer] No valid verdict dict found")
        return None

    def validate_json(self, parsed_json: Any) -> bool:
        """A verdict must be a dict with a boolean 'valid' key."""
        if not isinstance(parsed_json, dict):
            return False
        if 'valid' not in parsed_json:
            return False
        return isinstance(parsed_json.get('valid'), bool)

    def get_fallback_guidance(self, validation_reason: Optional[str] = None) -> str:
        reason_block = (
            f"Why your previous response was rejected: {validation_reason}.\n\n"
            if validation_reason
            else ""
        )
        return (
            "CRITICAL: Your previous response did not contain a valid verdict.\n\n"
            f"{reason_block}"
            "You MUST respond with ONLY a valid JSON OBJECT containing at minimum a "
            "boolean `valid`. Include `low_confidence: true` if you lack context to judge, "
            "and a string `reason`.\n\n"
            "### Required schema\n"
            "```json\n"
            "{\n"
            '  "valid": true,\n'
            '  "low_confidence": false,\n'
            '  "reason": "string — brief explanation"\n'
            "}\n"
            "```\n\n"
            "### CORRECT example (confident, valid)\n"
            "```json\n"
            '{"valid": true, "low_confidence": false, '
            '"reason": "The fix is safe; no lifetime, threading, or memory invariants are broken."}\n'
            "```\n\n"
            "### CORRECT example (low confidence)\n"
            "```json\n"
            '{"valid": true, "low_confidence": true, '
            '"reason": "Could not locate the kMTimeModificationPeriod constant; verdict is provisional."}\n'
            "```\n\n"
            "Your response MUST start with `{` and end with `}`. "
            "Return JSON ONLY — no markdown fences, no prose."
        )
