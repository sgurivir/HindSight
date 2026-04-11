#!/usr/bin/env python3
"""
Tests for hindsight.core.schema.code_analysis_result_schema module.
"""

import os
import sys
import json
from pathlib import Path
from datetime import datetime

import pytest

# Add project root to path
project_root = Path(__file__).parent.parent.parent.parent.parent
sys.path.insert(0, str(project_root))

from hindsight.core.schema.code_analysis_result_schema import (
    CodeAnalysisIssue,
    CodeAnalysisResult,
    CodeAnalysisResultValidator,
    create_result,
    create_issue,
    validate_result_format
)


class TestCodeAnalysisIssue:
    """Tests for CodeAnalysisIssue dataclass."""

    def test_init_required_fields(self):
        """Test initialization with required fields only."""
        issue = CodeAnalysisIssue(
            issue='Test bug',
            severity='high',
            category='logicBug'
        )
        
        assert issue.issue == 'Test bug'
        assert issue.severity == 'high'
        assert issue.category == 'logicBug'

    def test_init_all_fields(self):
        """Test initialization with all fields."""
        issue = CodeAnalysisIssue(
            issue='Test bug',
            severity='high',
            category='logicBug',
            file_path='src/Example.java',
            function_name='testFunc',
            line_number=42,
            description='Detailed description',
            suggestion='Fix suggestion',
            confidence=0.95,
            rule_id='RULE001',
            external_references=['https://example.com'],
            evidence='Validation evidence'
        )
        
        assert issue.file_path == 'src/Example.java'
        assert issue.function_name == 'testFunc'
        assert issue.line_number == 42
        assert issue.description == 'Detailed description'
        assert issue.suggestion == 'Fix suggestion'
        assert issue.confidence == 0.95
        assert issue.rule_id == 'RULE001'
        assert issue.external_references == ['https://example.com']
        assert issue.evidence == 'Validation evidence'

    def test_to_dict(self):
        """Test conversion to dictionary."""
        issue = CodeAnalysisIssue(
            issue='Test bug',
            severity='high',
            category='logicBug',
            description='Description'
        )
        
        result = issue.to_dict()
        
        assert isinstance(result, dict)
        assert result['issue'] == 'Test bug'
        assert result['severity'] == 'high'
        assert result['category'] == 'logicBug'
        assert result['description'] == 'Description'

    def test_to_dict_excludes_none(self):
        """Test that to_dict excludes None values."""
        issue = CodeAnalysisIssue(
            issue='Test bug',
            severity='high',
            category='logicBug'
        )
        
        result = issue.to_dict()
        
        # Optional fields with None should not be in dict
        assert 'file_path' not in result or result['file_path'] is not None

    def test_from_dict(self):
        """Test creation from dictionary."""
        data = {
            'issue': 'Test bug',
            'severity': 'high',
            'category': 'logicBug',
            'description': 'Description'
        }
        
        issue = CodeAnalysisIssue.from_dict(data)
        
        assert issue.issue == 'Test bug'
        assert issue.severity == 'high'
        assert issue.category == 'logicBug'
        assert issue.description == 'Description'

    def test_from_dict_ignores_unknown_fields(self):
        """Test that from_dict ignores unknown fields."""
        data = {
            'issue': 'Test bug',
            'severity': 'high',
            'category': 'logicBug',
            'unknown_field': 'value'
        }
        
        issue = CodeAnalysisIssue.from_dict(data)
        
        assert issue.issue == 'Test bug'
        assert not hasattr(issue, 'unknown_field')

    def test_default_evidence_empty(self):
        """Test that evidence defaults to empty string."""
        issue = CodeAnalysisIssue(
            issue='Test bug',
            severity='high',
            category='logicBug'
        )
        
        assert issue.evidence == ''


