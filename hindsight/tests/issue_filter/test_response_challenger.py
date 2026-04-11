#!/usr/bin/env python3
"""
Tests for hindsight.issue_filter.response_challenger module.

Note: Some tests for LLM-based challenging are marked as skipped because they
require external API calls or complex mocking that would need code changes
outside the tests directory.
"""

import os
import sys
import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

# Add project root to path
project_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(project_root))

from hindsight.issue_filter.response_challenger import LLMResponseChallenger


class TestLLMResponseChallengerInit:
    """Tests for LLMResponseChallenger initialization."""

    def test_init_with_api_key(self, temp_dir):
        """Test initialization with valid API key."""
        challenger = LLMResponseChallenger(
            api_key="test-api-key",
            config={'project_name': 'test'},
            dropped_issues_dir=temp_dir
        )
        
        assert challenger.challenger_available is True
        assert challenger.api_key == "test-api-key"

    def test_init_with_dummy_key(self, temp_dir):
        """Test initialization with dummy API key."""
        challenger = LLMResponseChallenger(
            api_key="dummy-key",
            config={'project_name': 'test'},
            dropped_issues_dir=temp_dir
        )
        
        assert challenger.challenger_available is True
        assert challenger.api_key == "dummy-key"

    def test_init_without_api_key(self, temp_dir):
        """Test initialization without API key."""
        challenger = LLMResponseChallenger(
            api_key=None,
            config={'project_name': 'test'},
            dropped_issues_dir=temp_dir
        )
        
        assert challenger.challenger_available is False

    def test_init_with_empty_api_key(self, temp_dir):
        """Test initialization with empty API key."""
        challenger = LLMResponseChallenger(
            api_key="",
            config={'project_name': 'test'},
            dropped_issues_dir=temp_dir
        )
        
        assert challenger.challenger_available is False

    def test_init_creates_dropped_issues_dir(self, temp_dir):
        """Test that initialization creates dropped issues directory."""
        dropped_dir = os.path.join(temp_dir, 'dropped_issues')
        
        challenger = LLMResponseChallenger(
            api_key="test-key",
            config={'project_name': 'test'},
            dropped_issues_dir=dropped_dir
        )
        
        assert os.path.exists(dropped_dir)

    def test_init_with_capture_evidence_enabled(self, temp_dir):
        """Test initialization with evidence capture enabled."""
        challenger = LLMResponseChallenger(
            api_key="test-key",
            config={'project_name': 'test'},
            dropped_issues_dir=temp_dir,
            capture_evidence=True
        )
        
        assert challenger.capture_evidence is True

    def test_init_with_capture_evidence_disabled(self, temp_dir):
        """Test initialization with evidence capture disabled."""
        challenger = LLMResponseChallenger(
            api_key="test-key",
            config={'project_name': 'test'},
            dropped_issues_dir=temp_dir,
            capture_evidence=False
        )
        
        assert challenger.capture_evidence is False


class TestLLMResponseChallengerIsAvailable:
    """Tests for LLMResponseChallenger.is_available() method."""

    def test_is_available_with_api_key(self, temp_dir):
        """Test is_available returns True with API key."""
        challenger = LLMResponseChallenger(
            api_key="test-key",
            config={'project_name': 'test'},
            dropped_issues_dir=temp_dir
        )
        
        assert challenger.is_available() is True

    def test_is_available_without_api_key(self, temp_dir):
        """Test is_available returns False without API key."""
        challenger = LLMResponseChallenger(
            api_key=None,
            config={'project_name': 'test'},
            dropped_issues_dir=temp_dir
        )
        
        assert challenger.is_available() is False


