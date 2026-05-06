#!/usr/bin/env python3
"""
Tests for hindsight/core/mcp/code_navigation_server.py

Tests the CodeNavigationServer which provides MCP tools for code navigation:
- search_symbol
- get_symbol
- get_function_body
- get_file_ast
- get_callers
- get_callees
- find_references
- execute_tool dispatch
"""

import json
import os
import tempfile
import pytest
from pathlib import Path

from hindsight.core.mcp_tools.code_navigation_server import CodeNavigationServer


@pytest.fixture
def sample_call_graph_data():
    """Sample call graph data in the merged_call_graph.json format."""
    return [
        {
            "file": "src/network/http_handler.swift",
            "functions": [
                {
                    "function": "handleRequest",
                    "context": {
                        "file": "src/network/http_handler.swift",
                        "start": 10,
                        "end": 30
                    },
                    "functions_invoked": [
                        {
                            "function": "parseBody",
                            "context": {
                                "file": "src/network/parser.swift",
                                "start": 5,
                                "end": 20
                            },
                            "functions_invoked": [
                                {
                                    "function": "validateJSON",
                                    "context": {
                                        "file": "src/utils/json_util.swift",
                                        "start": 1,
                                        "end": 15
                                    },
                                    "functions_invoked": []
                                }
                            ]
                        },
                        {
                            "function": "sendResponse",
                            "context": {
                                "file": "src/network/http_handler.swift",
                                "start": 35,
                                "end": 50
                            },
                            "functions_invoked": []
                        }
                    ]
                }
            ]
        },
        {
            "file": "src/utils/json_util.swift",
            "functions": [
                {
                    "function": "validateJSON",
                    "context": {
                        "file": "src/utils/json_util.swift",
                        "start": 1,
                        "end": 15
                    },
                    "functions_invoked": []
                },
                {
                    "function": "formatOutput",
                    "context": {
                        "file": "src/utils/json_util.swift",
                        "start": 20,
                        "end": 35
                    },
                    "functions_invoked": []
                }
            ]
        }
    ]


