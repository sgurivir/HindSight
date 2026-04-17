#!/usr/bin/env python3
"""
Tests for ContextCollectionAnalyzer.

Verifies that the analyzer correctly extracts dict with 'primary_function' key
from LLM responses, even when the response contains other JSON structures
like collection_notes arrays.
"""

import json
import pytest
from unittest.mock import Mock, MagicMock

from hindsight.core.llm.iterative.context_collection_analyzer import ContextCollectionAnalyzer


class TestContextCollectionAnalyzerExtractJson:
    """Tests for the extract_json method."""

    def setup_method(self):
        """Set up test fixtures."""
        # Create mock Claude client
        self.mock_claude = Mock()
        
        self.analyzer = ContextCollectionAnalyzer(claude=self.mock_claude)

    def test_extract_json_with_primary_function_dict(self):
        """Test extraction of dict with primary_function key."""
        response = '''
        Here is the context bundle:
        
        {
            "primary_function": {
                "name": "processData",
                "file_path": "src/processor.py",
                "code": "def processData(): pass"
            },
            "related_functions": [],
            "collection_notes": ["Note 1", "Note 2"]
        }
        '''
        
        result = self.analyzer.extract_json(response)
        
        assert result is not None
        parsed = json.loads(result)
        assert isinstance(parsed, dict)
        assert "primary_function" in parsed
        assert parsed["primary_function"]["name"] == "processData"

    def test_extract_json_prefers_primary_function_over_collection_notes(self):
        """
        Test that extract_json returns dict with primary_function, NOT the collection_notes array.
        
        This is the critical bug fix: clean_json_response() would return the LAST valid JSON,
        which could be the collection_notes array. Our analyzer must return the context bundle dict.
        """
        # Simulate LLM response with both context bundle and collection_notes as separate JSON
        response = '''
        Here is the context bundle:
        
        {
            "primary_function": {
                "name": "handleRequest",
                "file_path": "api/handler.py"
            },
            "related_functions": [
                {"name": "validateInput", "file_path": "api/validator.py"}
            ]
        }
        
        And here are my collection notes:
        
        ["I read the handler file", "I found the validator", "Analysis complete"]
        '''
        
        result = self.analyzer.extract_json(response)
        
        # Must return the dict with primary_function, NOT the array
        assert result is not None
        parsed = json.loads(result)
        assert isinstance(parsed, dict)
        assert "primary_function" in parsed
        assert parsed["primary_function"]["name"] == "handleRequest"

    def test_extract_json_with_markdown_code_block(self):
        """Test extraction from markdown code blocks."""
        response = '''
        ```json
        {
            "primary_function": {
                "name": "calculateTotal",
                "file_path": "math/calc.py"
            }
        }
        ```
        '''
        
        result = self.analyzer.extract_json(response)
        
        assert result is not None
        parsed = json.loads(result)
        assert isinstance(parsed, dict)
        assert "primary_function" in parsed

    def test_extract_json_returns_none_for_array_only(self):
        """Test that extract_json returns None when only an array is present (no primary_function)."""
        response = '''
        Here are the notes:
        
        ["Note 1", "Note 2", "Note 3"]
        '''
        
        result = self.analyzer.extract_json(response)
        
        # Should return None because there's no dict with primary_function
        assert result is None

    def test_extract_json_uses_fallback_for_wrong_dict(self):
        """Test that extract_json uses fallback behavior for dict without primary_function key.
        
        The analyzer now uses fallback behavior to improve robustness when LLMs return
        slightly malformed responses. It returns the dict with a warning logged.
        """
        response = '''
        {
            "some_other_key": "value",
            "another_key": 123
        }
        '''
        
        result = self.analyzer.extract_json(response)
        
        # Fallback behavior: returns the dict even without 'primary_function'
        # This improves robustness when LLMs return partial/malformed responses
        assert result is not None
        parsed = json.loads(result)
        assert isinstance(parsed, dict)
        assert "some_other_key" in parsed

    def test_extract_json_handles_nested_json(self):
        """Test extraction with deeply nested JSON structures."""
        response = '''
        {
            "primary_function": {
                "name": "deepFunction",
                "file_path": "deep/nested/path.py",
                "metadata": {
                    "complexity": "high",
                    "dependencies": ["dep1", "dep2"]
                }
            },
            "related_functions": [
                {
                    "name": "helper1",
                    "calls": [{"target": "helper2", "line": 10}]
                }
            ]
        }
        '''
        
        result = self.analyzer.extract_json(response)
        
        assert result is not None
        parsed = json.loads(result)
        assert isinstance(parsed, dict)
        assert "primary_function" in parsed
        assert parsed["primary_function"]["metadata"]["complexity"] == "high"


class TestContextCollectionAnalyzerValidateJson:
    """Tests for the validate_json method."""

    def setup_method(self):
        """Set up test fixtures."""
        self.mock_claude = Mock()
        
        self.analyzer = ContextCollectionAnalyzer(claude=self.mock_claude)

    def test_validate_json_accepts_valid_context_bundle(self):
        """Test that valid context bundle passes validation."""
        context_bundle = {
            "primary_function": {
                "name": "testFunc",
                "file_path": "test.py"
            }
        }
        
        assert self.analyzer.validate_json(context_bundle) is True

    def test_validate_json_rejects_array(self):
        """Test that arrays are rejected."""
        array_data = ["item1", "item2"]
        
        assert self.analyzer.validate_json(array_data) is False

    def test_validate_json_rejects_dict_without_primary_function(self):
        """Test that dicts without primary_function are rejected."""
        wrong_dict = {
            "some_key": "value"
        }
        
        assert self.analyzer.validate_json(wrong_dict) is False

    def test_validate_json_rejects_none(self):
        """Test that None is rejected."""
        assert self.analyzer.validate_json(None) is False

    def test_validate_json_rejects_string(self):
        """Test that strings are rejected."""
        assert self.analyzer.validate_json("not a dict") is False


class TestContextCollectionAnalyzerGetFallbackGuidance:
    """Tests for the get_fallback_guidance method."""

    def setup_method(self):
        """Set up test fixtures."""
        self.mock_claude = Mock()
        
        self.analyzer = ContextCollectionAnalyzer(claude=self.mock_claude)

    def test_fallback_guidance_mentions_primary_function(self):
        """Test that fallback guidance mentions the required structure."""
        guidance = self.analyzer.get_fallback_guidance()
        
        assert "primary_function" in guidance
        assert "JSON" in guidance

    def test_fallback_guidance_mentions_object_not_array(self):
        """Test that fallback guidance clarifies object vs array."""
        guidance = self.analyzer.get_fallback_guidance()
        
        # Should mention that we need an object, not an array
        assert "object" in guidance.lower() or "{" in guidance