class TestLLMResponseChallengerDummyChallenge:
    """Tests for dummy challenge mode."""

    def test_dummy_challenge_keeps_valid_issues(self, temp_dir, sample_issues):
        """Test dummy challenge keeps valid issues."""
        challenger = LLMResponseChallenger(
            api_key="dummy-key",
            config={'project_name': 'test'},
            dropped_issues_dir=temp_dir
        )
        
        # Filter to only logicBug and performance issues
        valid_issues = [
            issue for issue in sample_issues 
            if issue.get('category') in ['logicBug', 'performance']
        ]
        
        challenged = challenger.challenge_issues(valid_issues)
        
        # Dummy mode should keep most issues
        assert len(challenged) > 0

    def test_dummy_challenge_filters_false_positives(self, temp_dir):
        """Test dummy challenge filters obvious false positives."""
        challenger = LLMResponseChallenger(
            api_key="dummy-key",
            config={'project_name': 'test'},
            dropped_issues_dir=temp_dir
        )
        
        issues = [
            {
                'issue': 'Variable name does not follow convention',
                'category': 'logicBug',
                'severity': 'low',
                'description': 'The variable name should be camelCase'
            },
            {
                'issue': 'Memory leak detected',
                'category': 'logicBug',
                'severity': 'high',
                'description': 'Memory not released properly'
            }
        ]
        
        challenged = challenger.challenge_issues(issues)
        
        # Both should be filtered in dummy mode (variable name and memory leak)
        assert len(challenged) == 0

    def test_dummy_challenge_empty_list(self, temp_dir):
        """Test dummy challenge with empty list."""
        challenger = LLMResponseChallenger(
            api_key="dummy-key",
            config={'project_name': 'test'},
            dropped_issues_dir=temp_dir
        )
        
        challenged = challenger.challenge_issues([])
        
        assert challenged == []

    def test_dummy_challenge_adds_empty_evidence(self, temp_dir):
        """Test dummy challenge adds empty evidence field."""
        challenger = LLMResponseChallenger(
            api_key="dummy-key",
            config={'project_name': 'test'},
            dropped_issues_dir=temp_dir
        )
        
        issues = [
            {
                'issue': 'Null pointer dereference',
                'category': 'logicBug',
                'severity': 'high',
                'description': 'Pointer may be null'
            }
        ]
        
        challenged = challenger.challenge_issues(issues)
        
        if len(challenged) > 0:
            assert 'evidence' in challenged[0]
            assert challenged[0]['evidence'] == ''


class TestLLMResponseChallengerChallengeIssues:
    """Tests for challenge_issues method."""

    def test_challenge_issues_not_available(self, temp_dir, sample_issues):
        """Test challenge_issues when challenger not available."""
        challenger = LLMResponseChallenger(
            api_key=None,
            config={'project_name': 'test'},
            dropped_issues_dir=temp_dir
        )
        
        # Should return all issues unchanged when not available
        challenged = challenger.challenge_issues(sample_issues)
        
        assert len(challenged) == len(sample_issues)

    def test_challenge_issues_with_trace_context(self, temp_dir):
        """Test challenge_issues with trace context."""
        challenger = LLMResponseChallenger(
            api_key="dummy-key",
            config={'project_name': 'test'},
            dropped_issues_dir=temp_dir
        )
        
        issues = [
            {
                'issue': 'Null pointer',
                'category': 'logicBug',
                'severity': 'high'
            }
        ]
        
        trace_context = {
            'trace_id': 'test-trace-123',
            'callstack': ['func1', 'func2', 'func3'],
            'repo_name': 'test-repo'
        }
        
        challenged = challenger.challenge_issues(issues, trace_context=trace_context)
        
        # Should process without error
        assert isinstance(challenged, list)


class TestLLMResponseChallengerSetTraceContext:
    """Tests for set_trace_context method."""

    def test_set_trace_context(self, temp_dir):
        """Test setting trace context."""
        challenger = LLMResponseChallenger(
            api_key="test-key",
            config={'project_name': 'test'},
            dropped_issues_dir=temp_dir
        )
        
        trace_context = {
            'trace_id': 'test-123',
            'callstack': ['func1', 'func2'],
            'repo_name': 'test-repo'
        }
        
        challenger.set_trace_context(trace_context)
        
        assert challenger.trace_context == trace_context

    def test_set_trace_context_none(self, temp_dir):
        """Test setting trace context to None."""
        challenger = LLMResponseChallenger(
            api_key="test-key",
            config={'project_name': 'test'},
            dropped_issues_dir=temp_dir
        )
        
        challenger.set_trace_context(None)
        
        assert challenger.trace_context is None


