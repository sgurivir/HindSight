#!/usr/bin/env python3
"""
Tests for DiffContextAnalyzer.

Verifies that the analyzer correctly extracts dict with 'changed_functions' key
from LLM responses, even when the response contains other JSON structures.
"""

import json
import pytest
from unittest.mock import Mock, MagicMock

from hindsight.core.llm.iterative.diff_context_analyzer import DiffContextAnalyzer


class TestDiffContextAnalyzerExtractJson:
    """Tests for the extract_json method."""

    def setup_method(self):
        """Set up test fixtures."""
        self.mock_claude = Mock()
        
        self.analyzer = DiffContextAnalyzer(claude=self.mock_claude)

    def test_extract_json_with_changed_functions_dict(self):
        """Test extraction of dict with changed_functions key."""
        response = '''
        Here is the diff context bundle:
        
        {
            "changed_functions": [
                {
                    "name": "handleRequest",
                    "file_path": "api/handler.py",
                    "change_type": "modified"
                }
            ],
            "affected_callers": [],
            "collection_notes": ["Note 1", "Note 2"]
        }
        '''
        
        result = self.analyzer.extract_json(response)
        
        assert result is not None
        parsed = json.loads(result)
        assert isinstance(parsed, dict)
        assert "changed_functions" in parsed
        assert len(parsed["changed_functions"]) == 1
        assert parsed["changed_functions"][0]["name"] == "handleRequest"

    def test_extract_json_prefers_changed_functions_over_collection_notes(self):
        """
        Test that extract_json returns dict with changed_functions, NOT the collection_notes array.
        
        This is the critical bug fix: clean_json_response() would return the LAST valid JSON,
        which could be the collection_notes array. Our analyzer must return the diff context bundle dict.
        """
        response = '''
        Here is the diff context bundle:
        
        {
            "changed_functions": [
                {"name": "processData", "file_path": "processor.py"}
            ],
            "affected_callers": [
                {"name": "main", "file_path": "main.py"}
            ]
        }
        
        And here are my collection notes:
        
        ["I read the diff", "Found modified functions", "Analysis complete"]
        '''
        
        result = self.analyzer.extract_json(response)
        
        # Must return the dict with changed_functions, NOT the array
        assert result is not None
        parsed = json.loads(result)
        assert isinstance(parsed, dict)
        assert "changed_functions" in parsed
        assert parsed["changed_functions"][0]["name"] == "processData"

    def test_extract_json_with_markdown_code_block(self):
        """Test extraction from markdown code blocks."""
        response = '''
        ```json
        {
            "changed_functions": [
                {"name": "updateConfig", "file_path": "config.py"}
            ]
        }
        ```
        '''
        
        result = self.analyzer.extract_json(response)
        
        assert result is not None
        parsed = json.loads(result)
        assert isinstance(parsed, dict)
        assert "changed_functions" in parsed

    def test_extract_json_returns_none_for_array_only(self):
        """Test that extract_json returns None when only an array is present."""
        response = '''
        Here are the notes:
        
        ["Note 1", "Note 2", "Note 3"]
        '''
        
        result = self.analyzer.extract_json(response)
        
        # Should return None because there's no dict with changed_functions
        assert result is None

    def test_extract_json_accepts_primary_function_as_alternative(self):
        """Test that extract_json accepts dict with primary_function key as alternative structure.
        
        DiffContextAnalyzer accepts both 'changed_functions' and 'primary_function' keys
        as valid diff context bundle structures.
        """
        response = '''
        {
            "primary_function": {"name": "test"},
            "some_other_key": "value"
        }
        '''
        
        result = self.analyzer.extract_json(response)
        
        # Should return the dict because 'primary_function' is an accepted alternative key
        assert result is not None
        parsed = json.loads(result)
        assert isinstance(parsed, dict)
        assert "primary_function" in parsed

    def test_extract_json_handles_complex_diff_context(self):
        """Test extraction with complex diff context structure."""
        response = '''
        {
            "changed_functions": [
                {
                    "name": "handleWebhook",
                    "file_path": "webhooks/handler.py",
                    "change_type": "modified",
                    "diff_lines": ["+    new_code()", "-    old_code()"]
                },
                {
                    "name": "validatePayload",
                    "file_path": "webhooks/validator.py",
                    "change_type": "added"
                }
            ],
            "affected_callers": [
                {
                    "name": "processEvent",
                    "file_path": "events/processor.py",
                    "calls_to": ["handleWebhook"]
                }
            ],
            "affected_callees": [
                {
                    "name": "sendNotification",
                    "file_path": "notifications/sender.py"
                }
            ]
        }
        '''
        
        result = self.analyzer.extract_json(response)
        
        assert result is not None
        parsed = json.loads(result)
        assert isinstance(parsed, dict)
        assert "changed_functions" in parsed
        assert len(parsed["changed_functions"]) == 2
        assert "affected_callers" in parsed
        assert "affected_callees" in parsed


class TestDiffContextAnalyzerValidateJson:
    """Tests for the validate_json method."""

    def setup_method(self):
        """Set up test fixtures."""
        self.mock_claude = Mock()
        
        self.analyzer = DiffContextAnalyzer(claude=self.mock_claude)

    def test_validate_json_accepts_valid_diff_context(self):
        """Test that valid diff context bundle passes validation."""
        diff_context = {
            "changed_functions": [
                {"name": "testFunc", "file_path": "test.py"}
            ]
        }
        
        assert self.analyzer.validate_json(diff_context) is True

    def test_validate_json_accepts_empty_changed_functions(self):
        """Test that empty changed_functions array passes validation."""
        diff_context = {
            "changed_functions": []
        }
        
        assert self.analyzer.validate_json(diff_context) is True

    def test_validate_json_rejects_array(self):
        """Test that arrays are rejected."""
        array_data = ["item1", "item2"]
        
        assert self.analyzer.validate_json(array_data) is False

    def test_validate_json_accepts_primary_function_as_alternative(self):
        """Test that dicts with primary_function key are accepted as alternative structure."""
        alt_dict = {
            "primary_function": {"name": "test"}
        }
        
        # primary_function is an accepted alternative key for diff context bundles
        assert self.analyzer.validate_json(alt_dict) is True

    def test_validate_json_rejects_dict_without_expected_keys(self):
        """Test that dicts without changed_functions or primary_function are rejected."""
        wrong_dict = {
            "some_random_key": {"name": "test"},
            "another_key": "value"
        }
        
        assert self.analyzer.validate_json(wrong_dict) is False

    def test_validate_json_rejects_none(self):
        """Test that None is rejected."""
        assert self.analyzer.validate_json(None) is False

    def test_validate_json_rejects_string(self):
        """Test that strings are rejected."""
        assert self.analyzer.validate_json("not a dict") is False


class TestDiffContextAnalyzerGetFallbackGuidance:
    """Tests for the get_fallback_guidance method."""

    def setup_method(self):
        """Set up test fixtures."""
        self.mock_claude = Mock()
        
        self.analyzer = DiffContextAnalyzer(claude=self.mock_claude)

    def test_fallback_guidance_mentions_changed_functions(self):
        """Test that fallback guidance mentions the required structure."""
        guidance = self.analyzer.get_fallback_guidance()
        
        assert "changed_functions" in guidance
        assert "JSON" in guidance

    def test_fallback_guidance_mentions_object_not_array(self):
        """Test that fallback guidance clarifies object vs array."""
        guidance = self.analyzer.get_fallback_guidance()
        
        # Should mention that we need an object, not an array
        assert "object" in guidance.lower() or "{" in guidance