class TestCodeAnalysisResult:
    """Tests for CodeAnalysisResult dataclass."""

    def test_init_required_fields(self):
        """Test initialization with required fields."""
        result = CodeAnalysisResult(
            file_path='src/Example.java',
            function='testFunc',
            checksum='abc123'
        )
        
        assert result.file_path == 'src/Example.java'
        assert result.function == 'testFunc'
        assert result.checksum == 'abc123'
        assert result.results == []

    def test_init_with_issues(self):
        """Test initialization with issues."""
        issues = [
            {'issue': 'Bug 1', 'severity': 'high', 'category': 'logicBug'},
            {'issue': 'Bug 2', 'severity': 'medium', 'category': 'performance'}
        ]
        
        result = CodeAnalysisResult(
            file_path='src/Example.java',
            function='testFunc',
            checksum='abc123',
            results=issues
        )
        
        assert len(result.results) == 2
        assert all(isinstance(r, CodeAnalysisIssue) for r in result.results)

    def test_init_normalizes_dict_issues(self):
        """Test that dict issues are normalized to CodeAnalysisIssue."""
        issues = [
            {'issue': 'Bug 1', 'severity': 'high', 'category': 'logicBug'}
        ]
        
        result = CodeAnalysisResult(
            file_path='src/Example.java',
            function='testFunc',
            checksum='abc123',
            results=issues
        )
        
        assert isinstance(result.results[0], CodeAnalysisIssue)

    def test_init_sets_timestamp(self):
        """Test that analysis_timestamp is set automatically."""
        result = CodeAnalysisResult(
            file_path='src/Example.java',
            function='testFunc',
            checksum='abc123'
        )
        
        assert result.analysis_timestamp is not None
        assert isinstance(result.analysis_timestamp, datetime)

    def test_to_dict(self):
        """Test conversion to dictionary."""
        result = CodeAnalysisResult(
            file_path='src/Example.java',
            function='testFunc',
            checksum='abc123',
            results=[{'issue': 'Bug', 'severity': 'high', 'category': 'logicBug'}]
        )
        
        dict_result = result.to_dict()
        
        assert isinstance(dict_result, dict)
        assert dict_result['file_path'] == 'src/Example.java'
        assert dict_result['function'] == 'testFunc'
        assert dict_result['checksum'] == 'abc123'
        assert len(dict_result['results']) == 1

    def test_to_json(self):
        """Test conversion to JSON string."""
        result = CodeAnalysisResult(
            file_path='src/Example.java',
            function='testFunc',
            checksum='abc123'
        )
        
        json_str = result.to_json()
        
        assert isinstance(json_str, str)
        parsed = json.loads(json_str)
        assert parsed['file_path'] == 'src/Example.java'

    def test_from_dict(self):
        """Test creation from dictionary."""
        data = {
            'file_path': 'src/Example.java',
            'function': 'testFunc',
            'checksum': 'abc123',
            'results': [
                {'issue': 'Bug', 'severity': 'high', 'category': 'logicBug'}
            ]
        }
        
        result = CodeAnalysisResult.from_dict(data)
        
        assert result.file_path == 'src/Example.java'
        assert result.function == 'testFunc'
        assert result.checksum == 'abc123'
        assert len(result.results) == 1

    def test_from_dict_with_timestamp(self):
        """Test creation from dictionary with timestamp."""
        data = {
            'file_path': 'src/Example.java',
            'function': 'testFunc',
            'checksum': 'abc123',
            'results': [],
            'analysis_timestamp': '2024-01-15T10:30:00'
        }
        
        result = CodeAnalysisResult.from_dict(data)
        
        assert result.analysis_timestamp is not None

    def test_from_dict_invalid_type(self):
        """Test from_dict with invalid type raises ValueError."""
        with pytest.raises(ValueError):
            CodeAnalysisResult.from_dict("not a dict")

    def test_from_json(self):
        """Test creation from JSON string."""
        json_str = json.dumps({
            'file_path': 'src/Example.java',
            'function': 'testFunc',
            'checksum': 'abc123',
            'results': []
        })
        
        result = CodeAnalysisResult.from_json(json_str)
        
        assert result.file_path == 'src/Example.java'

    def test_has_issues_true(self):
        """Test has_issues returns True when issues exist."""
        result = CodeAnalysisResult(
            file_path='src/Example.java',
            function='testFunc',
            checksum='abc123',
            results=[{'issue': 'Bug', 'severity': 'high', 'category': 'logicBug'}]
        )
        
        assert result.has_issues() is True

    def test_has_issues_false(self):
        """Test has_issues returns False when no issues."""
        result = CodeAnalysisResult(
            file_path='src/Example.java',
            function='testFunc',
            checksum='abc123'
        )
        
        assert result.has_issues() is False

    def test_get_issue_count(self):
        """Test get_issue_count returns correct count."""
        result = CodeAnalysisResult(
            file_path='src/Example.java',
            function='testFunc',
            checksum='abc123',
            results=[
                {'issue': 'Bug 1', 'severity': 'high', 'category': 'logicBug'},
                {'issue': 'Bug 2', 'severity': 'medium', 'category': 'performance'}
            ]
        )
        
        assert result.get_issue_count() == 2

    def test_get_issues_by_severity(self):
        """Test get_issues_by_severity filters correctly."""
        result = CodeAnalysisResult(
            file_path='src/Example.java',
            function='testFunc',
            checksum='abc123',
            results=[
                {'issue': 'Bug 1', 'severity': 'high', 'category': 'logicBug'},
                {'issue': 'Bug 2', 'severity': 'medium', 'category': 'performance'},
                {'issue': 'Bug 3', 'severity': 'high', 'category': 'logicBug'}
            ]
        )
        
        high_issues = result.get_issues_by_severity('high')
        
        assert len(high_issues) == 2

    def test_get_critical_issues(self):
        """Test get_critical_issues returns critical issues."""
        result = CodeAnalysisResult(
            file_path='src/Example.java',
            function='testFunc',
            checksum='abc123',
            results=[
                {'issue': 'Bug 1', 'severity': 'critical', 'category': 'logicBug'},
                {'issue': 'Bug 2', 'severity': 'high', 'category': 'logicBug'}
            ]
        )
        
        critical = result.get_critical_issues()
        
        assert len(critical) == 1
        assert critical[0].severity == 'critical'

    def test_get_high_issues(self):
        """Test get_high_issues returns high severity issues."""
        result = CodeAnalysisResult(
            file_path='src/Example.java',
            function='testFunc',
            checksum='abc123',
            results=[
                {'issue': 'Bug 1', 'severity': 'high', 'category': 'logicBug'},
                {'issue': 'Bug 2', 'severity': 'medium', 'category': 'logicBug'}
            ]
        )
        
        high = result.get_high_issues()
        
        assert len(high) == 1
        assert high[0].severity == 'high'