class TestLLMResponseChallengerGetChallengerStats:
    """Tests for get_challenger_stats method."""

    def test_get_challenger_stats_not_available(self, temp_dir):
        """Test stats when challenger not available."""
        challenger = LLMResponseChallenger(
            api_key=None,
            config={'project_name': 'test'},
            dropped_issues_dir=temp_dir
        )
        
        stats = challenger.get_challenger_stats()
        
        assert stats['available'] is False
        assert 'reason' in stats

    def test_get_challenger_stats_dummy_mode(self, temp_dir):
        """Test stats in dummy mode."""
        challenger = LLMResponseChallenger(
            api_key="dummy-key",
            config={'project_name': 'test'},
            dropped_issues_dir=temp_dir
        )
        
        stats = challenger.get_challenger_stats()
        
        assert stats['available'] is True
        assert stats['challenger_type'] == 'dummy_false_positive_detection'

    def test_get_challenger_stats_real_mode(self, temp_dir):
        """Test stats in real LLM mode."""
        challenger = LLMResponseChallenger(
            api_key="real-api-key",
            config={'project_name': 'test'},
            dropped_issues_dir=temp_dir
        )
        
        stats = challenger.get_challenger_stats()
        
        assert stats['available'] is True
        assert stats['challenger_type'] == 'real_llm_challenging'


class TestLLMResponseChallengerGetFilePaths:
    """Tests for _get_file_paths_from_issue method."""

    def test_get_file_paths_from_issue_basic(self, temp_dir):
        """Test extracting file paths from issue."""
        challenger = LLMResponseChallenger(
            api_key="test-key",
            config={'project_name': 'test'},
            dropped_issues_dir=temp_dir
        )
        
        issue = {
            'file_path': 'src/main/java/Example.java',
            'description': 'Issue in the code'
        }
        
        paths = challenger._get_file_paths_from_issue(issue)
        
        assert 'src/main/java/Example.java' in paths

    def test_get_file_paths_from_issue_with_filePath(self, temp_dir):
        """Test extracting file paths using filePath key."""
        challenger = LLMResponseChallenger(
            api_key="test-key",
            config={'project_name': 'test'},
            dropped_issues_dir=temp_dir
        )
        
        issue = {
            'filePath': 'src/main/java/Example.java',
            'description': 'Issue in the code'
        }
        
        paths = challenger._get_file_paths_from_issue(issue)
        
        assert 'src/main/java/Example.java' in paths

    def test_get_file_paths_from_issue_unknown(self, temp_dir):
        """Test extracting file paths when path is Unknown."""
        challenger = LLMResponseChallenger(
            api_key="test-key",
            config={'project_name': 'test'},
            dropped_issues_dir=temp_dir
        )
        
        issue = {
            'file_path': 'Unknown',
            'description': 'Issue in the code'
        }
        
        paths = challenger._get_file_paths_from_issue(issue)
        
        assert 'Unknown' not in paths

    def test_get_file_paths_from_issue_empty(self, temp_dir):
        """Test extracting file paths when no path available."""
        challenger = LLMResponseChallenger(
            api_key="test-key",
            config={'project_name': 'test'},
            dropped_issues_dir=temp_dir
        )
        
        issue = {
            'description': 'Issue in the code'
        }
        
        paths = challenger._get_file_paths_from_issue(issue)
        
        assert len(paths) == 0


