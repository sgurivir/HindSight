#!/usr/bin/env python3
"""
Tests for hindsight/analyzers/sink_analyzer.py

Covers:
- SinkAnalyzer: schema validation, batch parsing, retry logic, async workers
- DataFlowAnalysisRunner: sink discovery step integration
"""

import asyncio
import json
import os
import tempfile
import time
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from hindsight.analyzers.sink_analyzer import SinkAnalyzer
from hindsight.core.async_infra import RateLimiter
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
                            "function": "executeQuery",
                            "context": {"file": "src/db.swift", "start": 1, "end": 15},
                            "functions_invoked": []
                        }
                    ]
                },
                {
                    "function": "executeQuery",
                    "context": {"file": "src/db.swift", "start": 1, "end": 15},
                    "functions_invoked": []
                },
                {
                    "function": "computeHash",
                    "context": {"file": "src/utils.swift", "start": 1, "end": 10},
                    "functions_invoked": []
                }
            ]
        },
        {
            "file": "src/db.swift",
            "functions": [
                {
                    "function": "executeQuery",
                    "context": {"file": "src/db.swift", "start": 1, "end": 15},
                    "functions_invoked": []
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
            "    let query = request.body\n"
            "    executeQuery(query)\n"
            "}\n"
        )
        (src_dir / "db.swift").write_text(
            "func executeQuery(sql: String) {\n"
            "    database.execute(sql)\n"
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
                            "function": "executeQuery",
                            "location": [{"file_path": "src/db.swift", "start_line": 1, "end_line": 15}],
                            "children": []
                        }
                    ]
                },
                {
                    "function": "computeHash",
                    "location": [{"file_path": "src/utils.swift", "start_line": 1, "end_line": 10}],
                    "children": []
                }
            ]
        },
        "metadata": {
            "total_functions": 3,
            "total_root_nodes": 2,
            "dag_edges_count": 3,
            "max_depth": 2
        }
    }


# --- RateLimiter Tests ---

class TestSinkRateLimiter:
    """Tests for the shared RateLimiter class used by sink analyzer."""

    def test_allows_requests_under_limit(self):
        limiter = RateLimiter(max_requests_per_minute=10)

        async def run():
            start = time.monotonic()
            for _ in range(5):
                await limiter.acquire()
            elapsed = time.monotonic() - start
            assert elapsed < 1.0

        asyncio.run(run())

    def test_rate_limits_when_full(self):
        limiter = RateLimiter(max_requests_per_minute=3)

        async def run():
            for _ in range(3):
                await limiter.acquire()
            start = time.monotonic()
            await limiter.acquire()
            elapsed = time.monotonic() - start
            assert elapsed > 0.1

        asyncio.run(run())


# --- Schema Validation Tests ---