class TestCodeAnalysisResultValidation:
    """Tests for CodeAnalysisResult validation."""

    def test_validate_valid_result(self):
        """Test validation of valid result."""
        result = CodeAnalysisResult(
            file_path='src/Example.java',
            function='testFunc',
            checksum='abc123',
            results=[{'issue': 'Bug', 'severity': 'high', 'category': 'logicBug'}]
        )
        
        errors = result.validate()
        
        assert len(errors) == 0

    def test_validate_missing_file_path(self):
        """Test validation catches missing file_path."""
        result = CodeAnalysisResult(
            file_path='',
            function='testFunc',
            checksum='abc123'
        )
        
        errors = result.validate()
        
        assert any('file_path' in error for error in errors)

    def test_validate_missing_function(self):
        """Test validation catches missing function."""
        result = CodeAnalysisResult(
            file_path='src/Example.java',
            function='',
            checksum='abc123'
        )
        
        errors = result.validate()
        
        assert any('function' in error for error in errors)

    def test_validate_missing_checksum(self):
        """Test validation catches missing checksum."""
        result = CodeAnalysisResult(
            file_path='src/Example.java',
            function='testFunc',
            checksum=''
        )
        
        errors = result.validate()
        
        assert any('checksum' in error for error in errors)

    def test_validate_invalid_severity(self):
        """Test validation catches invalid severity."""
        result = CodeAnalysisResult(
            file_path='src/Example.java',
            function='testFunc',
            checksum='abc123',
            results=[{'issue': 'Bug', 'severity': 'invalid', 'category': 'logicBug'}]
        )
        
        errors = result.validate()
        
        assert any('severity' in error for error in errors)

    def test_validate_missing_issue_text(self):
        """Test validation catches missing issue text."""
        result = CodeAnalysisResult(
            file_path='src/Example.java',
            function='testFunc',
            checksum='abc123',
            results=[{'issue': '', 'severity': 'high', 'category': 'logicBug'}]
        )
        
        errors = result.validate()
        
        assert any('issue' in error for error in errors)

    def test_validate_missing_category(self):
        """Test validation catches missing category."""
        result = CodeAnalysisResult(
            file_path='src/Example.java',
            function='testFunc',
            checksum='abc123',
            results=[{'issue': 'Bug', 'severity': 'high', 'category': ''}]
        )
        
        errors = result.validate()
        
        assert any('category' in error for error in errors)


