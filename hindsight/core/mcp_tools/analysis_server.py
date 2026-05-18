#!/usr/bin/env python3
"""
Unified Analysis MCP Server — wraps Tools + optional CodeNavigationServer.

Provides a single dispatch point for all analysis tools used by the LLM,
with support for tool filtering (e.g., Stage B restrictions).

Usage:
    server = AnalysisMCPServer(repo_path, file_content_provider=fcp, ...)
    result = server.execute_tool("readFile", {"path": "src/main.swift"})
"""

import json
from typing import Any, Dict, List, Optional, Set

from fastmcp import FastMCP

from ..llm.tools.tools import Tools
from ..llm.tools.tool_definitions import TOOL_DEFINITIONS
from .code_navigation_server import CodeNavigationServer
from ...utils.log_util import get_logger

logger = get_logger(__name__)

# Tool names provided by the Tools class
TOOLS_BASED_TOOL_NAMES = {
    "readFile",
    "getFileContentByLines",
    "checkFileSize",
    "runTerminalCmd",
    "list_files",
    "inspectDirectoryHierarchy",
    "getImplementation",
    "getSummaryOfFile",
}

# Tool names provided by CodeNavigationServer
CODE_NAV_TOOL_NAMES = {
    "search_symbol",
    "get_symbol",
    "get_function_body",
    "get_file_ast",
    "get_callers",
    "get_callees",
    "find_references",
}

# Stage B tool restriction
STAGE_B_TOOLS = {"readFile", "runTerminalCmd", "getFileContentByLines"}


