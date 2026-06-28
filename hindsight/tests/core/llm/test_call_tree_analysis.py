"""Tests for the call-tree-at-once analysis methods on CodeAnalysis and DiffAnalysis."""

import json
import os
import shutil
import tempfile
from unittest.mock import MagicMock, patch

import pytest

from hindsight.core.constants import MODEL_CLAUDE_SONNET_3_5_V2
from hindsight.core.llm.code_analysis import AnalysisConfig, CodeAnalysis
from hindsight.core.llm.diff_analysis import DiffAnalysis, DiffAnalysisConfig


SAMPLE_TREE_DICT = {
    "schema_version": "2.0",
    "root": {"function": "rootFn", "file": "src/x.py", "checksum": "abc123"},
    "nodes": [
        {
            "function": "rootFn",
            "file": "src/x.py",
            "start_line": 10,
            "end_line": 20,
            "depth": 0,
            "parent": None,
            "source": "    10 | def rootFn():\n    11 |     leaf()",
            "checksum": "abc123",
            "callees_in_tree": ["leaf"],
        },
        {
            "function": "leaf",
            "file": "src/x.py",
            "start_line": 30,
            "end_line": 35,
            "depth": 1,
            "parent": "rootFn",
            "source": "    30 | def leaf():\n    31 |     return 1",
            "checksum": "def456",
            "callees_in_tree": [],
        },
    ],
    "truncation": {
        "depth_cap_hit": False,
        "char_cap_hit": False,
        "node_cap_hit": False,
        "stubbed_nodes": [],
    },
    "stats": {"node_count": 2, "total_chars": 80, "tree_signature": "sig123"},
}


@pytest.fixture
def temp_repo():
    d = tempfile.mkdtemp()
    yield d
    if os.path.exists(d):
        shutil.rmtree(d)


@pytest.fixture
def code_analysis_instance(temp_repo):
    """Construct a CodeAnalysis with all external deps mocked."""
    cfg = AnalysisConfig(
        json_file_path=os.path.join(temp_repo, "input.json"),
        api_key="k",
        api_url="https://api.example/v1",
        model=MODEL_CLAUDE_SONNET_3_5_V2,
        repo_path=temp_repo,
        output_file="",
        config={"llm_provider_type": "aws_bedrock"},
    )
    with patch("hindsight.core.llm.code_analysis.get_output_directory_provider") as p, \
         patch("hindsight.core.llm.code_analysis.Claude") as mock_claude, \
         patch("hindsight.core.llm.code_analysis.Tools"), \
         patch("hindsight.core.llm.code_analysis.RepoAstIndex"):
        mock_prov = MagicMock()
        mock_prov.get_custom_base_dir.return_value = temp_repo
        mock_prov.get_repo_artifacts_dir.return_value = os.path.join(temp_repo, "artifacts")
        p.return_value = mock_prov
        instance = CodeAnalysis(cfg)
        instance.claude = mock_claude.return_value
        instance.tools = MagicMock()
        yield instance


