#!/usr/bin/env python3
"""
Tests for DiffAnalysisAnalyzer.

Verifies that the analyzer correctly extracts array of issue dicts from LLM responses
during diff analysis, distinguishing between arrays of issue objects and arrays of strings.
"""

import json
import pytest
from unittest.mock import Mock, MagicMock

from hindsight.core.llm.iterative.diff_analysis_analyzer import DiffAnalysisAnalyzer


class TestDiffAnalysisAnalyzerExtractJson:
    """Tests for the extract_json method."""

    def setup_method(self):
        """Set up test fixtures."""
        self.mock_claude = Mock()
        
        self.analyzer = DiffAnalysisAnalyzer(claude=self.mock_claude)

    def test_extract_json_with_issue_array(self):
        """Test extraction of array containing issue dicts."""
        response = '''
        Here are the issues found in the diff:
        
        [
            {
                "issue_type": "null_check_missing",
                "severity": "high",
                "file_path": "api/handler.py",
                "line_number": 42,
                "description": "Added code doesn't check for null before dereferencing"
            },
            {
                "issue_type": "error_handling",
                "severity": "medium",
                "file_path": "api/handler.py",
                "line_number": 55,
                "description": "Exception not properly caught"
            }
        ]
        '''
        
        result = self.analyzer.extract_json(response)
        
        assert result is not None
        parsed = json.loads(result)
        assert isinstance(parsed, list)
        assert len(parsed) == 2
        assert all(isinstance(item, dict) for item in parsed)
        assert parsed[0]["issue_type"] == "null_check_missing"

    def test_extract_json_ignores_string_array(self):
        """
        Test that extract_json ignores arrays of strings.
        
        This is the critical bug fix: clean_json_response() would return the LAST valid JSON,
        which could be a collection_notes array of strings. Our analyzer must skip string arrays.
        """
        response = '''
        Here are my analysis notes:
        
        ["Analyzed the diff", "Checked for issues", "Review complete"]
        
        And here are the actual issues:
        
        [
            {
                "issue_type": "security_vulnerability",
                "severity": "critical",
                "description": "SQL injection in new query"
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
        assert parsed[0]["issue_type"] == "security_vulnerability"

    def test_extract_json_prefers_issue_array_over_string_array_reversed_order(self):
        """
        Test extraction when string array comes AFTER issue array.
        
        This tests the scenario where clean_json_response() would return the string array
        because it's the LAST valid JSON in the response.
        """
        response = '''
        [
            {
                "issue_type": "concurrency_bug",
                "severity": "high",
                "description": "Race condition introduced in new code"
            }
        ]
        
        Analysis notes for reference:
        
        ["Note 1", "Note 2", "Note 3"]
        '''
        
        result = self.analyzer.extract_json(response)
        
        # Must return the issue array, NOT the string array (even though string array is last)
        assert result is not None
        parsed = json.loads(result)
        assert isinstance(parsed, list)
        assert len(parsed) == 1
        assert isinstance(parsed[0], dict)
        assert parsed[0]["issue_type"] == "concurrency_bug"

    def test_extract_json_with_empty_array(self):
        """Test extraction of empty array (no issues found in diff)."""
        response = '''
        After analyzing the diff, no issues were found.
        
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
                "issue_type": "logic_error",
                "severity": "medium",
                "line_number": 100
            }
        ]
        ```
        '''
        
        result = self.analyzer.extract_json(response)
        
        assert result is not None
        parsed = json.loads(result)
        assert isinstance(parsed, list)
        assert len(parsed) == 1

    def test_extract_json_extracts_nested_array_from_dict(self):
        """Test that extract_json extracts nested arrays from dicts."""
        response = '''
        {
            "changed_functions": [
                {"name": "test"}
            ]
        }
        '''
        
        result = self.analyzer.extract_json(response)
        
        # DiffAnalysisAnalyzer uses _find_all_json_arrays() which finds ALL arrays
        # in the content, including nested ones. The array [{"name": "test"}] is
        # found and extracted because it's an array of dicts (valid issue format).
        assert result is not None, "Should extract nested array"
        parsed = json.loads(result)
        assert isinstance(parsed, list)
        assert len(parsed) == 1
        assert parsed[0]["name"] == "test"

    def test_extract_json_handles_diff_specific_issues(self):
        """Test extraction with diff-specific issue fields."""
        response = '''
        [
            {
                "issue_type": "breaking_change",
                "severity": "critical",
                "file_path": "api/v2/endpoint.py",
                "line_number": 25,
                "description": "API signature changed without version bump",
                "diff_context": {
                    "added_lines": ["+def process(data, new_param):"],
                    "removed_lines": ["-def process(data):"]
                }
            }
        ]
        '''
        
        result = self.analyzer.extract_json(response)
        
        assert result is not None
        parsed = json.loads(result)
        assert isinstance(parsed, list)
        assert len(parsed) == 1
        assert parsed[0]["issue_type"] == "breaking_change"
        assert "diff_context" in parsed[0]


class TestDiffAnalysisAnalyzerValidateJson:
    """Tests for the validate_json method."""

    def setup_method(self):
        """Set up test fixtures."""
        self.mock_claude = Mock()
        
        self.analyzer = DiffAnalysisAnalyzer(claude=self.mock_claude)

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
        diff_context = {
            "changed_functions": [{"name": "test"}]
        }
        
        assert self.analyzer.validate_json(diff_context) is False

    def test_validate_json_rejects_none(self):
        """Test that None is rejected."""
        assert self.analyzer.validate_json(None) is False

    def test_validate_json_rejects_string(self):
        """Test that strings are rejected."""
        assert self.analyzer.validate_json("not an array") is False


class TestDiffAnalysisAnalyzerGetFallbackGuidance:
    """Tests for the get_fallback_guidance method."""

    def setup_method(self):
        """Set up test fixtures."""
        self.mock_claude = Mock()
        
        self.analyzer = DiffAnalysisAnalyzer(claude=self.mock_claude)

    def test_fallback_guidance_mentions_array(self):
        """Test that fallback guidance mentions the required array structure."""
        guidance = self.analyzer.get_fallback_guidance()
        
        assert "array" in guidance.lower() or "[" in guidance

    def test_fallback_guidance_mentions_issue_objects(self):
        """Test that fallback guidance clarifies issue objects."""
        guidance = self.analyzer.get_fallback_guidance()
        
        # Should mention that we need issue objects, not strings
        assert "issue" in guidance.lower() or "object" in guidance.lower()
