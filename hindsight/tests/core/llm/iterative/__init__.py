#!/usr/bin/env python3
"""
Tests for stage-isolated iterative analyzers.

These tests verify that each analyzer correctly extracts the expected JSON structure
from LLM responses, addressing the root cause bug where clean_json_response() returns
the LAST valid JSON candidate instead of the CORRECT structure for each stage.
"""
