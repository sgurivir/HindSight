#!/usr/bin/env python3
"""
Tests for hindsight/core/llm/diff_analysis.py - Two-stage diff analysis methods.
"""
import json
import os
import shutil
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch, mock_open
import pytest

from hindsight.core.llm.diff_analysis import DiffAnalysis, DiffAnalysisConfig


@pytest.fixture
def diff_config(tmp_path):
    return DiffAnalysisConfig(
        api_key="test-key",
        api_url="https://api.test.com",
        model="claude-3-5-sonnet",
        repo_path=str(tmp_path),
        output_file="",
        config={"project_name": "TestProject", "description": "Test"},
    )


@pytest.fixture
def sample_prompt_data():
    return {
        "function": "fetchData",
        "file_path": "NetworkClient.swift",
        "code": "  142: func fetchData() {\n  143:+    newCall()\n  144: }",
        "changed_lines": [143],
        "affected_reason": "modified",
        "data_types_used": [],
        "constants_used": {},
        "invoked_functions": [],
        "invoking_functions": [],
        "diff_context": {
            "all_changed_files": ["NetworkClient.swift"]
        }
    }


@pytest.fixture
def sample_diff_context_bundle():
    return {
        "primary_function": {
            "name": "fetchData",
            "file_path": "NetworkClient.swift",
            "start_line": 142,
            "end_line": 144,
            "affected_reason": "modified",
            "changed_lines": [143],
            "source": "  142: func fetchData() {\n  143:+    newCall()\n  144: }"
        },
        "callers": [],
        "callees": [],
        "data_types": [],
        "constants_and_globals": [],
        "file_summaries": {},
        "diff_context": {
            "all_changed_files": ["NetworkClient.swift"],
            "total_files_changed": 1,
            "is_part_of_wider_change": False
        },
        "knowledge_hits": [],
        "collection_notes": "OK"
    }


class TestRunDiffContextCollection:
    """Tests for DiffAnalysis.run_diff_context_collection()"""

    @patch('os.path.exists', return_value=False)
    @patch('hindsight.core.llm.diff_analysis.get_output_directory_provider')
    @patch('hindsight.core.llm.diff_analysis.Claude')
    @patch('hindsight.core.llm.diff_analysis.Tools')
    @patch('hindsight.core.llm.diff_analysis.DiffContextAnalyzer')
    def test_returns_valid_bundle_on_success(self, mock_analyzer_cls, mock_tools, mock_claude_cls, mock_provider, mock_exists, diff_config, sample_prompt_data, sample_diff_context_bundle):
        """Returns a valid diff context bundle dict on success."""
        mock_provider.return_value.get_repo_artifacts_dir.return_value = "/tmp/artifacts"
        mock_provider.return_value.get_custom_base_dir.return_value = "/tmp"

        mock_claude = MagicMock()
        mock_claude_cls.return_value = mock_claude
        mock_claude.check_token_limit.return_value = True

        # Mock the analyzer to return valid JSON
        mock_analyzer = MagicMock()
        mock_analyzer_cls.return_value = mock_analyzer
        mock_analyzer.run_iterative_analysis.return_value = json.dumps(sample_diff_context_bundle)

        with patch.object(Path, 'open', mock_open(read_data="# Diff Context Collection Prompt")):
            with patch('builtins.open', mock_open(read_data="# Diff Context Collection Prompt")):
                diff_analysis = DiffAnalysis(diff_config)
                result = diff_analysis.run_diff_context_collection(sample_prompt_data)

        assert result is not None
        assert isinstance(result, dict)

    @patch('os.path.exists', return_value=False)
    @patch('hindsight.core.llm.diff_analysis.get_output_directory_provider')
    @patch('hindsight.core.llm.diff_analysis.Claude')
    @patch('hindsight.core.llm.diff_analysis.Tools')
    @patch('hindsight.core.llm.diff_analysis.DiffContextAnalyzer')
    def test_returns_none_on_invalid_json(self, mock_analyzer_cls, mock_tools, mock_claude_cls, mock_provider, mock_exists, diff_config, sample_prompt_data):
        """Returns None if LLM returns invalid JSON after retry."""
        mock_provider.return_value.get_repo_artifacts_dir.return_value = "/tmp/artifacts"
        mock_provider.return_value.get_custom_base_dir.return_value = "/tmp"

        mock_claude = MagicMock()
        mock_claude_cls.return_value = mock_claude
        mock_claude.check_token_limit.return_value = True

        # Mock the analyzer to return invalid JSON
        mock_analyzer = MagicMock()
        mock_analyzer_cls.return_value = mock_analyzer
        mock_analyzer.run_iterative_analysis.return_value = "Not JSON at all"

        with patch('builtins.open', mock_open(read_data="# Diff Context Collection Prompt")):
            diff_analysis = DiffAnalysis(diff_config)
            result = diff_analysis.run_diff_context_collection(sample_prompt_data)

        assert result is None


