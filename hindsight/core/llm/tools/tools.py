#!/usr/bin/env python3
# Created by Sridhar Gurivireddy
"""
Tools Module - Unified Tools class with OpenAI-compatible tool execution.

This module provides the main Tools class that combines all tool implementations
and provides OpenAI-compatible interface for tool execution.
"""

from typing import Dict, Any, List, Optional

from ....utils.log_util import get_logger
from .base import ToolsBase
from .file_tools import FileToolsMixin
from .terminal_tools import TerminalToolsMixin
from .directory_tools import DirectoryToolsMixin
from .implementation_tools import ImplementationToolsMixin
from .tool_definitions import get_all_openai_function_schemas, get_tool_names, normalize_parameters


logger = get_logger(__name__)


class Tools(
    ToolsBase,
    FileToolsMixin,
    TerminalToolsMixin,
    DirectoryToolsMixin,
    ImplementationToolsMixin
):
    """
    Unified Tools class combining all tool implementations.

    File Tools:
    - readFile: Read file contents with automatic pruning for large files
    - getFileContentByLines: Read specific line ranges from files
    - checkFileSize: Check file existence and size information

    Terminal Tools:
    - runTerminalCmd: Execute safe terminal commands with validation

    Directory Tools:
    - list_files: List directory contents with file sizes
    - inspectDirectoryHierarchy: Get directory structure information

    Implementation Tools:
    - getImplementation: Retrieve class/function implementations from registry
    - getSummaryOfFile: Generate file summaries using CodeContextPruner
    """

    def __init__(
        self,
        repo_path: str,
        override_base_dir: str = None,
        file_content_provider=None,
        artifacts_dir: str = None,
        directory_tree_util=None,
        ignore_dirs: set = None,
    ):
        """
        Initialize Tools with repository path and optional configurations.

        Args:
            repo_path: Path to the repository root for tool access
            override_base_dir: Override base directory for temp files (optional)
            file_content_provider: Optional FileContentProvider instance for efficient file resolution
            artifacts_dir: Path to the artifacts directory where analysis files are stored (optional)
            directory_tree_util: Optional DirectoryTreeUtil instance for directory listing
            ignore_dirs: Set of directory names to ignore during file operations (optional)
        """
        super().__init__(
            repo_path=repo_path,
            override_base_dir=override_base_dir,
            file_content_provider=file_content_provider,
            artifacts_dir=artifacts_dir,
            directory_tree_util=directory_tree_util,
            ignore_dirs=ignore_dirs
        )

        # Store constructor parameters so they can be forwarded to copies (e.g. with_stage_b_tools)
        self._init_repo_path = repo_path
        self._init_override_base_dir = override_base_dir
        self._init_file_content_provider = file_content_provider
        self._init_artifacts_dir = artifacts_dir
        self._init_directory_tree_util = directory_tree_util
        self._init_ignore_dirs = ignore_dirs

        # Allowed tools filter; None means all tools are allowed
        self._allowed_tools: Optional[set] = None

        # Register all tool handlers
        self._register_tool_handlers()

        logger.info(f"Tools initialized with {len(get_tool_names())} available tools")

    def _register_tool_handlers(self):
        """Register all tool handler methods."""
        # File tools
        self.register_tool_handler("readFile", self._handle_read_file)
        self.register_tool_handler("getFileContentByLines", self._handle_get_file_content_by_lines)
        self.register_tool_handler("checkFileSize", self._handle_check_file_size)

        # Terminal tools
        self.register_tool_handler("runTerminalCmd", self._handle_run_terminal_cmd)

        # Directory tools
        self.register_tool_handler("list_files", self._handle_list_files)
        self.register_tool_handler("inspectDirectoryHierarchy", self._handle_inspect_directory_hierarchy)

        # Implementation tools
        self.register_tool_handler("getImplementation", self._handle_get_implementation)
        self.register_tool_handler("getSummaryOfFile", self._handle_get_summary_of_file)

    # ==================== TOOL HANDLERS ====================
    # These methods wrap the mixin implementations to match the expected signatures

    def _handle_read_file(self, path: str, reason: str = None) -> str:
        """Handler for readFile tool."""
        return self.execute_read_file_tool(path)

    def _handle_get_file_content_by_lines(
        self,
        path: str,
        startLine: int,
        endLine: int,
        reason: str = None
    ) -> str:
        """Handler for getFileContentByLines tool."""
        return self.execute_get_file_content_by_lines_tool(path, startLine, endLine, reason)

    def _handle_check_file_size(self, path: str, reason: str = None) -> str:
        """Handler for checkFileSize tool."""
        return self.execute_check_file_size_tool(path, reason)

    def _handle_run_terminal_cmd(self, command: str, reason: str = None) -> str:
        """Handler for runTerminalCmd tool."""
        return self.execute_terminal_cmd_tool(command, reason)

    def _handle_list_files(self, path: str, recursive: bool = False, reason: str = None) -> str:
        """Handler for list_files tool."""
        return self.execute_list_files_tool(path, recursive, reason)

    def _handle_inspect_directory_hierarchy(
        self,
        path: str = None,
        directory: str = None,
        reason: str = None,
        **kwargs
    ) -> str:
        """Handler for inspectDirectoryHierarchy tool.
        
        Accepts both 'path' and 'directory' parameters for flexibility,
        as LLMs may use either parameter name for directory inspection.
        """
        # Use path if provided, otherwise fall back to directory
        actual_path = path or directory
        return self.execute_inspect_directory_hierarchy_tool(actual_path, reason)

    def _handle_get_implementation(self, name: str, reason: str = None) -> str:
        """Handler for getImplementation tool."""
        return self.execute_get_implementation_tool(name, reason)

    def _handle_get_summary_of_file(self, path: str, reason: str = None) -> str:
        """Handler for getSummaryOfFile tool."""
        return self.execute_get_summary_of_file_tool(path, reason)

    # ==================== PUBLIC API ====================

    def get_tools_schema(self) -> List[Dict[str, Any]]:
        """Get tool schemas for all available tools."""
        return get_all_openai_function_schemas()

    def get_available_tools(self) -> List[str]:
        """Get list of all available tool names."""
        return list(get_tool_names())

    def with_stage_b_tools(self) -> 'Tools':
        """
        Return a new Tools instance restricted to Stage B tools only.

        Stage B allowed tools:
            readFile, runTerminalCmd

        Returns:
            A new Tools instance whose execute_tool_use only routes to the above tools.
        """
        stage_b_instance = Tools(
            repo_path=self._init_repo_path,
            override_base_dir=self._init_override_base_dir,
            file_content_provider=self._init_file_content_provider,
            artifacts_dir=self._init_artifacts_dir,
            directory_tree_util=self._init_directory_tree_util,
            ignore_dirs=self._init_ignore_dirs,
        )
        stage_b_instance._allowed_tools = {
            "readFile",
            "runTerminalCmd",
        }
        return stage_b_instance

    def execute_tool_use(self, tool_use_block: Dict[str, Any]) -> str:
        """
        Execute a tool_use block.

        Args:
            tool_use_block: Tool call block containing:
                - name: name of the tool to execute
                - input: dictionary of tool parameters
                - id: unique identifier (optional)

        Returns:
            str: Tool execution result or error message
        """
        try:
            tool_name = tool_use_block.get("name")
            tool_input = tool_use_block.get("input", {})
            tool_id = tool_use_block.get("id", "unknown")

            logger.info(f"[TOOL ORCHESTRATOR] Executing tool '{tool_name}' (id: {tool_id})")
            logger.debug(f"[TOOL ORCHESTRATOR] Tool input: {tool_input}")

            # Enforce allowed-tools filter when set (e.g. Stage B mode)
            if self._allowed_tools is not None and tool_name not in self._allowed_tools:
                error_msg = (
                    f"Error: Tool '{tool_name}' is not available in this context. "
                    f"Allowed tools: {', '.join(sorted(self._allowed_tools))}"
                )
                logger.warning(f"[TOOL ORCHESTRATOR] {error_msg}")
                return error_msg

            # Normalize parameters to handle aliases (e.g., file_path -> path)
            normalized_input = normalize_parameters(tool_name, tool_input)
            logger.debug(f"[TOOL ORCHESTRATOR] Normalized input: {normalized_input}")
            
            # Detect common parameter confusion and return helpful error message
            if tool_name == "getImplementation" and "query" in tool_input:
                return (
                    "Error: getImplementation requires 'name' parameter, not 'query'. "
                    "Use: {\"tool\": \"getImplementation\", \"name\": \"" + str(tool_input.get("query", "")) + "\"}"
                )
            
            # Check if we have a registered handler for this tool
            if tool_name in self._tool_handlers:
                handler = self._tool_handlers[tool_name]
                # Call the handler with the normalized input parameters
                return handler(**normalized_input)
            
            # Fallback: Route to appropriate tool implementation directly
            if tool_name == "readFile":
                path = tool_input.get("path", "")
                if not path:
                    return "Error: readFile tool requires 'path' parameter"
                return self.execute_read_file_tool(path)
                
            elif tool_name == "runTerminalCmd":
                command = tool_input.get("command", "")
                reason = tool_input.get("reason", "")
                if not command:
                    return "Error: runTerminalCmd tool requires 'command' parameter"
                return self.execute_terminal_cmd_tool(command, reason)
                
            elif tool_name == "getImplementation":
                name = tool_input.get("name", "")
                reason = tool_input.get("reason", "")
                if not name:
                    return "Error: getImplementation tool requires 'name' parameter"
                return self.execute_get_implementation_tool(name, reason)
                
            elif tool_name == "getSummaryOfFile":
                path = tool_input.get("path", "")
                reason = tool_input.get("reason", "")
                if not path:
                    return "Error: getSummaryOfFile tool requires 'path' parameter"
                return self.execute_get_summary_of_file_tool(path, reason)
                
            elif tool_name == "inspectDirectoryHierarchy":
                directory_path = tool_input.get("path", "")
                reason = tool_input.get("reason", "")
                return self.execute_inspect_directory_hierarchy_tool(directory_path, reason)
                
            elif tool_name == "list_files":
                path = tool_input.get("path", "")
                recursive = tool_input.get("recursive", False)
                reason = tool_input.get("reason", "")
                if not path:
                    return "Error: list_files tool requires 'path' parameter"
                return self.execute_list_files_tool(path, recursive, reason)
                
            elif tool_name == "getFileContentByLines" or tool_name == "getFileContent":
                # Handle both getFileContentByLines and getFileContent (alias) tool names
                if tool_name == "getFileContent":
                    logger.info(f"[TOOL ORCHESTRATOR] Using getFileContent alias for getFileContentByLines tool")
                
                path = tool_input.get("path", "")
                start_line = tool_input.get("startLine", 1)
                end_line = tool_input.get("endLine", 1)
                reason = tool_input.get("reason", "")
                if not path:
                    return f"Error: {tool_name} tool requires 'path' parameter"
                return self.execute_get_file_content_by_lines_tool(path, start_line, end_line, reason)
                
            elif tool_name == "checkFileSize":
                path = tool_input.get("path", "")
                reason = tool_input.get("reason", "")
                if not path:
                    return "Error: checkFileSize tool requires 'path' parameter"
                return self.execute_check_file_size_tool(path, reason)

            else:
                available = list(get_tool_names())
                error_msg = f"Error: Unknown tool '{tool_name}'. Available tools: {', '.join(available)}"
                logger.error(f"[TOOL ORCHESTRATOR] {error_msg}")
                return error_msg
                
        except Exception as e:
            tool_name = tool_use_block.get("name", "unknown")
            error_msg = f"Error executing tool '{tool_name}': {str(e)}"
            logger.error(f"[TOOL ORCHESTRATOR] {error_msg}")
            return error_msg
