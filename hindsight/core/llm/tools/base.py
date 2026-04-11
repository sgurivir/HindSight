#!/usr/bin/env python3
# Created by Sridhar Gurivireddy
"""
Base Tools Module - OpenAI-compatible tool execution framework.

This module provides:
- Constants for file size limits and validation
- Base class with shared initialization and utility methods
- OpenAI-compatible tool execution interface
"""

import os
import json
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional, Callable

from ...constants import MAX_FILE_CHARACTERS_FOR_READ_FILE
from ....utils.output_directory_provider import get_output_directory_provider
from ....utils.log_util import get_logger
from ..command_validator import CommandValidator
from .tool_definitions import (
    TOOL_DEFINITIONS,
    get_tool_definition,
    normalize_parameters,
    validate_tool_parameters,
    get_all_openai_function_schemas,
)


logger = get_logger(__name__)

# Constants
MAX_FILE_SIZE_BYTES = 1024 * 1024  # 1MB limit
MAX_RESPONSE_SIZE_BYTES = 8 * 1024 * 1024  # 8MB limit for tool responses
MAX_FILE_CHARACTERS = 80000  # 80,000 character limit for getImplementation tool

# Command validation constants
DANGEROUS_OPERATIONS = ['rm', 'mv', 'cp', 'chmod', 'sudo', 'su', 'chown', 'chgrp', 'dd', 'mkfs']
DANGEROUS_REDIRECTS = ['>', '>>', '2>', '&>']
DANGEROUS_CHAINS = [';', '&&', '||']
SAFE_PIPE_COMMANDS = {
    'grep', 'head', 'tail', 'wc', 'sort', 'uniq', 'cut', 'awk', 'sed',
    'cat', 'less', 'more', 'find', 'ls', 'file', 'tree', 'xargs'
}