class TestRunDiffAnalysisFromContext:
    """Tests for DiffAnalysis.run_diff_analysis_from_context()"""

    @patch('os.path.exists', return_value=False)
    @patch('hindsight.core.llm.diff_analysis.get_output_directory_provider')
    @patch('hindsight.core.llm.diff_analysis.Claude')
    @patch('hindsight.core.llm.diff_analysis.Tools')
    @patch('hindsight.core.llm.diff_analysis.DiffAnalysisAnalyzer')
    def test_returns_issues_list_on_success(self, mock_analyzer_cls, mock_tools, mock_claude_cls, mock_provider, mock_exists, diff_config, sample_diff_context_bundle):
        """Returns a list of issues matching the output schema."""
        mock_provider.return_value.get_repo_artifacts_dir.return_value = "/tmp/artifacts"
        mock_provider.return_value.get_custom_base_dir.return_value = "/tmp"

        mock_claude = MagicMock()
        mock_claude_cls.return_value = mock_claude
        mock_claude.check_token_limit.return_value = True

        # Mock the analyzer to return valid issues JSON
        mock_analyzer = MagicMock()
        mock_analyzer_cls.return_value = mock_analyzer
        issues = [{"file_path": "NetworkClient.swift", "function_name": "fetchData", "line_number": "143", "severity": "high", "issue": "Bug", "description": "desc", "suggestion": "fix", "category": "logicBug", "issueType": "logicBug"}]
        mock_analyzer.run_iterative_analysis.return_value = json.dumps(issues)

        with patch('builtins.open', mock_open(read_data="# Diff Analysis Prompt")):
            diff_analysis = DiffAnalysis(diff_config)
            result = diff_analysis.run_diff_analysis_from_context(sample_diff_context_bundle)

        assert result is not None
        assert isinstance(result, list)

    @patch('os.path.exists', return_value=False)
    @patch('hindsight.core.llm.diff_analysis.get_output_directory_provider')
    @patch('hindsight.core.llm.diff_analysis.Claude')
    @patch('hindsight.core.llm.diff_analysis.Tools')
    @patch('hindsight.core.llm.diff_analysis.DiffAnalysisAnalyzer')
    def test_returns_empty_list_when_no_issues(self, mock_analyzer_cls, mock_tools, mock_claude_cls, mock_provider, mock_exists, diff_config, sample_diff_context_bundle):
        """Returns [] when LLM finds no issues."""
        mock_provider.return_value.get_repo_artifacts_dir.return_value = "/tmp/artifacts"
        mock_provider.return_value.get_custom_base_dir.return_value = "/tmp"

        mock_claude = MagicMock()
        mock_claude_cls.return_value = mock_claude
        mock_claude.check_token_limit.return_value = True

        # Mock the analyzer to return empty array
        mock_analyzer = MagicMock()
        mock_analyzer_cls.return_value = mock_analyzer
        mock_analyzer.run_iterative_analysis.return_value = '[]'

        with patch('builtins.open', mock_open(read_data="# Diff Analysis Prompt")):
            diff_analysis = DiffAnalysis(diff_config)
            result = diff_analysis.run_diff_analysis_from_context(sample_diff_context_bundle)

        assert result == []
