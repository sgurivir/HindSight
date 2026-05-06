#!/usr/bin/env python3
"""
Tests for hindsight/analyzers/data_flow_analyzer.py and
hindsight/analyzers/external_input_analyzer.py

Covers:
- ExternalInputAnalyzer: schema validation, retry logic, async workers, rate limiting
- DataFlowAnalysisRunner: external input step integration
"""

import asyncio
import json
import os
import tempfile
import time
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from hindsight.analyzers.external_input_analyzer import (
    ExternalInputAnalyzer,
    RateLimiter,
)
from hindsight.core.mcp_tools.code_navigation_server import CodeNavigationServer


# --- Fixtures ---

@pytest.fixture
def sample_call_graph_data():
    """Minimal call graph for testing."""
    return [
        {
            "file": "src/app.swift",
            "functions": [
                {
                    "function": "handleHTTPRequest",
                    "context": {"file": "src/app.swift", "start": 1, "end": 20},
                    "functions_invoked": [
                        {
                            "function": "processData",
                            "context": {"file": "src/app.swift", "start": 25, "end": 40},
                            "functions_invoked": []
                        }
                    ]
                },
                {
                    "function": "processData",
                    "context": {"file": "src/app.swift", "start": 25, "end": 40},
                    "functions_invoked": [
                        {
                            "function": "computeHash",
                            "context": {"file": "src/utils.swift", "start": 1, "end": 10},
                            "functions_invoked": []
                        }
                    ]
                }
            ]
        },
        {
            "file": "src/utils.swift",
            "functions": [
                {
                    "function": "computeHash",
                    "context": {"file": "src/utils.swift", "start": 1, "end": 10},
                    "functions_invoked": []
                }
            ]
        }
    ]