@pytest.fixture
def temp_repo_with_files():
    """Create a temp repo with source files matching the call graph data."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create source files
        network_dir = Path(tmpdir) / "src" / "network"
        network_dir.mkdir(parents=True)

        handler_content = "\n".join([
            f"// line {i}" for i in range(1, 10)
        ] + [
            "func handleRequest(request: HTTPRequest) -> HTTPResponse {",
            "    let body = parseBody(request.data)",
            "    let validated = validateJSON(body)",
            "    return sendResponse(validated)",
            "}",
        ] + [f"// line {i}" for i in range(15, 55)])
        (network_dir / "http_handler.swift").write_text(handler_content)

        utils_dir = Path(tmpdir) / "src" / "utils"
        utils_dir.mkdir(parents=True)

        json_content = "func validateJSON(data: Data) -> Bool {\n    return true\n}\n" + (
            "\n".join([f"// line {i}" for i in range(4, 40)])
        )
        (utils_dir / "json_util.swift").write_text(json_content)

        yield tmpdir


@pytest.fixture
def server(sample_call_graph_data, temp_repo_with_files):
    """Create a CodeNavigationServer with sample data."""
    return CodeNavigationServer(
        repo_path=temp_repo_with_files,
        call_graph_data=sample_call_graph_data
    )


class TestCodeNavigationServerInit:
    """Tests for server initialization."""

    def test_initializes_graph(self, server):
        assert server.graph is not None
        assert server.graph.get_num_nodes() > 0

    def test_initializes_implementations(self, server):
        assert "handleRequest" in server.implementations
        assert "parseBody" in server.implementations
        assert "validateJSON" in server.implementations

    def test_builds_file_functions_index(self, server):
        assert len(server._file_functions) > 0
        assert "src/network/http_handler.swift" in server._file_functions


class TestSearchSymbol:
    """Tests for search_symbol tool."""

    def test_finds_matching_symbol(self, server):
        result = json.loads(server.search_symbol("handle"))
        assert "results" in result
        assert len(result["results"]) >= 1
        assert result["results"][0]["symbol"] == "handleRequest"

    def test_case_insensitive_search(self, server):
        result = json.loads(server.search_symbol("HANDLE"))
        assert len(result["results"]) >= 1

    def test_no_results_for_unknown(self, server):
        result = json.loads(server.search_symbol("xyznonexistent"))
        assert result["results"] == []
        assert "message" in result

    def test_limits_to_20_results(self, sample_call_graph_data, temp_repo_with_files):
        # Create data with many functions
        big_data = [{
            "file": "big.swift",
            "functions": [
                {"function": f"func_{i}", "context": {"file": "big.swift", "start": i, "end": i + 1}, "functions_invoked": []}
                for i in range(50)
            ]
        }]
        srv = CodeNavigationServer(temp_repo_with_files, big_data)
        result = json.loads(srv.search_symbol("func"))
        assert len(result["results"]) <= 20


class TestGetSymbol:
    """Tests for get_symbol tool."""

    def test_returns_symbol_info(self, server):
        result = json.loads(server.get_symbol("handleRequest"))
        assert result["symbol"] == "handleRequest"
        assert len(result["locations"]) >= 1
        assert result["callees_count"] >= 2  # parseBody, sendResponse

    def test_returns_callers(self, server):
        result = json.loads(server.get_symbol("parseBody"))
        assert "handleRequest" in result["callers"]

    def test_unknown_symbol_returns_error(self, server):
        result = json.loads(server.get_symbol("nonexistentFunc"))
        assert "error" in result


class TestGetFunctionBody:
    """Tests for get_function_body tool."""

    def test_reads_function_source(self, server):
        result = json.loads(server.get_function_body("handleRequest"))
        assert "body" in result
        assert "handleRequest" in result["body"]
        assert result["start_line"] == 10

    def test_unknown_symbol_returns_error(self, server):
        result = json.loads(server.get_function_body("nonexistent"))
        assert "error" in result

    def test_missing_file_returns_error(self, sample_call_graph_data):
        # Server with repo_path that doesn't contain the files
        with tempfile.TemporaryDirectory() as empty_dir:
            srv = CodeNavigationServer(empty_dir, sample_call_graph_data)
            result = json.loads(srv.get_function_body("handleRequest"))
            assert "error" in result


class TestGetCallers:
    """Tests for get_callers tool."""

    def test_returns_callers(self, server):
        result = json.loads(server.get_callers("parseBody"))
        assert result["symbol"] == "parseBody"
        assert "handleRequest" in result["callers"]
        assert result["count"] >= 1

    def test_root_function_has_no_callers(self, server):
        result = json.loads(server.get_callers("handleRequest"))
        assert result["count"] == 0
        assert result["callers"] == []

    def test_unknown_symbol_returns_error(self, server):
        result = json.loads(server.get_callers("nonexistent"))
        assert "error" in result


class TestGetCallees:
    """Tests for get_callees tool."""

    def test_returns_callees(self, server):
        result = json.loads(server.get_callees("handleRequest"))
        assert "parseBody" in result["callees"]
        assert "sendResponse" in result["callees"]

    def test_leaf_function_has_no_callees(self, server):
        result = json.loads(server.get_callees("sendResponse"))
        assert result["count"] == 0

    def test_unknown_symbol_returns_error(self, server):
        result = json.loads(server.get_callees("nonexistent"))
        assert "error" in result


class TestFindReferences:
    """Tests for find_references tool."""

    def test_returns_all_references(self, server):
        result = json.loads(server.find_references("parseBody"))
        assert "implementations" in result
        assert "callers" in result
        assert "callees" in result
        assert "handleRequest" in result["callers"]

    def test_unknown_symbol_returns_error(self, server):
        result = json.loads(server.find_references("nonexistent"))
        assert "error" in result


class TestGetFileAst:
    """Tests for get_file_ast tool."""

    def test_returns_functions_in_file(self, server):
        result = json.loads(server.get_file_ast("src/utils/json_util.swift"))
        assert "functions" in result
        func_names = [f["function"] for f in result["functions"]]
        assert "validateJSON" in func_names

    def test_unknown_file_returns_error(self, server):
        result = json.loads(server.get_file_ast("nonexistent/file.swift"))
        assert "error" in result


class TestExecuteTool:
    """Tests for the execute_tool dispatch method."""

    def test_dispatches_search_symbol(self, server):
        result = json.loads(server.execute_tool("search_symbol", {"query": "handle"}))
        assert "results" in result

    def test_dispatches_get_callers(self, server):
        result = json.loads(server.execute_tool("get_callers", {"symbol_id": "parseBody"}))
        assert "callers" in result

    def test_unknown_tool_returns_error(self, server):
        result = json.loads(server.execute_tool("unknown_tool", {}))
        assert "error" in result
        assert "Unknown tool" in result["error"]


class TestGetToolDescriptions:
    """Tests for get_tool_descriptions."""

    def test_returns_all_tool_names(self, server):
        desc = server.get_tool_descriptions()
        assert "search_symbol" in desc
        assert "get_symbol" in desc
        assert "get_function_body" in desc
        assert "get_file_ast" in desc
        assert "get_callers" in desc
        assert "get_callees" in desc
        assert "find_references" in desc