class TestCodeAnalysisResultValidator:
    """Tests for CodeAnalysisResultValidator class."""

    def test_is_valid_result_valid(self):
        """Test is_valid_result returns True for valid result."""
        result = CodeAnalysisResult(
            file_path='src/Example.java',
            function='testFunc',
            checksum='abc123',
            results=[{'issue': 'Bug', 'severity': 'high', 'category': 'logicBug'}]
        )
        
        assert CodeAnalysisResultValidator.is_valid_result(result) is True

    def test_is_valid_result_invalid(self):
        """Test is_valid_result returns False for invalid result."""
        result = CodeAnalysisResult(
            file_path='',
            function='testFunc',
            checksum='abc123'
        )
        
        assert CodeAnalysisResultValidator.is_valid_result(result) is False

    def test_is_valid_result_dict(self):
        """Test is_valid_result with dictionary input."""
        data = {
            'file_path': 'src/Example.java',
            'function': 'testFunc',
            'checksum': 'abc123',
            'results': [{'issue': 'Bug', 'severity': 'high', 'category': 'logicBug'}]
        }
        
        assert CodeAnalysisResultValidator.is_valid_result(data) is True

    def test_is_valid_result_invalid_type(self):
        """Test is_valid_result returns False for invalid type."""
        assert CodeAnalysisResultValidator.is_valid_result("not a result") is False
        assert CodeAnalysisResultValidator.is_valid_result(123) is False
        assert CodeAnalysisResultValidator.is_valid_result(None) is False

    def test_normalize_result_from_instance(self):
        """Test normalize_result with CodeAnalysisResult instance."""
        result = CodeAnalysisResult(
            file_path='src/Example.java',
            function='testFunc',
            checksum='abc123'
        )
        
        normalized = CodeAnalysisResultValidator.normalize_result(result)
        
        assert normalized == result

    def test_normalize_result_from_dict(self):
        """Test normalize_result with dictionary."""
        data = {
            'file_path': 'src/Example.java',
            'function': 'testFunc',
            'checksum': 'abc123',
            'results': []
        }
        
        normalized = CodeAnalysisResultValidator.normalize_result(data)
        
        assert isinstance(normalized, CodeAnalysisResult)
        assert normalized.file_path == 'src/Example.java'

    def test_normalize_result_missing_fields(self):
        """Test normalize_result raises error for missing fields."""
        data = {
            'file_path': 'src/Example.java'
            # Missing function and checksum
        }
        
        with pytest.raises(ValueError):
            CodeAnalysisResultValidator.normalize_result(data)

    def test_normalize_result_invalid_type(self):
        """Test normalize_result raises error for invalid type."""
        with pytest.raises(ValueError):
            CodeAnalysisResultValidator.normalize_result("not a result")