class AnalysisMCPServer:
    """
    Unified MCP server combining Tools and CodeNavigationServer capabilities.

    This class provides a single interface for all LLM tool execution during
    code analysis. It wraps the existing Tools class (always) and optionally
    the CodeNavigationServer (when call graph data is available).

    Tool filtering via `allowed_tools` enables stage-based restrictions
    (e.g., Stage B only allows readFile, runTerminalCmd, getFileContentByLines).
    """

    def __init__(
        self,
        repo_path: str,
        file_content_provider=None,
        artifacts_dir: str = None,
        directory_tree_util=None,
        ignore_dirs: set = None,
        call_graph_data=None,
        graph=None,
        implementations=None,
        allowed_tools: Optional[Set[str]] = None,
    ):
        """
        Initialize the unified analysis MCP server.

        Args:
            repo_path: Path to the repository root
            file_content_provider: Optional FileContentProvider for file resolution
            artifacts_dir: Path to the artifacts directory (optional)
            directory_tree_util: Optional DirectoryTreeUtil for directory listing
            ignore_dirs: Set of directory names to ignore (optional)
            call_graph_data: Optional call graph JSON data (enables code nav tools)
            graph: Optional pre-built CallGraph instance
            implementations: Optional pre-extracted implementations dict
            allowed_tools: Set of tool names that are allowed (None = all allowed)
        """
        self.repo_path = repo_path
        self._allowed_tools = allowed_tools

        # Store constructor params for creating copies (e.g., with_stage_b_tools)
        self._init_kwargs = {
            "repo_path": repo_path,
            "file_content_provider": file_content_provider,
            "artifacts_dir": artifacts_dir,
            "directory_tree_util": directory_tree_util,
            "ignore_dirs": ignore_dirs,
            "call_graph_data": call_graph_data,
            "graph": graph,
            "implementations": implementations,
        }

        # Always create a Tools instance
        self._tools = Tools(
            repo_path=repo_path,
            file_content_provider=file_content_provider,
            artifacts_dir=artifacts_dir,
            directory_tree_util=directory_tree_util,
            ignore_dirs=ignore_dirs,
        )

        # Optionally create CodeNavigationServer (only when call graph data or graph provided)
        self._code_nav_server: Optional[CodeNavigationServer] = None
        if call_graph_data is not None or graph is not None:
            self._code_nav_server = CodeNavigationServer(
                repo_path=repo_path,
                call_graph_data=call_graph_data,
                implementations=implementations,
                graph=graph,
            )

        # Create the FastMCP instance with all tools registered
        self.mcp = FastMCP("analysis")
        self._register_tools()

        available_count = len(self.get_available_tool_names())
        logger.info(
            f"AnalysisMCPServer initialized: {available_count} tools available, "
            f"code_nav={'enabled' if self._code_nav_server else 'disabled'}"
        )

    def _register_tools(self) -> None:
        """Register all tools with the FastMCP instance."""

        # --- Tools-based tools ---

        @self.mcp.tool()
        def readFile(path: str) -> str:
            """Read file contents with automatic pruning for large files."""
            return self.execute_tool("readFile", {"path": path})

        @self.mcp.tool()
        def getFileContentByLines(path: str, startLine: int, endLine: int, reason: str = "") -> str:
            """Read specific line ranges from a file."""
            return self.execute_tool("getFileContentByLines", {
                "path": path, "startLine": startLine, "endLine": endLine, "reason": reason
            })

        @self.mcp.tool()
        def checkFileSize(path: str, reason: str = "") -> str:
            """Check if a file exists and get its size and line count."""
            return self.execute_tool("checkFileSize", {"path": path, "reason": reason})

        @self.mcp.tool()
        def runTerminalCmd(command: str, reason: str = "") -> str:
            """Execute safe terminal commands with validation."""
            return self.execute_tool("runTerminalCmd", {"command": command, "reason": reason})

        @self.mcp.tool()
        def list_files(path: str, recursive: bool = False, reason: str = "") -> str:
            """List directory contents with file sizes."""
            return self.execute_tool("list_files", {"path": path, "recursive": recursive, "reason": reason})

        @self.mcp.tool()
        def inspectDirectoryHierarchy(path: str = "", reason: str = "") -> str:
            """Get directory structure information."""
            return self.execute_tool("inspectDirectoryHierarchy", {"path": path, "reason": reason})

        @self.mcp.tool()
        def getImplementation(name: str, reason: str = "") -> str:
            """Retrieve class or function implementations from the code registry."""
            return self.execute_tool("getImplementation", {"name": name, "reason": reason})

        @self.mcp.tool()
        def getSummaryOfFile(path: str, reason: str = "") -> str:
            """Generate file summary using CodeContextPruner."""
            return self.execute_tool("getSummaryOfFile", {"path": path, "reason": reason})

        # --- CodeNavigation tools (only when available) ---

        if self._code_nav_server is not None:
            @self.mcp.tool()
            def search_symbol(query: str) -> str:
                """Search for symbols (functions/methods) matching a query string."""
                return self.execute_tool("search_symbol", {"query": query})

            @self.mcp.tool()
            def get_symbol(symbol_id: str) -> str:
                """Get detailed information about a specific symbol."""
                return self.execute_tool("get_symbol", {"symbol_id": symbol_id})

            @self.mcp.tool()
            def get_function_body(symbol_id: str) -> str:
                """Read the source code body of a function."""
                return self.execute_tool("get_function_body", {"symbol_id": symbol_id})

            @self.mcp.tool()
            def get_file_ast(file_path: str, max_depth: int = 2) -> str:
                """Get a summary of functions defined in a file."""
                return self.execute_tool("get_file_ast", {"file_path": file_path, "max_depth": max_depth})

            @self.mcp.tool()
            def get_callers(symbol_id: str) -> str:
                """Get all functions that call the given symbol."""
                return self.execute_tool("get_callers", {"symbol_id": symbol_id})

            @self.mcp.tool()
            def get_callees(symbol_id: str) -> str:
                """Get all functions called by the given symbol."""
                return self.execute_tool("get_callees", {"symbol_id": symbol_id})

            @self.mcp.tool()
            def find_references(symbol_id: str) -> str:
                """Find all references to a symbol."""
                return self.execute_tool("find_references", {"symbol_id": symbol_id})

    @property
    def tools(self) -> Tools:
        """
        Expose the internal Tools instance for backward compatibility.

        The iterative analyzer framework calls tools_executor.tools.execute_tool_use(),
        so any object passed as tools_executor must expose a .tools attribute with an
        execute_tool_use() method. This property allows AnalysisMCPServer to be used
        directly as a tools_executor.
        """
        return self._tools

    def execute_tool_use(self, tool_use_block: Dict[str, Any]) -> str:
        """
        Execute a tool_use block, routing to both Tools-based and CodeNavigation tools.

        This allows AnalysisMCPServer to be used directly as a `tools` object in the
        iterative analysis framework (which calls tools_executor.tools.execute_tool_use()).

        Args:
            tool_use_block: Tool call block containing:
                - name: name of the tool to execute
                - input: dictionary of tool parameters
                - id: unique identifier (optional)

        Returns:
            str: Tool execution result or error message
        """
        tool_name = tool_use_block.get("name", "unknown")
        tool_input = tool_use_block.get("input", {})

        if tool_name in CODE_NAV_TOOL_NAMES and self._code_nav_server is not None:
            return self.execute_tool(tool_name, tool_input)

        return self._tools.execute_tool_use(tool_use_block)

    def log_tool_usage_summary(self) -> None:
        """Delegate to the internal Tools instance."""
        self._tools.log_tool_usage_summary()

    def execute_tool(self, tool_name: str, params: Dict[str, Any]) -> str:
        """
        Execute a tool by name with given parameters.

        Enforces the allowed_tools filter, then routes to the appropriate
        underlying implementation (Tools or CodeNavigationServer).

        Args:
            tool_name: Name of the tool to execute
            params: Dictionary of tool parameters

        Returns:
            str: Tool execution result (typically JSON) or error message
        """
        # Enforce allowed_tools filter
        if self._allowed_tools is not None and tool_name not in self._allowed_tools:
            error_msg = (
                f"Error: Tool '{tool_name}' is not available in this context. "
                f"Allowed tools: {', '.join(sorted(self._allowed_tools))}"
            )
            logger.warning(f"[AnalysisMCPServer] {error_msg}")
            return error_msg

        # Route to Tools-based implementation
        if tool_name in TOOLS_BASED_TOOL_NAMES:
            tool_use_block = {
                "name": tool_name,
                "input": params,
                "id": f"analysis_mcp_{tool_name}",
            }
            return self._tools.execute_tool_use(tool_use_block)

        # Route to CodeNavigationServer
        if tool_name in CODE_NAV_TOOL_NAMES:
            if self._code_nav_server is None:
                return json.dumps({
                    "error": f"Tool '{tool_name}' requires call graph data which is not available"
                })
            return self._code_nav_server.execute_tool(tool_name, params)

        # Unknown tool
        all_tools = sorted(TOOLS_BASED_TOOL_NAMES | CODE_NAV_TOOL_NAMES)
        error_msg = f"Error: Unknown tool '{tool_name}'. Available tools: {', '.join(all_tools)}"
        logger.error(f"[AnalysisMCPServer] {error_msg}")
        return error_msg

    def get_tool_descriptions(self) -> str:
        """
        Return markdown describing all currently-allowed tools for prompt injection.

        Combines Tools descriptions and CodeNavigation descriptions,
        respecting the allowed_tools filter.

        Returns:
            str: Formatted markdown string describing available tools
        """
        lines = ["# Available Analysis Tools", ""]

        # Tools-based tool descriptions
        tools_section_lines = []
        for tool_name, tool_def in TOOL_DEFINITIONS.items():
            if self._allowed_tools is not None and tool_name not in self._allowed_tools:
                continue
            desc = tool_def["description"]
            params_desc = []
            for param_name, param_info in tool_def["parameters"].items():
                required = " (required)" if param_info.get("required") else ""
                params_desc.append(f"  - `{param_name}`: {param_info['description']}{required}")

            tools_section_lines.append(f"## {tool_name}")
            tools_section_lines.append(f"{desc}")
            tools_section_lines.append("")
            tools_section_lines.append("Parameters:")
            tools_section_lines.extend(params_desc)
            tools_section_lines.append("")
            tools_section_lines.append(
                f'```json {{"tool": "{tool_name}", '
                + ", ".join(f'"{p}": "<value>"' for p in tool_def["parameters"])
                + "}"
                + "```"
            )
            tools_section_lines.append("")

        if tools_section_lines:
            lines.extend(tools_section_lines)

        # CodeNavigation tool descriptions (when available)
        if self._code_nav_server is not None:
            code_nav_descriptions = self._get_code_nav_tool_descriptions()
            if code_nav_descriptions:
                lines.append("## Code Navigation Tools")
                lines.append("")
                lines.extend(code_nav_descriptions)

        return "\n".join(lines)

    def _get_code_nav_tool_descriptions(self) -> List[str]:
        """Get descriptions for code navigation tools, respecting allowed_tools filter."""
        lines = []

        code_nav_tools = [
            ("search_symbol", "Search for functions/methods by name substring.",
             '{"tool": "search_symbol", "query": "<search_term>"}'),
            ("get_symbol", "Get full info about a symbol (locations, callers, callees).",
             '{"tool": "get_symbol", "symbol_id": "<function_name>"}'),
            ("get_function_body", "Read the source code of a function.",
             '{"tool": "get_function_body", "symbol_id": "<function_name>"}'),
            ("get_file_ast", "List all functions defined in a file.",
             '{"tool": "get_file_ast", "file_path": "<path>"}'),
            ("get_callers", "Get all functions that call a given function.",
             '{"tool": "get_callers", "symbol_id": "<function_name>"}'),
            ("get_callees", "Get all functions called by a given function.",
             '{"tool": "get_callees", "symbol_id": "<function_name>"}'),
            ("find_references", "Find all references (callers + callees + implementation sites).",
             '{"tool": "find_references", "symbol_id": "<function_name>"}'),
        ]

        for tool_name, description, example in code_nav_tools:
            if self._allowed_tools is not None and tool_name not in self._allowed_tools:
                continue
            lines.append(f"### {tool_name}")
            lines.append(f"{description}")
            lines.append(f"```json {example}```")
            lines.append("")

        return lines

    def with_stage_b_tools(self) -> 'AnalysisMCPServer':
        """
        Return a new AnalysisMCPServer instance restricted to Stage B tools only.

        Stage B tools: readFile, runTerminalCmd, getFileContentByLines

        Returns:
            A new AnalysisMCPServer with allowed_tools restricted to Stage B set.
        """
        return AnalysisMCPServer(
            **self._init_kwargs,
            allowed_tools=STAGE_B_TOOLS.copy(),
        )

    def get_available_tool_names(self) -> List[str]:
        """
        Return names of tools currently available (after filtering).

        Returns:
            List of available tool names
        """
        all_tools = list(TOOLS_BASED_TOOL_NAMES)
        if self._code_nav_server is not None:
            all_tools.extend(CODE_NAV_TOOL_NAMES)

        if self._allowed_tools is not None:
            all_tools = [t for t in all_tools if t in self._allowed_tools]

        return sorted(all_tools)