class ToolsBase:
    """
    Base class for tool implementations providing OpenAI-compatible tool execution.
    
    This class provides:
    - Repository path management
    - File content provider integration
    - Tool usage statistics tracking
    - OpenAI-compatible tool execution interface
    - Common file path validation and resolution methods
    """

    def __init__(
        self,
        repo_path: str,
        override_base_dir: str = None,
        file_content_provider=None,
        artifacts_dir: str = None,
        directory_tree_util=None,
        ignore_dirs: set = None
    ):
        """
        Initialize ToolsBase with repository path and optional configurations.

        Args:
            repo_path: Path to the repository root for tool access
            override_base_dir: Override base directory for temp files (optional)
            file_content_provider: Optional FileContentProvider instance for efficient file resolution
            artifacts_dir: Path to the artifacts directory where analysis files are stored (optional)
            directory_tree_util: Optional DirectoryTreeUtil instance for directory listing
            ignore_dirs: Set of directory names to ignore during file operations (optional)
        """
        self.repo_path = repo_path
        self.override_base_dir = override_base_dir
        self.ignore_dirs = ignore_dirs or set()
        self.file_content_provider = file_content_provider
        self.artifacts_dir = artifacts_dir
        self.directory_tree_util = directory_tree_util
        self.allowed_commands = {
            'ls', 'find', 'grep', 'wc', 'head', 'tail', 'cat', 'tree', 'file', 'sed'
        }
        
        # Initialize enhanced command validator
        self.command_validator = CommandValidator(self.allowed_commands)

        # Tool usage tracking
        self.tool_usage_stats = {
            'readFile': {'count': 0, 'total_chars': 0, 'files_accessed': []},
            'runTerminalCmd': {'count': 0, 'total_chars': 0, 'commands_executed': []},
            'getImplementation': {'count': 0, 'total_chars': 0, 'classes_accessed': []},
            'getSummaryOfFile': {'count': 0, 'total_chars': 0, 'files_accessed': []},
            'list_files': {'count': 0, 'total_chars': 0, 'paths_accessed': []},
            'checkFileSize': {'count': 0, 'total_chars': 0, 'files_checked': []},
            'inspectDirectoryHierarchy': {'count': 0, 'total_chars': 0, 'directories_accessed': []},
            'getFileContentByLines': {'count': 0, 'total_chars': 0, 'files_accessed': []},
        }

        # Class registry cache
        self._class_registry_cache = None
        self._class_registry_loaded = False
        
        # Tool handler registry - maps tool names to handler methods
        self._tool_handlers: Dict[str, Callable] = {}

        logger.info(
            f"Initialized ToolsBase with repo path: {repo_path}, "
            f"override_base_dir: {override_base_dir}, "
            f"ignore_dirs: {len(self.ignore_dirs)} directories, "
            f"file_content_provider: {'provided' if file_content_provider else 'not provided'}, "
            f"artifacts_dir: {artifacts_dir}, "
            f"directory_tree_util: {'provided' if directory_tree_util else 'not provided'}"
        )
        
        if file_content_provider:
            logger.debug(f"FileContentProvider type: {type(file_content_provider).__name__}")
        else:
            logger.warning("FileContentProvider not provided - file resolution capabilities will be limited")

    def register_tool_handler(self, tool_name: str, handler: Callable) -> None:
        """
        Register a handler function for a tool.
        
        Args:
            tool_name: Name of the tool
            handler: Callable that handles the tool execution
        """
        self._tool_handlers[tool_name] = handler
        logger.debug(f"Registered handler for tool: {tool_name}")

    def get_openai_tools_schema(self) -> List[Dict[str, Any]]:
        """
        Get OpenAI-compatible tools schema for all registered tools.
        
        Returns:
            List of OpenAI function schema dictionaries
        """
        return get_all_openai_function_schemas()

    def execute_tool_call(self, tool_call: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute an OpenAI-compatible tool call.
        
        This method handles the OpenAI function calling format:
        {
            "id": "call_abc123",
            "type": "function",
            "function": {
                "name": "tool_name",
                "arguments": "{\"param1\": \"value1\"}"  # JSON string
            }
        }
        
        Args:
            tool_call: OpenAI-format tool call dictionary
            
        Returns:
            Tool result in OpenAI-compatible format:
            {
                "tool_call_id": "call_abc123",
                "role": "tool",
                "content": "result string"
            }
        """
        tool_call_id = tool_call.get("id", "unknown")
        
        try:
            # Extract function details
            function_info = tool_call.get("function", {})
            tool_name = function_info.get("name", "")
            arguments_str = function_info.get("arguments", "{}")
            
            # Parse arguments from JSON string
            try:
                arguments = json.loads(arguments_str) if isinstance(arguments_str, str) else arguments_str
            except json.JSONDecodeError as e:
                logger.error(f"[TOOL] Failed to parse arguments for {tool_name}: {e}")
                return {
                    "tool_call_id": tool_call_id,
                    "role": "tool",
                    "content": f"Error: Invalid JSON in arguments: {e}"
                }
            
            logger.info(f"[TOOL EXECUTOR] Executing tool '{tool_name}' (id: {tool_call_id})")
            logger.debug(f"[TOOL EXECUTOR] Arguments: {arguments}")
            
            # Validate parameters
            is_valid, error_msg = validate_tool_parameters(tool_name, arguments)
            if not is_valid:
                logger.error(f"[TOOL EXECUTOR] Parameter validation failed: {error_msg}")
                return {
                    "tool_call_id": tool_call_id,
                    "role": "tool",
                    "content": f"Error: {error_msg}"
                }
            
            # Normalize parameters (handle aliases)
            normalized_args = normalize_parameters(tool_name, arguments)
            
            # Execute the tool
            result = self._execute_tool(tool_name, normalized_args)
            
            return {
                "tool_call_id": tool_call_id,
                "role": "tool",
                "content": result
            }
            
        except Exception as e:
            error_msg = f"Error executing tool: {str(e)}"
            logger.error(f"[TOOL EXECUTOR] {error_msg}")
            return {
                "tool_call_id": tool_call_id,
                "role": "tool",
                "content": error_msg
            }

    def _execute_tool(self, tool_name: str, params: Dict[str, Any]) -> str:
        """
        Internal method to execute a tool by name.
        
        Args:
            tool_name: Name of the tool to execute
            params: Normalized parameters dictionary
            
        Returns:
            str: Tool execution result
        """
        # Check if tool exists in definitions
        tool_def = get_tool_definition(tool_name)
        if not tool_def:
            available_tools = ", ".join(TOOL_DEFINITIONS.keys())
            error_msg = f"Error: Unknown tool '{tool_name}'. Available tools: {available_tools}"
            logger.error(f"[TOOL EXECUTOR] {error_msg}")
            return error_msg
        
        # Check if handler is registered
        handler = self._tool_handlers.get(tool_name)
        if not handler:
            error_msg = f"Error: No handler registered for tool '{tool_name}'"
            logger.error(f"[TOOL EXECUTOR] {error_msg}")
            return error_msg
        
        try:
            return handler(**params)
        except TypeError as e:
            # Handle parameter mismatch
            error_msg = f"Error: Parameter mismatch for tool '{tool_name}': {e}"
            logger.error(f"[TOOL EXECUTOR] {error_msg}")
            return error_msg
        except Exception as e:
            error_msg = f"Error executing tool '{tool_name}': {str(e)}"
            logger.error(f"[TOOL EXECUTOR] {error_msg}")
            return error_msg

    def _resolve_file_path(self, file_path: str):
        """
        Resolves file path for tool operations.
        Returns (resolved_path: Path|None, original_string_for_errors: str)
        """
        if not isinstance(file_path, str):
            return None, str(file_path)
        
        file_path = file_path.strip()
        if not file_path:
            return None, file_path
            
        # Try absolute path first
        p = Path(file_path)
        if p.is_absolute() and p.exists():
            return p.resolve(), file_path
            
        # Try repo-relative path
        repo_relative = Path(self.repo_path).resolve() / file_path
        if repo_relative.exists():
            return repo_relative.resolve(), file_path
            
        # Try FileContentProvider if available
        if self.file_content_provider:
            resolved_path = self.file_content_provider.resolve_file_path(file_path)
            if resolved_path:
                return Path(resolved_path), file_path
                
        return None, file_path

    def _validate_file_path(self, file_path: str) -> Optional[str]:
        """
        Validate file path structure and existence only.
        Does not check file size - use _validate_file_size for that.

        Args:
            file_path: Path to validate

        Returns:
            str: Error message if invalid, None if valid
        """
        # Construct full path using repo_path
        full_path = os.path.join(self.repo_path, file_path)

        if not os.path.exists(full_path):
            error_msg = f"Error: File '{file_path}' not found."
            logger.warning(error_msg)
            return error_msg

        return None  # File path is valid

    def _validate_file_size(self, file_path: str) -> Optional[str]:
        """
        Validate file size to prevent reading huge files.

        Args:
            file_path: Path to the file to check

        Returns:
            str: Error message if file is too large, None if within limits
        """
        # Construct full path using repo_path
        full_path = os.path.join(self.repo_path, file_path)

        # Check file size to prevent reading huge files
        file_size = os.path.getsize(full_path)
        if file_size > MAX_FILE_SIZE_BYTES:
            error_msg = f"Error: File '{file_path}' is too large ({file_size} bytes). Maximum size is {MAX_FILE_SIZE_BYTES // (1024 * 1024)}MB."
            logger.warning(f"[TOOL] readFile - File too large: {error_msg}")
            return error_msg

        # Also check character count for files that might have many characters but small byte size
        try:
            with open(full_path, 'r', encoding='utf-8', errors='ignore') as f:
                # Read first chunk to estimate character count
                f.read(10000)  # Read first 10K chars
                f.seek(0, 2)  # Seek to end
                file_size_chars = f.tell()  # Get file size in bytes

                # Rough estimate: if file is large in bytes, it's likely large in characters too
                if file_size_chars > MAX_FILE_CHARACTERS:
                    error_msg = f"Error: File '{file_path}' is too large (estimated {file_size_chars} characters). Maximum size is {MAX_FILE_CHARACTERS} characters."
                    logger.warning(f"[TOOL] readFile - File too large (character estimate): {error_msg}")
                    return error_msg
        except Exception as e:
            # If we can't estimate, continue with normal validation
            logger.debug(f"[TOOL] readFile - Could not estimate character count for {file_path}: {e}")

        return None  # File size is within limits

    def _fallback_file_search(self, file_path: str, original_validation_error: str) -> str:
        """
        Fallback file search method using FileContentProvider and directory walking.

        Args:
            file_path: Original file path that failed validation
            original_validation_error: The original validation error message

        Returns:
            str: Resolved file path or error message
        """
        filename = os.path.basename(file_path)

        # First try using FileContentProvider.guess_path if available
        if self.file_content_provider and hasattr(self.file_content_provider, 'guess_path'):
            logger.debug(f"[TOOL] readFile - Using FileContentProvider.guess_path for: {filename}")

            # Extract directory part from original file_path for context
            dir_path = os.path.dirname(file_path) if os.path.dirname(file_path) else ""

            guessed_path = self.file_content_provider.guess_path(filename, dir_path)
            if guessed_path:
                logger.info(f"[TOOL] readFile - FileContentProvider.guess_path resolved to: {guessed_path}")

                # Validate the guessed path
                guessed_validation_error = self._validate_file_path(guessed_path)
                if guessed_validation_error is None:
                    return guessed_path
                else:
                    logger.warning(f"[TOOL] readFile - Guessed path failed validation: {guessed_validation_error}")

        # Fallback to directory walking if FileContentProvider didn't work
        logger.debug(f"[TOOL] readFile - Falling back to directory walking for: {filename}")

        # Search for files with the same name in the repository
        matching_files = []
        for root, _, files in os.walk(self.repo_path):
            if filename in files:
                # Get relative path from repo_path
                relative_path = os.path.relpath(os.path.join(root, filename), self.repo_path)
                matching_files.append(relative_path)

        if len(matching_files) == 1:
            # Found exactly one file with this name, use it
            found_file_path = matching_files[0]
            logger.info(f"[TOOL] readFile - Found alternate file: {found_file_path}")

            # Validate the found file path (should pass since it exists and is not ignored)
            found_validation_error = self._validate_file_path(found_file_path)
            if found_validation_error:
                return found_validation_error

            return found_file_path
        elif len(matching_files) == 0:
            logger.warning(f"[TOOL] readFile - No files found with name: {filename}")
            return original_validation_error  # Return original validation error
        else:
            logger.warning(f"[TOOL] readFile - Multiple files found with name {filename}: {matching_files}")
            return f"Error: Multiple files found with name '{filename}': {matching_files}. Please specify the full path."

    def _find_files_by_name(self, filename: str) -> List[str]:
        """
        Find all files in the repository with the given filename.

        Args:
            filename: Name of the file to search for

        Returns:
            List of relative paths to files with the given name
        """
        matching_files = []

        try:
            for root, _, files in os.walk(self.repo_path):
                if filename in files:
                    # Get relative path from repo_path
                    relative_path = os.path.relpath(os.path.join(root, filename), self.repo_path)
                    matching_files.append(relative_path)

        except Exception as e:
            logger.debug(f"Error searching for files with name '{filename}': {e}")

        return matching_files

    def _get_artifacts_dir(self) -> str:
        """
        Get the artifacts directory path, using provided path or singleton fallback.
        
        Returns:
            str: Path to the artifacts directory
        """
        if self.artifacts_dir:
            return self.artifacts_dir
        else:
            # Use OutputDirectoryProvider singleton
            output_provider = get_output_directory_provider()
            return output_provider.get_repo_artifacts_dir()

    def _log_tool_failure(self, tool_name: str, command: str, error_msg: str) -> None:
        """
        Log tool failure to {artifacts_dir}/{repo_name}/tool_failures/failures.txt
        
        This method provides a centralized way to log tool failures for debugging
        and analysis purposes. The log file is created in the tool_failures directory
        under the repository's artifacts directory.
        
        Args:
            tool_name: Name of the tool that failed (e.g., 'runTerminalCmd')
            command: The command that failed
            error_msg: The error message
        """
        try:
            # Determine artifacts directory
            if self.artifacts_dir:
                # artifacts_dir is typically {base}/opencv/code_insights
                # We want to log to {base}/opencv/tool_failures/failures.txt
                # So go up one level from artifacts_dir
                artifacts_path = os.path.dirname(self.artifacts_dir)
            else:
                output_provider = get_output_directory_provider()
                artifacts_path = output_provider.get_repo_artifacts_dir()
            
            # Create tool_failures directory if needed
            tool_failures_dir = os.path.join(artifacts_path, "tool_failures")
            os.makedirs(tool_failures_dir, exist_ok=True)
            
            # Log file path: {artifacts_dir}/{repo_name}/tool_failures/failures.txt
            log_file = os.path.join(tool_failures_dir, "failures.txt")
            
            # Format: [timestamp] [tool_name] command
            # Error: error_msg
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
            log_entry = f"[{timestamp}] [{tool_name}] {command}\nError: {error_msg}\n\n"
            
            # Append to file
            with open(log_file, 'a', encoding='utf-8') as f:
                f.write(log_entry)
                
            logger.debug(f"[TOOL] Logged failure to {log_file}")
            
        except Exception as e:
            # Non-blocking: log error but don't fail the operation
            logger.warning(f"[TOOL] Failed to log tool failure to file: {e}")

    def log_tool_usage_summary(self):
        """Log comprehensive tool usage statistics"""
        logger.info("=== TOOL USAGE SUMMARY ===")

        total_tools_used = sum(stats['count'] for stats in self.tool_usage_stats.values())
        total_chars_returned = sum(stats['total_chars'] for stats in self.tool_usage_stats.values())

        logger.info(f"Total tool calls: {total_tools_used}")
        logger.info(f"Total characters returned: {total_chars_returned}")

        for tool_name, stats in self.tool_usage_stats.items():
            if stats['count'] > 0:
                logger.info(f"[{tool_name}] Calls: {stats['count']}, Chars: {stats['total_chars']}")

                if tool_name == 'readFile' and stats.get('files_accessed'):
                    logger.info(f"[{tool_name}] Files accessed: {', '.join(stats['files_accessed'])}")
                elif tool_name == 'runTerminalCmd' and stats.get('commands_executed'):
                    logger.info(f"[{tool_name}] Commands: {', '.join(stats['commands_executed'])}")
                elif tool_name == 'getImplementation' and stats.get('classes_accessed'):
                    logger.info(f"[{tool_name}] Classes: {', '.join(stats['classes_accessed'])}")
                elif tool_name == 'getSummaryOfFile' and stats.get('files_accessed'):
                    logger.info(f"[{tool_name}] Files accessed: {', '.join(stats['files_accessed'])}")
                elif tool_name == 'inspectDirectoryHierarchy' and stats.get('directories_accessed'):
                    logger.info(f"[{tool_name}] Directories accessed: {', '.join(stats['directories_accessed'])}")
                elif tool_name == 'list_files' and stats.get('paths_accessed'):
                    logger.info(f"[{tool_name}] Paths accessed: {', '.join(stats['paths_accessed'])}")
                elif tool_name == 'getFileContentByLines' and stats.get('files_accessed'):
                    logger.info(f"[{tool_name}] Files accessed: {', '.join(stats['files_accessed'])}")
                elif tool_name == 'checkFileSize' and stats.get('files_checked'):
                    logger.info(f"[{tool_name}] Files checked: {', '.join(stats['files_checked'])}")

        logger.info("=== END TOOL USAGE SUMMARY ===")
