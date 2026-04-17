#!/usr/bin/env python3
"""
Tests for CodeAnalysisAnalyzer.

Verifies that the analyzer correctly extracts array of issue dicts from LLM responses,
distinguishing between arrays of issue objects and arrays of strings (like collection_notes).
"""

import json
import pytest
from unittest.mock import Mock, MagicMock

from hindsight.core.llm.iterative.code_analysis_analyzer import CodeAnalysisAnalyzer


class TestCodeAnalysisAnalyzerExtractJson:
    """Tests for the extract_json method."""

    def setup_method(self):
        """Set up test fixtures."""
        self.mock_claude = Mock()
        
        self.analyzer = CodeAnalysisAnalyzer(claude=self.mock_claude)

    def test_extract_json_with_issue_array(self):
        """Test extraction of array containing issue dicts."""
        response = '''
        Here are the issues I found:
        
        [
            {
                "issue_type": "null_pointer",
                "severity": "high",
                "file_path": "src/handler.py",
                "line_number": 42,
                "description": "Potential null pointer dereference"
            },
            {
                "issue_type": "resource_leak",
                "severity": "medium",
                "file_path": "src/handler.py",
                "line_number": 55,
                "description": "File handle not closed"
            }
        ]
        '''
        
        result = self.analyzer.extract_json(response)
        
        assert result is not None
        parsed = json.loads(result)
        assert isinstance(parsed, list)
        assert len(parsed) == 2
        assert all(isinstance(item, dict) for item in parsed)
        assert parsed[0]["issue_type"] == "null_pointer"

    def test_extract_json_ignores_string_array(self):
        """
        Test that extract_json ignores arrays of strings (like collection_notes).
        
        This is the critical bug fix: clean_json_response() would return the LAST valid JSON,
        which could be a collection_notes array of strings. Our analyzer must skip string arrays.
        """
        response = '''
        Here are my notes:
        
        ["I analyzed the code", "Found some issues", "Analysis complete"]
        
        And here are the actual issues:
        
        [
            {
                "issue_type": "buffer_overflow",
                "severity": "critical",
                "description": "Buffer overflow in memcpy"
            }
        ]
        '''
        
        result = self.analyzer.extract_json(response)
        
        # Must return the array of issue dicts, NOT the array of strings
        assert result is not None
        parsed = json.loads(result)
        assert isinstance(parsed, list)
        assert len(parsed) == 1
        assert isinstance(parsed[0], dict)
        assert parsed[0]["issue_type"] == "buffer_overflow"

    def test_extract_json_prefers_issue_array_over_string_array_reversed_order(self):
        """
        Test extraction when string array comes AFTER issue array.
        
        This tests the scenario where clean_json_response() would return the string array
        because it's the LAST valid JSON in the response.
        """
        response = '''
        [
            {
                "issue_type": "race_condition",
                "severity": "high",
                "description": "Race condition in thread pool"
            }
        ]
        
        Collection notes for reference:
        
        ["Note 1", "Note 2", "Note 3"]
        '''
        
        result = self.analyzer.extract_json(response)
        
        # Must return the issue array, NOT the string array (even though string array is last)
        assert result is not None
        parsed = json.loads(result)
        assert isinstance(parsed, list)
        assert len(parsed) == 1
        assert isinstance(parsed[0], dict)
        assert parsed[0]["issue_type"] == "race_condition"

    def test_extract_json_with_empty_array(self):
        """Test extraction of empty array (no issues found)."""
        response = '''
        After thorough analysis, no issues were found.
        
        []
        '''
        
        result = self.analyzer.extract_json(response)
        
        assert result is not None
        parsed = json.loads(result)
        assert isinstance(parsed, list)
        assert len(parsed) == 0

    def test_extract_json_with_markdown_code_block(self):
        """Test extraction from markdown code blocks."""
        response = '''
        ```json
        [
            {
                "issue_type": "memory_leak",
                "severity": "medium"
            }
        ]
        ```
        '''
        
        result = self.analyzer.extract_json(response)
        
        assert result is not None
        parsed = json.loads(result)
        assert isinstance(parsed, list)
        assert len(parsed) == 1

    def test_extract_json_returns_none_for_dict_only(self):
        """Test that extract_json returns None when only a dict is present (not an array)."""
        response = '''
        {
            "primary_function": {
                "name": "test"
            }
        }
        '''
        
        result = self.analyzer.extract_json(response)
        
        # Should return None because we need an array of issues, not a dict
        assert result is None

    def test_extract_json_handles_mixed_array(self):
        """Test extraction with array containing both dicts and other types."""
        # This is an edge case - if the array has at least one dict, we accept it
        response = '''
        [
            {
                "issue_type": "type_error",
                "severity": "low"
            },
            "some string that shouldn't be here"
        ]
        '''
        
        result = self.analyzer.extract_json(response)
        
        # Should still extract because there's at least one dict
        assert result is not None
        parsed = json.loads(result)
        assert isinstance(parsed, list)


class TestCodeAnalysisAnalyzerValidateJson:
    """Tests for the validate_json method."""

    def setup_method(self):
        """Set up test fixtures."""
        self.mock_claude = Mock()
        
        self.analyzer = CodeAnalysisAnalyzer(claude=self.mock_claude)

    def test_validate_json_accepts_array_of_dicts(self):
        """Test that array of issue dicts passes validation."""
        issues = [
            {"issue_type": "bug", "severity": "high"},
            {"issue_type": "warning", "severity": "low"}
        ]
        
        assert self.analyzer.validate_json(issues) is True

    def test_validate_json_accepts_empty_array(self):
        """Test that empty array passes validation (no issues found)."""
        assert self.analyzer.validate_json([]) is True

    def test_validate_json_rejects_array_of_strings(self):
        """Test that arrays of strings are rejected."""
        string_array = ["note1", "note2", "note3"]
        
        assert self.analyzer.validate_json(string_array) is False

    def test_validate_json_rejects_dict(self):
        """Test that dicts are rejected (we need an array)."""
        context_bundle = {
            "primary_function": {"name": "test"}
        }
        
        assert self.analyzer.validate_json(context_bundle) is False

    def test_validate_json_rejects_none(self):
        """Test that None is rejected."""
        assert self.analyzer.validate_json(None) is False

    def test_validate_json_rejects_string(self):
        """Test that strings are rejected."""
        assert self.analyzer.validate_json("not an array") is False


class TestCodeAnalysisAnalyzerGetFallbackGuidance:
    """Tests for the get_fallback_guidance method."""

    def setup_method(self):
        """Set up test fixtures."""
        self.mock_claude = Mock()
        
        self.analyzer = CodeAnalysisAnalyzer(claude=self.mock_claude)

    def test_fallback_guidance_mentions_array(self):
        """Test that fallback guidance mentions the required array structure."""
        guidance = self.analyzer.get_fallback_guidance()
        
        assert "array" in guidance.lower() or "[" in guidance

    def test_fallback_guidance_mentions_issue_objects(self):
        """Test that fallback guidance clarifies issue objects."""
        guidance = self.analyzer.get_fallback_guidance()
        
        # Should mention that we need issue objects, not strings
        assert "issue" in guidance.lower() or "object" in guidance.lower()