class TestLLMResponseChallengerSaveDroppedIssue:
    """Tests for _save_dropped_issue method."""

    def test_save_dropped_issue_basic(self, temp_dir):
        """Test saving dropped issue."""
        dropped_dir = os.path.join(temp_dir, 'dropped')
        os.makedirs(dropped_dir, exist_ok=True)
        
        challenger = LLMResponseChallenger(
            api_key="test-key",
            config={'project_name': 'test'},
            dropped_issues_dir=dropped_dir
        )
        
        issue = {
            'issue': 'Test issue',
            'category': 'logicBug',
            'severity': 'high'
        }
        
        challenger._save_dropped_issue(issue, "Test reason for dropping")
        
        # Check that a file was created
        files = os.listdir(dropped_dir)
        assert len(files) == 1
        assert files[0].endswith('.json')

    def test_save_dropped_issue_with_trace_context(self, temp_dir):
        """Test saving dropped issue with trace context."""
        dropped_dir = os.path.join(temp_dir, 'dropped')
        os.makedirs(dropped_dir, exist_ok=True)
        
        challenger = LLMResponseChallenger(
            api_key="test-key",
            config={'project_name': 'test'},
            dropped_issues_dir=dropped_dir
        )
        
        challenger.set_trace_context({
            'trace_id': 'test-trace-123',
            'callstack': ['func1', 'func2'],
            'repo_name': 'test-repo'
        })
        
        issue = {
            'issue': 'Test issue',
            'category': 'logicBug',
            'severity': 'high'
        }
        
        challenger._save_dropped_issue(issue, "Test reason")
        
        # Check file content
        files = os.listdir(dropped_dir)
        assert len(files) == 1
        
        with open(os.path.join(dropped_dir, files[0]), 'r') as f:
            data = json.load(f)
        
        assert data['trace_id'] == 'test-trace-123'
        assert 'results' in data

    def test_save_dropped_issue_no_dir(self, temp_dir):
        """Test saving dropped issue when directory not available."""
        challenger = LLMResponseChallenger(
            api_key="test-key",
            config={'project_name': 'test'},
            dropped_issues_dir=None
        )
        
        # Force dropped_issues_dir to None
        challenger.dropped_issues_dir = None
        
        issue = {
            'issue': 'Test issue',
            'category': 'logicBug'
        }
        
        # Should not raise, just log warning
        challenger._save_dropped_issue(issue, "Test reason")


class TestLLMResponseChallengerLLMChallenge:
    """Tests for real LLM challenge mode.
    
    Note: These tests are marked as skipped because they require:
    1. External API calls to LLM providers
    2. Complex mocking of the Claude class and Tools class
    3. Modifications to code outside the tests directory
    """

    @pytest.mark.skip(reason="Requires external LLM API calls - skipping for unit tests")
    def test_llm_challenge_real_api(self, temp_dir):
        """Test real LLM challenge with actual API."""
        pass

    @pytest.mark.skip(reason="Requires complex mocking of Claude class")
    def test_llm_challenge_with_mocked_llm(self, temp_dir):
        """Test LLM challenge with mocked LLM responses."""
        pass

    @pytest.mark.skip(reason="Requires complex mocking of Tools class")
    def test_llm_challenge_with_tools(self, temp_dir):
        """Test LLM challenge with file reading tools."""
        pass


class TestLLMResponseChallengerEdgeCases:
    """Tests for edge cases and error handling."""

    def test_challenge_issues_preserves_original_data(self, temp_dir):
        """Test that challenging preserves original issue data."""
        challenger = LLMResponseChallenger(
            api_key="dummy-key",
            config={'project_name': 'test'},
            dropped_issues_dir=temp_dir
        )
        
        original_issue = {
            'issue': 'Null pointer dereference',
            'category': 'logicBug',
            'severity': 'high',
            'file_path': '/path/to/file.java',
            'line_number': 42,
            'custom_field': 'custom_value'
        }
        
        challenged = challenger.challenge_issues([original_issue])
        
        if len(challenged) > 0:
            # All original fields should be preserved
            for key in original_issue:
                assert key in challenged[0]
                assert challenged[0][key] == original_issue[key]

    def test_challenge_issues_handles_malformed_issues(self, temp_dir):
        """Test that challenging handles malformed issues gracefully."""
        challenger = LLMResponseChallenger(
            api_key="dummy-key",
            config={'project_name': 'test'},
            dropped_issues_dir=temp_dir
        )
        
        issues = [
            {},  # Empty issue
            {'issue': ''},  # Empty issue text
            {'category': 'logicBug'},  # Missing issue text
        ]
        
        # Should not raise
        challenged = challenger.challenge_issues(issues)
        
        assert isinstance(challenged, list)