@pytest.fixture
def temp_repo():
    """Create a temp repo with source files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        src_dir = Path(tmpdir) / "src"
        src_dir.mkdir()
        (src_dir / "app.swift").write_text(
            "func handleHTTPRequest(request: URLRequest) {\n"
            "    let data = request.body\n"
            "    processData(data)\n"
            "}\n\n"
            "// padding\n" * 20 + "\n"
            "func processData(data: Data) {\n"
            "    let hash = computeHash(data)\n"
            "}\n"
        )
        (src_dir / "utils.swift").write_text(
            "func computeHash(data: Data) -> String {\n"
            "    return data.sha256()\n"
            "}\n"
        )
        yield tmpdir


@pytest.fixture
def mcp_server(sample_call_graph_data, temp_repo):
    return CodeNavigationServer(
        repo_path=temp_repo,
        call_graph_data=sample_call_graph_data
    )


@pytest.fixture
def sample_call_tree():
    """A call tree structure as produced by CallTreeGenerator."""
    return {
        "call_tree": {
            "function": "ROOT",
            "location": [],
            "children": [
                {
                    "function": "handleHTTPRequest",
                    "location": [{"file_path": "src/app.swift", "start_line": 1, "end_line": 20}],
                    "children": [
                        {
                            "function": "processData",
                            "location": [{"file_path": "src/app.swift", "start_line": 25, "end_line": 40}],
                            "children": [
                                {
                                    "function": "computeHash",
                                    "location": [{"file_path": "src/utils.swift", "start_line": 1, "end_line": 10}],
                                    "children": []
                                }
                            ]
                        }
                    ]
                }
            ]
        },
        "metadata": {
            "total_functions": 3,
            "total_root_nodes": 1,
            "dag_edges_count": 3,
            "max_depth": 2
        }
    }


# --- RateLimiter Tests ---

class TestRateLimiter:
    """Tests for the RateLimiter class."""

    def test_allows_requests_under_limit(self):
        """Requests under the limit should not block."""
        limiter = RateLimiter(max_requests_per_minute=10)

        async def run():
            start = time.monotonic()
            for _ in range(5):
                await limiter.acquire()
            elapsed = time.monotonic() - start
            assert elapsed < 1.0  # Should be near-instant

        asyncio.run(run())

    def test_rate_limits_when_full(self):
        """Should delay when rate limit is exhausted."""
        limiter = RateLimiter(max_requests_per_minute=3)

        async def run():
            # Fill up the bucket
            for _ in range(3):
                await limiter.acquire()
            # Next one should wait
            start = time.monotonic()
            await limiter.acquire()
            elapsed = time.monotonic() - start
            # Should have waited some time (not instant)
            assert elapsed > 0.1

        asyncio.run(run())


# --- Schema Validation Tests ---

class TestExternalInputAnalyzerSchemaValidation:
    """Tests for _validate_output_schema and _extract_final_answer."""

    def _make_analyzer(self, mcp_server):
        async def dummy_llm(system, messages):
            return ""
        return ExternalInputAnalyzer(mcp_server=mcp_server, llm_request_fn=dummy_llm)

    def test_valid_schema_accepted(self, mcp_server):
        analyzer = self._make_analyzer(mcp_server)
        assert analyzer._validate_output_schema({"ext_input": True, "reason": "Takes HTTP request"})
        assert analyzer._validate_output_schema({"ext_input": False, "reason": "Pure computation"})

    def test_missing_reason_rejected(self, mcp_server):
        analyzer = self._make_analyzer(mcp_server)
        assert not analyzer._validate_output_schema({"ext_input": True})

    def test_missing_ext_input_rejected(self, mcp_server):
        analyzer = self._make_analyzer(mcp_server)
        assert not analyzer._validate_output_schema({"reason": "some reason"})

    def test_wrong_ext_input_type_rejected(self, mcp_server):
        analyzer = self._make_analyzer(mcp_server)
        assert not analyzer._validate_output_schema({"ext_input": "yes", "reason": "blah"})

    def test_wrong_reason_type_rejected(self, mcp_server):
        analyzer = self._make_analyzer(mcp_server)
        assert not analyzer._validate_output_schema({"ext_input": True, "reason": 123})

    def test_tool_call_not_treated_as_answer(self, mcp_server):
        analyzer = self._make_analyzer(mcp_server)
        assert not analyzer._validate_output_schema({"tool": "get_callers", "ext_input": True, "reason": "x"})

    def test_extract_final_answer_from_code_fence(self, mcp_server):
        analyzer = self._make_analyzer(mcp_server)
        text = 'Some analysis.\n```json\n{"ext_input": true, "reason": "HTTP handler"}\n```'
        result = analyzer._extract_final_answer(text)
        assert result == (True, "HTTP handler")

    def test_extract_final_answer_false(self, mcp_server):
        analyzer = self._make_analyzer(mcp_server)
        text = '```json\n{"ext_input": false, "reason": "Internal helper"}\n```'
        result = analyzer._extract_final_answer(text)
        assert result == (False, "Internal helper")

    def test_extract_returns_none_for_invalid(self, mcp_server):
        analyzer = self._make_analyzer(mcp_server)
        text = '```json\n{"ext_input": true}\n```'  # missing reason
        result = analyzer._extract_final_answer(text)
        assert result is None

    def test_extract_returns_none_for_tool_call(self, mcp_server):
        analyzer = self._make_analyzer(mcp_server)
        text = '```json\n{"tool": "get_function_body", "symbol_id": "foo"}\n```'
        result = analyzer._extract_final_answer(text)
        assert result is None

    def test_extract_from_unformatted_json(self, mcp_server):
        analyzer = self._make_analyzer(mcp_server)
        text = 'Based on analysis: {"ext_input": false, "reason": "No I/O"}'
        result = analyzer._extract_final_answer(text)
        assert result == (False, "No I/O")


# --- Tool Request Extraction Tests ---

class TestToolRequestExtraction:
    """Tests for _extract_json_tool_requests."""

    def _make_analyzer(self, mcp_server):
        async def dummy_llm(system, messages):
            return ""
        return ExternalInputAnalyzer(mcp_server=mcp_server, llm_request_fn=dummy_llm)

    def test_extracts_tool_request(self, mcp_server):
        analyzer = self._make_analyzer(mcp_server)
        text = 'I need to read the function.\n```json\n{"tool": "get_function_body", "symbol_id": "handleHTTPRequest"}\n```'
        requests = analyzer._extract_json_tool_requests(text)
        assert len(requests) == 1
        assert requests[0]["tool"] == "get_function_body"
        assert requests[0]["symbol_id"] == "handleHTTPRequest"

    def test_extracts_multiple_tool_requests(self, mcp_server):
        analyzer = self._make_analyzer(mcp_server)
        text = (
            '```json\n{"tool": "get_function_body", "symbol_id": "foo"}\n```\n'
            'And also:\n'
            '```json\n{"tool": "get_callers", "symbol_id": "foo"}\n```'
        )
        requests = analyzer._extract_json_tool_requests(text)
        assert len(requests) == 2

    def test_ignores_non_tool_json(self, mcp_server):
        analyzer = self._make_analyzer(mcp_server)
        text = '```json\n{"ext_input": true, "reason": "test"}\n```'
        requests = analyzer._extract_json_tool_requests(text)
        assert len(requests) == 0  # no "tool" key


# --- Schema Correction Tests ---

class TestSchemaCorrection:
    """Tests for schema correction retry logic."""

    def test_correction_message_mentions_schema(self, mcp_server):
        async def dummy_llm(system, messages):
            return ""
        analyzer = ExternalInputAnalyzer(mcp_server=mcp_server, llm_request_fn=dummy_llm)
        msg = analyzer._build_schema_correction_message("bad output")
        assert "ext_input" in msg
        assert "reason" in msg
        assert "boolean" in msg

    def test_retries_on_invalid_schema(self, mcp_server):
        """When LLM returns JSON missing 'reason', correction is sent and retried."""
        call_count = [0]

        async def mock_llm(system, messages):
            call_count[0] += 1
            if call_count[0] == 1:
                # First call: return invalid schema (missing reason)
                return '```json\n{"ext_input": true}\n```'
            else:
                # Second call (after correction): return valid schema
                return '```json\n{"ext_input": true, "reason": "HTTP handler"}\n```'

        analyzer = ExternalInputAnalyzer(
            mcp_server=mcp_server,
            llm_request_fn=mock_llm,
            rate_limit=1000,  # High limit to avoid waits in test
            max_workers=1,
        )

        result = asyncio.run(analyzer._analyze_single_function("handleHTTPRequest"))
        assert result == (True, "HTTP handler")
        assert call_count[0] == 2  # Initial + retry after correction


# --- Full Analysis Flow Tests ---

class TestExternalInputAnalyzerFlow:
    """Tests for the full analysis flow with mocked LLM."""

    def test_tool_use_then_answer(self, mcp_server):
        """LLM uses a tool first, then provides final answer."""
        call_count = [0]

        async def mock_llm(system, messages):
            call_count[0] += 1
            if call_count[0] == 1:
                return '```json\n{"tool": "get_function_body", "symbol_id": "handleHTTPRequest"}\n```'
            else:
                return '```json\n{"ext_input": true, "reason": "Receives HTTP request parameter"}\n```'

        analyzer = ExternalInputAnalyzer(
            mcp_server=mcp_server,
            llm_request_fn=mock_llm,
            rate_limit=1000,
            max_workers=1,
        )

        result = asyncio.run(analyzer._analyze_single_function("handleHTTPRequest"))
        assert result == (True, "Receives HTTP request parameter")

    def test_immediate_answer_no_tools(self, mcp_server):
        """LLM provides answer without using any tools."""
        async def mock_llm(system, messages):
            return '```json\n{"ext_input": false, "reason": "Pure computation"}\n```'

        analyzer = ExternalInputAnalyzer(
            mcp_server=mcp_server,
            llm_request_fn=mock_llm,
            rate_limit=1000,
            max_workers=1,
        )

        result = asyncio.run(analyzer._analyze_single_function("computeHash"))
        assert result == (False, "Pure computation")

    def test_max_iterations_forces_answer(self, mcp_server):
        """When max iterations hit, sends enforcement and extracts answer."""
        call_count = [0]

        async def mock_llm(system, messages):
            call_count[0] += 1
            if call_count[0] <= 10:
                # Keep requesting tools
                return '```json\n{"tool": "get_callers", "symbol_id": "processData"}\n```'
            else:
                # Final enforcement call — provide answer
                return '```json\n{"ext_input": false, "reason": "Only called internally"}\n```'

        analyzer = ExternalInputAnalyzer(
            mcp_server=mcp_server,
            llm_request_fn=mock_llm,
            rate_limit=1000,
            max_workers=1,
            max_tool_iterations=10,
        )

        result = asyncio.run(analyzer._analyze_single_function("processData"))
        assert result == (False, "Only called internally")

    def test_empty_response_returns_default(self, mcp_server):
        """Empty LLM response defaults to (False, ...)."""
        async def mock_llm(system, messages):
            return ""

        analyzer = ExternalInputAnalyzer(
            mcp_server=mcp_server,
            llm_request_fn=mock_llm,
            rate_limit=1000,
            max_workers=1,
        )

        result = asyncio.run(analyzer._analyze_single_function("computeHash"))
        assert result[0] is False

    def test_llm_exception_returns_default(self, mcp_server):
        """LLM exception defaults to (False, ...)."""
        async def mock_llm(system, messages):
            raise RuntimeError("API timeout")

        analyzer = ExternalInputAnalyzer(
            mcp_server=mcp_server,
            llm_request_fn=mock_llm,
            rate_limit=1000,
            max_workers=1,
        )

        result = asyncio.run(analyzer._analyze_single_function("computeHash"))
        assert result[0] is False
        assert "failed" in result[1].lower()


# --- analyze_all and annotate_call_tree Tests ---

class TestAnalyzeAllAndAnnotate:
    """Tests for analyze_all parallel execution and call tree annotation."""

    def test_analyze_all_processes_all_functions(self, mcp_server):
        """All functions in the list get analyzed."""
        async def mock_llm(system, messages):
            # Determine which function based on prompt content
            user_msg = messages[0]["content"]
            if "handleHTTPRequest" in user_msg:
                return '```json\n{"ext_input": true, "reason": "HTTP handler"}\n```'
            return '```json\n{"ext_input": false, "reason": "Internal"}\n```'

        analyzer = ExternalInputAnalyzer(
            mcp_server=mcp_server,
            llm_request_fn=mock_llm,
            rate_limit=1000,
            max_workers=2,
        )

        results = asyncio.run(analyzer.analyze_all(["handleHTTPRequest", "processData", "computeHash"]))
        assert len(results) == 3
        assert results["handleHTTPRequest"] == (True, "HTTP handler")
        assert results["processData"] == (False, "Internal")
        assert results["computeHash"] == (False, "Internal")

    def test_annotate_call_tree_adds_ext_input(self, mcp_server, sample_call_tree):
        """annotate_call_tree adds ext_input and ext_input_reason to every node."""
        async def mock_llm(system, messages):
            user_msg = messages[0]["content"]
            if "handleHTTPRequest" in user_msg:
                return '```json\n{"ext_input": true, "reason": "HTTP"}\n```'
            return '```json\n{"ext_input": false, "reason": "Internal"}\n```'

        analyzer = ExternalInputAnalyzer(
            mcp_server=mcp_server,
            llm_request_fn=mock_llm,
            rate_limit=1000,
            max_workers=2,
        )
        asyncio.run(analyzer.analyze_all(["handleHTTPRequest", "processData", "computeHash"]))

        annotated = analyzer.annotate_call_tree(sample_call_tree)

        # Check structure
        root = annotated["call_tree"]
        assert root["function"] == "ROOT"
        assert "ext_input" in root

        # Check handleHTTPRequest
        handler_node = root["children"][0]
        assert handler_node["function"] == "handleHTTPRequest"
        assert handler_node["ext_input"] is True
        assert handler_node["ext_input_reason"] == "HTTP"

        # Check processData
        process_node = handler_node["children"][0]
        assert process_node["ext_input"] is False

        # Check metadata
        meta = annotated["metadata"]["external_input_analysis"]
        assert meta["total_functions_analyzed"] == 3
        assert meta["functions_with_external_input"] == 1

    def test_annotate_preserves_original_metadata(self, mcp_server, sample_call_tree):
        """Original metadata keys are preserved in annotated tree."""
        async def mock_llm(system, messages):
            return '```json\n{"ext_input": false, "reason": "N/A"}\n```'

        analyzer = ExternalInputAnalyzer(
            mcp_server=mcp_server,
            llm_request_fn=mock_llm,
            rate_limit=1000,
            max_workers=1,
        )
        asyncio.run(analyzer.analyze_all(["handleHTTPRequest"]))

        annotated = analyzer.annotate_call_tree(sample_call_tree)
        assert annotated["metadata"]["total_functions"] == 3
        assert annotated["metadata"]["total_root_nodes"] == 1


# --- DataFlowAnalysisRunner Integration Tests ---

class TestDataFlowAnalysisRunnerExternalInput:
    """Tests for _run_external_input_analysis in DataFlowAnalysisRunner."""

    @pytest.fixture
    def runner_setup(self, sample_call_graph_data, temp_repo, sample_call_tree):
        """Setup a runner with config and temp dirs."""
        from hindsight.analyzers.data_flow_analyzer import DataFlowAnalysisRunner
        from hindsight.core.constants import NESTED_CALL_GRAPH_FILE

        # Write call graph to temp dir simulating astCallGraphDir
        ast_dir = Path(temp_repo) / "code_insights"
        ast_dir.mkdir()
        call_graph_path = ast_dir / NESTED_CALL_GRAPH_FILE
        with open(call_graph_path, 'w') as f:
            json.dump(sample_call_graph_data, f)

        # Output directory
        out_dir = Path(temp_repo) / "output"
        out_dir.mkdir()
        data_flow_dir = out_dir / "data_flow_analysis"
        data_flow_dir.mkdir()

        config = {
            'path_to_repo': temp_repo,
            'astCallGraphDir': str(ast_dir),
            'output_base_dir': str(out_dir),
            'api_end_point': 'http://fake-endpoint',
            'model': 'test-model',
            'max_tokens': 4096,
        }

        runner = DataFlowAnalysisRunner()
        return runner, config, sample_call_tree, data_flow_dir

    @patch('hindsight.core.llm.llm.create_llm_provider')
    @patch('hindsight.analyzers.data_flow_analyzer.get_llm_provider_type')
    @patch('hindsight.analyzers.data_flow_analyzer.get_api_key_from_config')
    def test_produces_call_tree_with_sources_json(
        self, mock_get_api_key, mock_get_provider_type, mock_create_provider, runner_setup
    ):
        """_run_external_input_analysis writes call_tree_with_sources.json."""
        runner, config, call_tree, data_flow_dir = runner_setup

        mock_get_api_key.return_value = "test-key"
        mock_get_provider_type.return_value = "aws_bedrock"

        # Mock provider that returns ext_input=false for everything
        mock_provider = MagicMock()
        mock_provider.create_payload.return_value = {}
        mock_provider.make_request.return_value = {
            "choices": [{"message": {"content":
                '```json\n{"ext_input": false, "reason": "Internal function"}\n```'
            }}]
        }
        mock_create_provider.return_value = mock_provider

        # Mock get_default_data_flow_paths to point to our temp dir
        with patch.object(runner, 'get_default_data_flow_paths', return_value={
            'data_flow_dir': str(data_flow_dir),
            'call_tree_json': str(data_flow_dir / "call_tree.json"),
            'call_tree_text': str(data_flow_dir / "call_tree.txt"),
            'statistics_file': str(data_flow_dir / "call_graph_statistics.json"),
        }):
            result = runner._run_external_input_analysis(config, call_tree, max_workers=2)

        # Verify output file exists
        output_path = data_flow_dir / "call_tree_with_sources.json"
        assert output_path.exists()

        # Verify content
        with open(output_path) as f:
            output = json.load(f)

        assert "call_tree" in output
        assert "metadata" in output
        assert "external_input_analysis" in output["metadata"]

        # Every node should have ext_input field
        def check_node(node):
            assert "ext_input" in node
            assert "ext_input_reason" in node
            assert isinstance(node["ext_input"], bool)
            for child in node.get("children", []):
                check_node(child)

        check_node(output["call_tree"])