class TestSinkAnalyzerSchemaValidation:
    """Tests for _validate_single_output_schema and _extract_final_answer."""

    def _make_analyzer(self, mcp_server):
        async def dummy_llm(system, messages):
            return ""
        return SinkAnalyzer(mcp_server=mcp_server, llm_request_fn=dummy_llm)

    def test_valid_schema_accepted(self, mcp_server):
        analyzer = self._make_analyzer(mcp_server)
        assert analyzer._validate_single_output_schema({
            "is_sink": True, "reason": "Executes SQL query", "category": "database_write"
        })
        assert analyzer._validate_single_output_schema({
            "is_sink": False, "reason": "Pure computation", "category": "none"
        })

    def test_missing_reason_rejected(self, mcp_server):
        analyzer = self._make_analyzer(mcp_server)
        assert not analyzer._validate_single_output_schema({"is_sink": True})

    def test_missing_is_sink_rejected(self, mcp_server):
        analyzer = self._make_analyzer(mcp_server)
        assert not analyzer._validate_single_output_schema({"reason": "some reason"})

    def test_wrong_is_sink_type_rejected(self, mcp_server):
        analyzer = self._make_analyzer(mcp_server)
        assert not analyzer._validate_single_output_schema({"is_sink": "yes", "reason": "blah"})

    def test_wrong_reason_type_rejected(self, mcp_server):
        analyzer = self._make_analyzer(mcp_server)
        assert not analyzer._validate_single_output_schema({"is_sink": True, "reason": 123})

    def test_tool_call_not_treated_as_answer(self, mcp_server):
        analyzer = self._make_analyzer(mcp_server)
        assert not analyzer._validate_single_output_schema({
            "tool": "get_callers", "is_sink": True, "reason": "x"
        })

    def test_extract_final_answer_from_code_fence(self, mcp_server):
        analyzer = self._make_analyzer(mcp_server)
        text = 'Analysis.\n```json\n{"is_sink": true, "reason": "Executes SQL", "category": "database_write"}\n```'
        result = analyzer._extract_final_answer(text)
        assert result == (True, "Executes SQL", "database_write")

    def test_extract_final_answer_false(self, mcp_server):
        analyzer = self._make_analyzer(mcp_server)
        text = '```json\n{"is_sink": false, "reason": "Pure computation", "category": "none"}\n```'
        result = analyzer._extract_final_answer(text)
        assert result == (False, "Pure computation", "none")

    def test_extract_returns_none_for_invalid(self, mcp_server):
        analyzer = self._make_analyzer(mcp_server)
        text = '```json\n{"is_sink": true}\n```'  # missing reason
        result = analyzer._extract_final_answer(text)
        assert result is None

    def test_extract_returns_none_for_tool_call(self, mcp_server):
        analyzer = self._make_analyzer(mcp_server)
        text = '```json\n{"tool": "get_function_body", "symbol_id": "foo"}\n```'
        result = analyzer._extract_final_answer(text)
        assert result is None

    def test_extract_from_unformatted_json(self, mcp_server):
        analyzer = self._make_analyzer(mcp_server)
        text = 'Based on analysis: {"is_sink": false, "reason": "No I/O", "category": "none"}'
        result = analyzer._extract_final_answer(text)
        assert result == (False, "No I/O", "none")

    def test_extract_defaults_category_to_none(self, mcp_server):
        """When category is missing from JSON, should still parse (defaults to 'none')."""
        analyzer = self._make_analyzer(mcp_server)
        text = '```json\n{"is_sink": false, "reason": "No I/O"}\n```'
        result = analyzer._extract_final_answer(text)
        assert result == (False, "No I/O", "none")


# --- Batch Response Parsing Tests ---

class TestSinkBatchResponseParsing:
    """Tests for _parse_batch_response."""

    def _make_analyzer(self, mcp_server):
        async def dummy_llm(system, messages):
            return ""
        return SinkAnalyzer(mcp_server=mcp_server, llm_request_fn=dummy_llm)

    def test_parse_valid_batch_response(self, mcp_server):
        analyzer = self._make_analyzer(mcp_server)
        batch = [
            {"id": "aaa", "function_name": "executeQuery", "body": "..."},
            {"id": "bbb", "function_name": "computeHash", "body": "..."},
        ]
        response = '''```json
[
  {"id": "aaa", "is_sink": true, "reason": "Executes SQL query with string interpolation.", "category": "database_write"},
  {"id": "bbb", "is_sink": false, "reason": "Pure hash computation.", "category": "none"}
]
```'''
        results = analyzer._parse_batch_response(response, batch)
        assert len(results) == 2
        assert results["executeQuery"] == (True, "Executes SQL query with string interpolation.", "database_write")
        assert results["computeHash"] == (False, "Pure hash computation.", "none")

    def test_parse_response_without_code_fence(self, mcp_server):
        analyzer = self._make_analyzer(mcp_server)
        batch = [{"id": "aaa", "function_name": "foo", "body": "..."}]
        response = '[{"id": "aaa", "is_sink": true, "reason": "Writes file.", "category": "file_system_write"}]'
        results = analyzer._parse_batch_response(response, batch)
        assert len(results) == 1
        assert results["foo"][0] is True

    def test_parse_empty_response(self, mcp_server):
        analyzer = self._make_analyzer(mcp_server)
        batch = [{"id": "aaa", "function_name": "foo", "body": "..."}]
        results = analyzer._parse_batch_response("No JSON here", batch)
        assert len(results) == 0

    def test_parse_coerces_string_boolean(self, mcp_server):
        analyzer = self._make_analyzer(mcp_server)
        batch = [{"id": "aaa", "function_name": "foo", "body": "..."}]
        response = '```json\n[{"id": "aaa", "is_sink": "true", "reason": "test", "category": "none"}]\n```'
        results = analyzer._parse_batch_response(response, batch)
        assert results["foo"][0] is True

    def test_parse_skips_unknown_ids(self, mcp_server):
        analyzer = self._make_analyzer(mcp_server)
        batch = [{"id": "aaa", "function_name": "foo", "body": "..."}]
        response = '```json\n[{"id": "zzz", "is_sink": true, "reason": "unknown", "category": "none"}]\n```'
        results = analyzer._parse_batch_response(response, batch)
        assert len(results) == 0


