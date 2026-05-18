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

    def get_fallback_guidance(self) -> str:
        return (
            "CRITICAL: Your previous response did not contain a valid verdict. "
            "Respond with ONLY a JSON object containing at minimum a 'valid' boolean. "
            "Include 'low_confidence': true if you lack context to judge, and a 'reason' string. "
            "Example: {\"valid\": true, \"low_confidence\": true, \"reason\": \"...\"}. "
            "No markdown, no prose. Return the JSON object now."
        )