class TestRunCallTreeAnalysis:
    """Tests for CodeAnalysis.run_call_tree_analysis."""

    def test_returns_issues_on_success(self, code_analysis_instance):
        instance = code_analysis_instance
        instance.claude.check_token_limit.return_value = True

        sample_issues = [
            {"defect_function": "leaf", "defect_file": "src/x.py", "issue": "wrong return"}
        ]
        with patch(
            "hindsight.core.llm.iterative.code_analysis_analyzer.CodeAnalysisAnalyzer.run_iterative_analysis",
            return_value=json.dumps(sample_issues),
        ):
            result = instance.run_call_tree_analysis(SAMPLE_TREE_DICT)
        assert result == sample_issues

    def test_returns_none_when_token_limit_exceeded(self, code_analysis_instance):
        instance = code_analysis_instance
        instance.claude.check_token_limit.return_value = False
        result = instance.run_call_tree_analysis(SAMPLE_TREE_DICT)
        assert result is None

    def test_returns_none_when_llm_returns_nothing(self, code_analysis_instance):
        instance = code_analysis_instance
        instance.claude.check_token_limit.return_value = True
        with patch(
            "hindsight.core.llm.iterative.code_analysis_analyzer.CodeAnalysisAnalyzer.run_iterative_analysis",
            return_value=None,
        ):
            result = instance.run_call_tree_analysis(SAMPLE_TREE_DICT)
        assert result is None

    def test_empty_array_means_no_issues(self, code_analysis_instance):
        instance = code_analysis_instance
        instance.claude.check_token_limit.return_value = True
        with patch(
            "hindsight.core.llm.iterative.code_analysis_analyzer.CodeAnalysisAnalyzer.run_iterative_analysis",
            return_value="[]",
        ):
            result = instance.run_call_tree_analysis(SAMPLE_TREE_DICT)
        assert result == []

    def test_dict_response_is_wrapped(self, code_analysis_instance):
        instance = code_analysis_instance
        instance.claude.check_token_limit.return_value = True
        with patch(
            "hindsight.core.llm.iterative.code_analysis_analyzer.CodeAnalysisAnalyzer.run_iterative_analysis",
            return_value='{"defect_function": "rootFn", "issue": "x"}',
        ):
            result = instance.run_call_tree_analysis(SAMPLE_TREE_DICT)
        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0]["defect_function"] == "rootFn"

    def test_results_envelope_is_unwrapped(self, code_analysis_instance):
        instance = code_analysis_instance
        instance.claude.check_token_limit.return_value = True
        body = json.dumps({"results": [{"defect_function": "leaf", "issue": "x"}]})
        with patch(
            "hindsight.core.llm.iterative.code_analysis_analyzer.CodeAnalysisAnalyzer.run_iterative_analysis",
            return_value=body,
        ):
            result = instance.run_call_tree_analysis(SAMPLE_TREE_DICT)
        assert isinstance(result, list)
        assert result[0]["defect_function"] == "leaf"


@pytest.fixture
def diff_analysis_instance(temp_repo):
    cfg = DiffAnalysisConfig(
        api_key="k",
        api_url="https://api.example/v1",
        model=MODEL_CLAUDE_SONNET_3_5_V2,
        repo_path=temp_repo,
        output_file="",
        config={"llm_provider_type": "aws_bedrock"},
    )
    with patch("hindsight.core.llm.diff_analysis.get_output_directory_provider") as p, \
         patch("hindsight.core.llm.diff_analysis.Claude") as mock_claude, \
         patch("hindsight.core.llm.diff_analysis.Tools"):
        mock_prov = MagicMock()
        mock_prov.get_custom_base_dir.return_value = temp_repo
        mock_prov.get_repo_artifacts_dir.return_value = os.path.join(temp_repo, "artifacts")
        p.return_value = mock_prov
        instance = DiffAnalysis(cfg)
        instance.claude = mock_claude.return_value
        instance.tools = MagicMock()
        yield instance


class TestRunDiffCallTreeAnalysis:
    """Tests for DiffAnalysis.run_diff_call_tree_analysis."""

    DIFF_CONTEXT = {
        "all_changed_files": ["src/x.py"],
        "changed_lines_per_file": {"src/x.py": {"added": [31], "removed": []}},
    }

    def test_returns_issues_on_success(self, diff_analysis_instance):
        instance = diff_analysis_instance
        instance.claude.check_token_limit.return_value = True

        sample_issues = [
            {
                "defect_function": "leaf",
                "defect_file": "src/x.py",
                "defect_line_number": "31",
                "affected_caller_function": "rootFn",
                "affected_caller_line_number": "11",
                "issue": "wrong return",
                "category": "logicBug",
                "severity": "high",
            }
        ]
        with patch(
            "hindsight.core.llm.iterative.diff_analysis_analyzer.DiffAnalysisAnalyzer.run_iterative_analysis",
            return_value=json.dumps(sample_issues),
        ):
            result = instance.run_diff_call_tree_analysis(SAMPLE_TREE_DICT, self.DIFF_CONTEXT)
        assert result == sample_issues

    def test_returns_none_when_token_limit_exceeded(self, diff_analysis_instance):
        instance = diff_analysis_instance
        instance.claude.check_token_limit.return_value = False
        result = instance.run_diff_call_tree_analysis(SAMPLE_TREE_DICT, self.DIFF_CONTEXT)
        assert result is None

    def test_empty_array_means_no_issues(self, diff_analysis_instance):
        instance = diff_analysis_instance
        instance.claude.check_token_limit.return_value = True
        with patch(
            "hindsight.core.llm.iterative.diff_analysis_analyzer.DiffAnalysisAnalyzer.run_iterative_analysis",
            return_value="[]",
        ):
            result = instance.run_diff_call_tree_analysis(SAMPLE_TREE_DICT, self.DIFF_CONTEXT)
        assert result == []