# --- Tool Request Extraction Tests ---

class TestSinkToolRequestExtraction:

    def _make_analyzer(self, mcp_server):
        async def dummy_llm(system, messages):
            return ""
        return SinkAnalyzer(mcp_server=mcp_server, llm_request_fn=dummy_llm)

    def test_extracts_tool_request(self, mcp_server):
        analyzer = self._make_analyzer(mcp_server)
        text = '```json\n{"tool": "get_function_body", "symbol_id": "executeQuery"}\n```'
        requests = analyzer._extract_json_tool_requests(text)
        assert len(requests) == 1
        assert requests[0]["tool"] == "get_function_body"

    def test_ignores_non_tool_json(self, mcp_server):
        analyzer = self._make_analyzer(mcp_server)
        text = '```json\n{"is_sink": true, "reason": "test", "category": "none"}\n```'
        requests = analyzer._extract_json_tool_requests(text)
        assert len(requests) == 0


# --- Full Analysis Flow Tests ---

class TestSinkAnalyzerFlow:
    """Tests for the full analysis flow with mocked LLM."""

    def test_tool_use_then_answer(self, mcp_server):
        call_count = [0]

        async def mock_llm(system, messages):
            call_count[0] += 1
            if call_count[0] == 1:
                return '```json\n{"tool": "get_function_body", "symbol_id": "executeQuery"}\n```'
            else:
                return '```json\n{"is_sink": true, "reason": "Executes SQL query", "category": "database_write"}\n```'

        analyzer = SinkAnalyzer(
            mcp_server=mcp_server,
            llm_request_fn=mock_llm,
            rate_limit=1000,
            max_workers=1,
        )

        result = asyncio.run(analyzer._analyze_single_function("executeQuery"))
        assert result == (True, "Executes SQL query", "database_write")

    def test_immediate_answer_no_tools(self, mcp_server):
        async def mock_llm(system, messages):
            return '```json\n{"is_sink": false, "reason": "Pure computation", "category": "none"}\n```'

        analyzer = SinkAnalyzer(
            mcp_server=mcp_server,
            llm_request_fn=mock_llm,
            rate_limit=1000,
            max_workers=1,
        )

        result = asyncio.run(analyzer._analyze_single_function("computeHash"))
        assert result == (False, "Pure computation", "none")

    def test_max_iterations_forces_answer(self, mcp_server):
        call_count = [0]

        async def mock_llm(system, messages):
            call_count[0] += 1
            if call_count[0] <= 10:
                return '```json\n{"tool": "get_callers", "symbol_id": "executeQuery"}\n```'
            else:
                return '```json\n{"is_sink": true, "reason": "Database write", "category": "database_write"}\n```'

        analyzer = SinkAnalyzer(
            mcp_server=mcp_server,
            llm_request_fn=mock_llm,
            rate_limit=1000,
            max_workers=1,
            max_tool_iterations=10,
        )

        result = asyncio.run(analyzer._analyze_single_function("executeQuery"))
        assert result == (True, "Database write", "database_write")

    def test_empty_response_returns_default(self, mcp_server):
        async def mock_llm(system, messages):
            return ""

        analyzer = SinkAnalyzer(
            mcp_server=mcp_server,
            llm_request_fn=mock_llm,
            rate_limit=1000,
            max_workers=1,
        )

        result = asyncio.run(analyzer._analyze_single_function("computeHash"))
        assert result[0] is False

    def test_llm_exception_returns_default(self, mcp_server):
        async def mock_llm(system, messages):
            raise RuntimeError("API timeout")

        analyzer = SinkAnalyzer(
            mcp_server=mcp_server,
            llm_request_fn=mock_llm,
            rate_limit=1000,
            max_workers=1,
        )

        result = asyncio.run(analyzer._analyze_single_function("computeHash"))
        assert result[0] is False
        assert "failed" in result[1].lower()


# --- analyze_all and annotate_call_tree Tests ---

