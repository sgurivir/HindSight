#!/usr/bin/env python3
"""
Tests for hindsight/core/mcp_tools/analysis_server.py

Tests the unified AnalysisMCPServer which wraps both the Tools class
and optionally the CodeNavigationServer:
- Initialization with and without call graph data
- execute_tool dispatch for Tools-based and CodeNavigation tools
- allowed_tools filtering (Stage B restrictions)
- with_stage_b_tools() returns restricted instance
- get_tool_descriptions() respects allowed_tools
- get_available_tool_names() correctness
- Unknown tool returns error
"""

import json
import os
import tempfile
import pytest
from pathlib import Path

from hindsight.core.mcp_tools.analysis_server import (
    AnalysisMCPServer,
    TOOLS_BASED_TOOL_NAMES,
    CODE_NAV_TOOL_NAMES,
    STAGE_B_TOOLS,
)


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
                        "end": 30,
                    },
                    "functions_invoked": [
                        {
                            "function": "parseBody",
                            "context": {
                                "file": "src/network/parser.swift",
                                "start": 5,
                                "end": 20,
                            },
                            "functions_invoked": [
                                {
                                    "function": "validateJSON",
                                    "context": {
                                        "file": "src/utils/json_util.swift",
                                        "start": 1,
                                        "end": 15,
                                    },
                                    "functions_invoked": [],
                                }
                            ],
                        },
                        {
                            "function": "sendResponse",
                            "context": {
                                "file": "src/network/http_handler.swift",
                                "start": 35,
                                "end": 50,
                            },
                            "functions_invoked": [],
                        },
                    ],
                }
            ],
        },
        {
            "file": "src/utils/json_util.swift",
            "functions": [
                {
                    "function": "validateJSON",
                    "context": {
                        "file": "src/utils/json_util.swift",
                        "start": 1,
                        "end": 15,
                    },
                    "functions_invoked": [],
                },
                {
                    "function": "formatOutput",
                    "context": {
                        "file": "src/utils/json_util.swift",
                        "start": 20,
                        "end": 35,
                    },
                    "functions_invoked": [],
                },
            ],
        },
    ]