class TestConvenienceFunctions:
    """Tests for convenience functions."""

    def test_create_result(self):
        """Test create_result function."""
        result = create_result(
            file_path='src/Example.java',
            function='testFunc',
            checksum='abc123'
        )
        
        assert isinstance(result, CodeAnalysisResult)
        assert result.file_path == 'src/Example.java'
        assert result.function == 'testFunc'
        assert result.checksum == 'abc123'

    def test_create_result_with_issues(self):
        """Test create_result with issues."""
        issues = [
            {'issue': 'Bug', 'severity': 'high', 'category': 'logicBug'}
        ]
        
        result = create_result(
            file_path='src/Example.java',
            function='testFunc',
            checksum='abc123',
            issues=issues
        )
        
        assert len(result.results) == 1

    def test_create_issue(self):
        """Test create_issue function."""
        issue = create_issue(
            issue='Test bug',
            severity='high',
            category='logicBug'
        )
        
        assert isinstance(issue, CodeAnalysisIssue)
        assert issue.issue == 'Test bug'
        assert issue.severity == 'high'
        assert issue.category == 'logicBug'

    def test_create_issue_with_kwargs(self):
        """Test create_issue with additional kwargs."""
        issue = create_issue(
            issue='Test bug',
            severity='high',
            category='logicBug',
            description='Detailed description',
            suggestion='Fix it'
        )
        
        assert issue.description == 'Detailed description'
        assert issue.suggestion == 'Fix it'

    def test_validate_result_format_valid(self):
        """Test validate_result_format with valid result."""
        result = CodeAnalysisResult(
            file_path='src/Example.java',
            function='testFunc',
            checksum='abc123',
            results=[{'issue': 'Bug', 'severity': 'high', 'category': 'logicBug'}]
        )
        
        errors = validate_result_format(result)
        
        assert len(errors) == 0

    def test_validate_result_format_dict(self):
        """Test validate_result_format with dictionary."""
        data = {
            'file_path': 'src/Example.java',
            'function': 'testFunc',
            'checksum': 'abc123',
            'results': [{'issue': 'Bug', 'severity': 'high', 'category': 'logicBug'}]
        }
        
        errors = validate_result_format(data)
        
        assert len(errors) == 0

    def test_validate_result_format_invalid(self):
        """Test validate_result_format with invalid data."""
        errors = validate_result_format("not a result")
        
        assert len(errors) > 0
        assert any('Invalid data type' in error for error in errors)


class TestCodeAnalysisResultEdgeCases:
    """Tests for edge cases in CodeAnalysisResult."""

    def test_empty_results_list(self):
        """Test result with empty results list."""
        result = CodeAnalysisResult(
            file_path='src/Example.java',
            function='testFunc',
            checksum='abc123',
            results=[]
        )
        
        assert result.has_issues() is False
        assert result.get_issue_count() == 0

    def test_results_with_mixed_types(self):
        """Test results with mixed dict and CodeAnalysisIssue types."""
        issue_obj = CodeAnalysisIssue(
            issue='Bug 1',
            severity='high',
            category='logicBug'
        )
        issue_dict = {'issue': 'Bug 2', 'severity': 'medium', 'category': 'performance'}
        
        result = CodeAnalysisResult(
            file_path='src/Example.java',
            function='testFunc',
            checksum='abc123',
            results=[issue_obj, issue_dict]
        )
        
        assert len(result.results) == 2
        assert all(isinstance(r, CodeAnalysisIssue) for r in result.results)

    def test_roundtrip_serialization(self):
        """Test that result survives JSON roundtrip."""
        original = CodeAnalysisResult(
            file_path='src/Example.java',
            function='testFunc',
            checksum='abc123',
            results=[{'issue': 'Bug', 'severity': 'high', 'category': 'logicBug'}]
        )
        
        json_str = original.to_json()
        restored = CodeAnalysisResult.from_json(json_str)
        
        assert restored.file_path == original.file_path
        assert restored.function == original.function
        assert restored.checksum == original.checksum
        assert len(restored.results) == len(original.results)