class TestSinkAnalyzeAllAndAnnotate:

    def test_analyze_all_processes_all_functions(self, mcp_server):
        async def mock_llm(system, messages):
            user_msg = messages[0]["content"]
            if "executeQuery" in user_msg:
                return '```json\n[{"id": "PLACEHOLDER", "is_sink": true, "reason": "SQL execution", "category": "database_write"}]\n```'
            return '```json\n[{"id": "PLACEHOLDER", "is_sink": false, "reason": "Internal", "category": "none"}]\n```'

        analyzer = SinkAnalyzer(
            mcp_server=mcp_server,
            llm_request_fn=mock_llm,
            rate_limit=1000,
            max_workers=2,
        )

        results = asyncio.run(analyzer.analyze_all(["handleHTTPRequest", "executeQuery", "computeHash"]))
        assert len(results) == 3

    def test_annotate_call_tree_adds_sink_fields(self, mcp_server, sample_call_tree):
        analyzer = SinkAnalyzer(
            mcp_server=mcp_server,
            llm_request_fn=AsyncMock(return_value=""),
            rate_limit=1000,
            max_workers=1,
        )
        # Manually set results
        analyzer._results = {
            "ROOT": (False, "", "none"),
            "handleHTTPRequest": (False, "Not a sink, it's a source", "none"),
            "executeQuery": (True, "Executes SQL query", "database_write"),
            "computeHash": (False, "Pure computation", "none"),
        }

        annotated = analyzer.annotate_call_tree(sample_call_tree)

        root = annotated["call_tree"]
        assert "is_sink" in root
        assert "sink_category" in root

        # handleHTTPRequest
        handler_node = root["children"][0]
        assert handler_node["is_sink"] is False

        # executeQuery
        query_node = handler_node["children"][0]
        assert query_node["is_sink"] is True
        assert query_node["sink_category"] == "database_write"

        # computeHash
        hash_node = root["children"][1]
        assert hash_node["is_sink"] is False

        # metadata
        meta = annotated["metadata"]["sink_analysis"]
        assert meta["total_functions_analyzed"] == 4
        assert meta["functions_classified_as_sinks"] == 1

    def test_annotate_preserves_original_metadata(self, mcp_server, sample_call_tree):
        analyzer = SinkAnalyzer(
            mcp_server=mcp_server,
            llm_request_fn=AsyncMock(return_value=""),
            rate_limit=1000,
            max_workers=1,
        )
        analyzer._results = {}

        annotated = analyzer.annotate_call_tree(sample_call_tree)
        assert annotated["metadata"]["total_functions"] == 3
        assert annotated["metadata"]["total_root_nodes"] == 2


# --- DataFlowAnalysisRunner Integration Tests ---