@pytest.fixture
def temp_repo():
    """Create a temp repo with source files matching the call graph data."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create source files for call graph
        network_dir = Path(tmpdir) / "src" / "network"
        network_dir.mkdir(parents=True)

        handler_content = "\n".join(
            [f"// line {i}" for i in range(1, 10)]
            + [
                "func handleRequest(request: HTTPRequest) -> HTTPResponse {",
                "    let body = parseBody(request.data)",
                "    let validated = validateJSON(body)",
                "    return sendResponse(validated)",
                "}",
            ]
            + [f"// line {i}" for i in range(15, 55)]
        )
        (network_dir / "http_handler.swift").write_text(handler_content)

        parser_content = "\n".join(
            [f"// line {i}" for i in range(1, 5)]
            + [
                "func parseBody(data: Data) -> String {",
                "    let json = validateJSON(data)",
                "    return json",
                "}",
            ]
            + [f"// line {i}" for i in range(9, 25)]
        )
        (network_dir / "parser.swift").write_text(parser_content)

        utils_dir = Path(tmpdir) / "src" / "utils"
        utils_dir.mkdir(parents=True)

        json_content = "func validateJSON(data: Data) -> Bool {\n    return true\n}\n" + (
            "\n".join([f"// line {i}" for i in range(4, 40)])
        )
        (utils_dir / "json_util.swift").write_text(json_content)

        # Create a simple file for readFile testing (use .swift so CodeContextPruner keeps content)
        (Path(tmpdir) / "hello.swift").write_text(
            "import Foundation\n\nfunc hello() {\n    print(\"Hello, World!\")\n}\n"
        )
        # Also create a plain text file for getFileContentByLines testing
        (Path(tmpdir) / "hello.txt").write_text("Hello, World!\nLine 2\nLine 3\n")

        yield tmpdir


@pytest.fixture
def server_with_nav(sample_call_graph_data, temp_repo):
    """Create AnalysisMCPServer with call graph data (all tools enabled)."""
    return AnalysisMCPServer(
        repo_path=temp_repo,
        call_graph_data=sample_call_graph_data,
    )


@pytest.fixture
def server_without_nav(temp_repo):
    """Create AnalysisMCPServer without call graph data (only Tools-based tools)."""
    return AnalysisMCPServer(
        repo_path=temp_repo,
    )


class TestInitialization:
    """Tests for AnalysisMCPServer initialization."""

    def test_init_without_call_graph(self, server_without_nav):
        """Server initializes correctly without call graph data."""
        assert server_without_nav._tools is not None
        assert server_without_nav._code_nav_server is None
        assert server_without_nav.mcp is not None

    def test_init_with_call_graph(self, server_with_nav):
        """Server initializes correctly with call graph data."""
        assert server_with_nav._tools is not None
        assert server_with_nav._code_nav_server is not None
        assert server_with_nav.mcp is not None

    def test_init_with_allowed_tools(self, temp_repo):
        """Server initializes with restricted allowed_tools set."""
        server = AnalysisMCPServer(
            repo_path=temp_repo,
            allowed_tools={"readFile", "runTerminalCmd"},
        )
        assert server._allowed_tools == {"readFile", "runTerminalCmd"}

    def test_init_stores_repo_path(self, server_with_nav, temp_repo):
        """Server stores repo_path correctly."""
        assert server_with_nav.repo_path == temp_repo

    def test_init_with_graph_object(self, sample_call_graph_data, temp_repo):
        """Server initializes with pre-built graph object."""
        from hindsight.core.lang_util.call_graph_util import load_call_graph_from_json

        graph = load_call_graph_from_json(sample_call_graph_data)
        server = AnalysisMCPServer(
            repo_path=temp_repo,
            call_graph_data=sample_call_graph_data,
            graph=graph,
        )
        assert server._code_nav_server is not None
        assert server._code_nav_server.graph is graph


class TestExecuteToolForToolsBased:
    """Tests for execute_tool routing to Tools class."""

    def test_read_file(self, server_without_nav, temp_repo):
        """readFile tool reads file contents correctly."""
        result = server_without_nav.execute_tool("readFile", {"path": "hello.swift"})
        # readFile applies CodeContextPruner — check for function signature
        assert "func hello()" in result

    def test_check_file_size(self, server_without_nav, temp_repo):
        """checkFileSize tool returns file info."""
        result = server_without_nav.execute_tool("checkFileSize", {"path": "hello.txt"})
        assert "hello.txt" in result or "line_count" in result

    def test_list_files_without_directory_tree_util(self, server_without_nav, temp_repo):
        """list_files tool returns error when DirectoryTreeUtil is not provided."""
        result = server_without_nav.execute_tool("list_files", {"path": "src"})
        # Without DirectoryTreeUtil, this tool returns an error (expected behavior)
        assert "Error" in result or "DirectoryTreeUtil" in result

    def test_list_files_dispatches_correctly(self, temp_repo):
        """list_files tool dispatches to the Tools instance correctly."""
        from unittest.mock import MagicMock

        # Create a mock directory_tree_util
        mock_dtu = MagicMock()
        mock_dtu.get_directory_listing.return_value = "network/\nutils/"
        server = AnalysisMCPServer(
            repo_path=temp_repo,
            directory_tree_util=mock_dtu,
        )
        result = server.execute_tool("list_files", {"path": "src"})
        assert "network" in result or "utils" in result

    def test_get_file_content_by_lines(self, server_without_nav, temp_repo):
        """getFileContentByLines reads specific lines."""
        result = server_without_nav.execute_tool(
            "getFileContentByLines",
            {"path": "hello.txt", "startLine": 1, "endLine": 2},
        )
        assert "Hello, World!" in result

    def test_run_terminal_cmd(self, server_without_nav, temp_repo):
        """runTerminalCmd executes safe commands."""
        result = server_without_nav.execute_tool(
            "runTerminalCmd", {"command": f"ls {temp_repo}"}
        )
        assert "hello.txt" in result


class TestExecuteToolForCodeNavigation:
    """Tests for execute_tool routing to CodeNavigationServer."""

    def test_search_symbol(self, server_with_nav):
        """search_symbol finds matching symbols."""
        result = json.loads(
            server_with_nav.execute_tool("search_symbol", {"query": "handle"})
        )
        assert "results" in result
        assert len(result["results"]) >= 1
        symbols = [r["symbol"] for r in result["results"]]
        assert "handleRequest" in symbols

    def test_get_callers(self, server_with_nav):
        """get_callers returns caller functions."""
        result = json.loads(
            server_with_nav.execute_tool("get_callers", {"symbol_id": "parseBody"})
        )
        assert "handleRequest" in result["callers"]

    def test_get_callees(self, server_with_nav):
        """get_callees returns callee functions."""
        result = json.loads(
            server_with_nav.execute_tool("get_callees", {"symbol_id": "handleRequest"})
        )
        assert "parseBody" in result["callees"]
        assert "sendResponse" in result["callees"]

    def test_get_function_body(self, server_with_nav):
        """get_function_body reads function source."""
        result = json.loads(
            server_with_nav.execute_tool(
                "get_function_body", {"symbol_id": "handleRequest"}
            )
        )
        assert "body" in result
        assert "handleRequest" in result["body"]

    def test_find_references(self, server_with_nav):
        """find_references returns callers, callees, and implementations."""
        result = json.loads(
            server_with_nav.execute_tool(
                "find_references", {"symbol_id": "parseBody"}
            )
        )
        assert "callers" in result
        assert "callees" in result
        assert "implementations" in result

    def test_get_symbol(self, server_with_nav):
        """get_symbol returns symbol info."""
        result = json.loads(
            server_with_nav.execute_tool("get_symbol", {"symbol_id": "handleRequest"})
        )
        assert result["symbol"] == "handleRequest"
        assert "locations" in result

    def test_code_nav_tool_without_server_returns_error(self, server_without_nav):
        """Calling a code nav tool without call graph data returns error."""
        result = json.loads(
            server_without_nav.execute_tool("search_symbol", {"query": "test"})
        )
        assert "error" in result
        assert "requires call graph data" in result["error"]


class TestAllowedToolsFiltering:
    """Tests for allowed_tools enforcement."""

    def test_allowed_tool_succeeds(self, temp_repo):
        """A tool in allowed_tools set executes normally."""
        server = AnalysisMCPServer(
            repo_path=temp_repo,
            allowed_tools={"readFile"},
        )
        result = server.execute_tool("readFile", {"path": "hello.swift"})
        assert "func hello()" in result

    def test_disallowed_tool_returns_error(self, temp_repo):
        """A tool NOT in allowed_tools set returns error."""
        server = AnalysisMCPServer(
            repo_path=temp_repo,
            allowed_tools={"readFile"},
        )
        result = server.execute_tool("list_files", {"path": "src"})
        assert "Error" in result
        assert "not available" in result
        assert "list_files" in result

    def test_none_allowed_tools_allows_all(self, server_without_nav, temp_repo):
        """When allowed_tools is None, all tools are allowed."""
        # readFile should work
        result = server_without_nav.execute_tool("readFile", {"path": "hello.swift"})
        assert "func hello()" in result
        # checkFileSize should also work
        result = server_without_nav.execute_tool("checkFileSize", {"path": "hello.swift"})
        assert "file_available" in result

    def test_code_nav_tool_filtered(self, sample_call_graph_data, temp_repo):
        """Code nav tool is blocked when not in allowed_tools."""
        server = AnalysisMCPServer(
            repo_path=temp_repo,
            call_graph_data=sample_call_graph_data,
            allowed_tools={"readFile", "runTerminalCmd"},
        )
        result = server.execute_tool("search_symbol", {"query": "handle"})
        assert "Error" in result
        assert "not available" in result


class TestWithStageBTools:
    """Tests for with_stage_b_tools() method."""

    def test_returns_new_instance(self, server_with_nav):
        """with_stage_b_tools returns a new AnalysisMCPServer."""
        stage_b = server_with_nav.with_stage_b_tools()
        assert stage_b is not server_with_nav
        assert isinstance(stage_b, AnalysisMCPServer)

    def test_restricts_to_stage_b_tools(self, server_with_nav):
        """Stage B instance only allows readFile, runTerminalCmd, getFileContentByLines."""
        stage_b = server_with_nav.with_stage_b_tools()
        assert stage_b._allowed_tools == STAGE_B_TOOLS

    def test_stage_b_allows_read_file(self, server_with_nav, temp_repo):
        """Stage B allows readFile."""
        stage_b = server_with_nav.with_stage_b_tools()
        result = stage_b.execute_tool("readFile", {"path": "hello.swift"})
        assert "func hello()" in result

    def test_stage_b_blocks_list_files(self, server_with_nav):
        """Stage B blocks list_files."""
        stage_b = server_with_nav.with_stage_b_tools()
        result = stage_b.execute_tool("list_files", {"path": "src"})
        assert "Error" in result
        assert "not available" in result

    def test_stage_b_blocks_code_nav_tools(self, server_with_nav):
        """Stage B blocks code navigation tools."""
        stage_b = server_with_nav.with_stage_b_tools()
        result = stage_b.execute_tool("search_symbol", {"query": "handle"})
        assert "Error" in result
        assert "not available" in result

    def test_stage_b_preserves_code_nav_server(self, server_with_nav):
        """Stage B instance still has the code nav server (for potential future use)."""
        stage_b = server_with_nav.with_stage_b_tools()
        # Code nav server is present but tools are filtered
        assert stage_b._code_nav_server is not None


class TestUnknownTool:
    """Tests for unknown tool names."""

    def test_unknown_tool_returns_error(self, server_without_nav):
        """Unknown tool name returns descriptive error."""
        result = server_without_nav.execute_tool("nonexistent_tool", {})
        assert "Error" in result
        assert "Unknown tool" in result
        assert "nonexistent_tool" in result

    def test_unknown_tool_lists_available(self, server_without_nav):
        """Unknown tool error message includes available tool names."""
        result = server_without_nav.execute_tool("bad_tool", {})
        assert "readFile" in result


class TestGetAvailableToolNames:
    """Tests for get_available_tool_names() method."""

    def test_without_nav_returns_tools_only(self, server_without_nav):
        """Without code nav, only Tools-based names are returned."""
        names = server_without_nav.get_available_tool_names()
        assert set(names) == TOOLS_BASED_TOOL_NAMES

    def test_with_nav_returns_all_tools(self, server_with_nav):
        """With code nav, both Tools and CodeNav names are returned."""
        names = server_with_nav.get_available_tool_names()
        expected = TOOLS_BASED_TOOL_NAMES | CODE_NAV_TOOL_NAMES
        assert set(names) == expected

    def test_with_allowed_filter_returns_subset(self, temp_repo):
        """With allowed_tools set, only allowed names are returned."""
        server = AnalysisMCPServer(
            repo_path=temp_repo,
            allowed_tools={"readFile", "runTerminalCmd"},
        )
        names = server.get_available_tool_names()
        assert set(names) == {"readFile", "runTerminalCmd"}

    def test_stage_b_returns_stage_b_names(self, server_with_nav):
        """Stage B instance returns only Stage B tool names."""
        stage_b = server_with_nav.with_stage_b_tools()
        names = stage_b.get_available_tool_names()
        assert set(names) == STAGE_B_TOOLS

    def test_returns_sorted(self, server_without_nav):
        """Tool names are returned in sorted order."""
        names = server_without_nav.get_available_tool_names()
        assert names == sorted(names)


class TestGetToolDescriptions:
    """Tests for get_tool_descriptions() method."""

    def test_includes_tools_descriptions(self, server_without_nav):
        """Descriptions include Tools-based tools."""
        desc = server_without_nav.get_tool_descriptions()
        assert "readFile" in desc
        assert "runTerminalCmd" in desc
        assert "list_files" in desc

    def test_includes_code_nav_descriptions(self, server_with_nav):
        """When code nav is available, descriptions include code nav tools."""
        desc = server_with_nav.get_tool_descriptions()
        assert "search_symbol" in desc
        assert "get_callers" in desc
        assert "get_callees" in desc

    def test_excludes_code_nav_when_not_available(self, server_without_nav):
        """When code nav is not available, descriptions exclude code nav tools."""
        desc = server_without_nav.get_tool_descriptions()
        assert "search_symbol" not in desc
        assert "get_callers" not in desc

    def test_respects_allowed_tools_filter(self, temp_repo):
        """Descriptions respect allowed_tools filter."""
        server = AnalysisMCPServer(
            repo_path=temp_repo,
            allowed_tools={"readFile"},
        )
        desc = server.get_tool_descriptions()
        assert "readFile" in desc
        assert "runTerminalCmd" not in desc
        assert "list_files" not in desc

    def test_stage_b_descriptions(self, server_with_nav):
        """Stage B descriptions only include Stage B tools."""
        stage_b = server_with_nav.with_stage_b_tools()
        desc = stage_b.get_tool_descriptions()
        assert "readFile" in desc
        assert "runTerminalCmd" in desc
        assert "getFileContentByLines" in desc
        # Should not include Stage A-only tools
        assert "getImplementation" not in desc
        assert "search_symbol" not in desc

    def test_returns_string(self, server_without_nav):
        """get_tool_descriptions returns a string."""
        desc = server_without_nav.get_tool_descriptions()
        assert isinstance(desc, str)
        assert len(desc) > 0