class TestDataFlowAnalysisRunnerSinkDiscovery:
    """Tests for _run_sink_discovery_with_llm in DataFlowAnalysisRunner."""

    @pytest.fixture
    def runner_setup(self, sample_call_graph_data, temp_repo, sample_call_tree):
        from hindsight.analyzers.data_flow_analyzer import DataFlowAnalysisRunner
        from hindsight.core.constants import NESTED_CALL_GRAPH_FILE

        ast_dir = Path(temp_repo) / "code_insights"
        ast_dir.mkdir()
        call_graph_path = ast_dir / NESTED_CALL_GRAPH_FILE
        with open(call_graph_path, 'w') as f:
            json.dump(sample_call_graph_data, f)

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
    def test_produces_data_sinks_json(
        self, mock_get_api_key, mock_get_provider_type, mock_create_provider, runner_setup
    ):
        runner, config, call_tree, data_flow_dir = runner_setup

        mock_get_api_key.return_value = "test-key"
        mock_get_provider_type.return_value = "aws_bedrock"

        mock_provider = MagicMock()
        mock_provider.create_payload.return_value = {}
        mock_provider.make_request.return_value = {
            "choices": [{"message": {"content":
                '```json\n{"is_sink": false, "reason": "Not a sink", "category": "none"}\n```'
            }}]
        }
        mock_create_provider.return_value = mock_provider

        with patch.object(runner, 'get_default_data_flow_paths', return_value={
            'data_flow_dir': str(data_flow_dir),
            'call_tree_json': str(data_flow_dir / "call_tree.json"),
            'call_tree_text': str(data_flow_dir / "call_tree.txt"),
            'statistics_file': str(data_flow_dir / "call_graph_statistics.json"),
        }):
            result = runner._run_sink_discovery_with_llm(config, call_tree, max_workers=2)

        # Verify output files
        sinks_path = data_flow_dir / "data_sinks.json"
        assert sinks_path.exists()

        with open(sinks_path) as f:
            sinks = json.load(f)

        assert isinstance(sinks, list)

        # Cache should also exist
        cache_path = data_flow_dir / "sink_cache.json"
        assert cache_path.exists()

    @patch('hindsight.core.llm.llm.create_llm_provider')
    @patch('hindsight.analyzers.data_flow_analyzer.get_llm_provider_type')
    @patch('hindsight.analyzers.data_flow_analyzer.get_api_key_from_config')
    def test_sinks_have_correct_schema(
        self, mock_get_api_key, mock_get_provider_type, mock_create_provider, runner_setup
    ):
        """Each entry in data_sinks.json has function, location, reason, category."""
        runner, config, call_tree, data_flow_dir = runner_setup

        mock_get_api_key.return_value = "test-key"
        mock_get_provider_type.return_value = "aws_bedrock"

        mock_provider = MagicMock()
        mock_provider.create_payload.return_value = {}
        # Return is_sink=true for everything so we can inspect the schema
        mock_provider.make_request.return_value = {
            "choices": [{"message": {"content":
                '```json\n{"is_sink": true, "reason": "Writes data", "category": "file_system_write"}\n```'
            }}]
        }
        mock_create_provider.return_value = mock_provider

        with patch.object(runner, 'get_default_data_flow_paths', return_value={
            'data_flow_dir': str(data_flow_dir),
            'call_tree_json': str(data_flow_dir / "call_tree.json"),
            'call_tree_text': str(data_flow_dir / "call_tree.txt"),
            'statistics_file': str(data_flow_dir / "call_graph_statistics.json"),
        }):
            result = runner._run_sink_discovery_with_llm(config, call_tree, max_workers=2)

        with open(data_flow_dir / "data_sinks.json") as f:
            sinks = json.load(f)

        assert len(sinks) > 0
        for entry in sinks:
            assert "function" in entry
            assert "location" in entry
            assert isinstance(entry["location"], list)
            assert "reason" in entry
            assert isinstance(entry["reason"], str)
            assert "category" in entry
            assert isinstance(entry["category"], str)

    def test_domain_understanding_is_noop(self, runner_setup):
        """_run_sink_discovery_with_domain_understanding returns input unchanged."""
        runner, config, call_tree, data_flow_dir = runner_setup

        sink_results = {
            "all_results": {"foo": (True, "test", "database_write")},
            "sink_functions": [{"function": "foo", "location": [], "reason": "test", "category": "database_write"}],
        }

        result = runner._run_sink_discovery_with_domain_understanding(config, call_tree, sink_results)
        assert result is sink_results


# --- Prompt Content Tests ---

class TestSinkPromptContent:
    """Verify that prompts contain expected sink categories."""

    def _make_analyzer(self, mcp_server):
        async def dummy_llm(system, messages):
            return ""
        return SinkAnalyzer(mcp_server=mcp_server, llm_request_fn=dummy_llm)

    def test_batch_prompt_contains_all_categories(self, mcp_server):
        analyzer = self._make_analyzer(mcp_server)
        prompt = analyzer._build_batch_system_prompt()

        expected_categories = [
            "process_execution",
            "file_system_write",
            "network_output",
            "database_write",
            "memory_operation",
            "deserialization",
            "authentication_authorization",
            "cryptographic_operation",
            "system_state_modification",
            "ipc_output",
            "logging_with_user_data",
            "dynamic_dispatch",
            "url_path_construction",
            "query_construction",
            "markup_generation",
            "privilege_boundary",
            "resource_allocation",
            "notification_broadcast",
        ]

        for cat in expected_categories:
            assert cat in prompt, f"Missing category in prompt: {cat}"

    def test_prompt_is_language_agnostic(self, mcp_server):
        """Prompt should not reference specific OS/language APIs exclusively."""
        analyzer = self._make_analyzer(mcp_server)
        prompt = analyzer._build_batch_system_prompt()

        # Should contain "Examples across languages" or similar phrasing
        # Should NOT be exclusively about one language
        assert "is_sink" in prompt
        assert "category" in prompt
        # The prompt should mention generic concepts
        assert "attacker-controlled" in prompt.lower() or "attacker" in prompt.lower()
