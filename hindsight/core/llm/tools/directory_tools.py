#!/usr/bin/env python3
# Created by Sridhar Gurivireddy
"""
Directory Tools Module - Directory listing and hierarchy inspection tools.

This module provides tools for:
- list_files: List directory contents with file sizes
- inspectDirectoryHierarchy: Get directory structure information
"""

import os
import time

from ....report.issue_directory_organizer import RepositoryDirHierarchy
from ....utils.log_util import get_logger
from .base import ToolsBase


logger = get_logger(__name__)


class DirectoryToolsMixin:
    """
    Mixin class providing directory tool implementations.
    
    This mixin should be used with ToolsBase to provide directory-related tools:
    - execute_list_files_tool
    - execute_inspect_directory_hierarchy_tool
    """

    def execute_list_files_tool(self: ToolsBase, path: str, recursive: bool = False, reason: str = None) -> str:
        """
        Execute list_files tool to retrieve directory tree structure using DirectoryTreeUtil.

        Args:
            path: Path to the directory or file to list (relative to repo root) or dict containing path info
            recursive: If True, list files recursively in tree format. Default is False.
            reason: Reason why this tool is being used (optional for backward compatibility)

        Returns:
            str: Directory tree structure or error message
        """
        start_time = time.time()
        self.tool_usage_stats['list_files']['count'] += 1

        # Validate path parameter
        if not isinstance(path, str):
            error_msg = f"Error: path parameter must be a string, got {type(path)}: {path}"
            logger.error(f"[TOOL] list_files - Invalid input type: {error_msg}")
            return error_msg
        
        path = path.strip() if path else self.repo_path

        logger.info(f"[TOOL] list_files called #{self.tool_usage_stats['list_files']['count']} - Path: {path}, Recursive: {recursive}")
        logger.info(f"[AI REASONING] {reason if reason else 'No reason provided'}")

        try:
            # Check if DirectoryTreeUtil is available
            if not self.directory_tree_util:
                error_msg = "Error: DirectoryTreeUtil not available. This tool requires DirectoryTreeUtil to be initialized."
                logger.error(f"[TOOL] list_files - {error_msg}")
                return error_msg

            # Use DirectoryTreeUtil with recursive parameter
            result = self.directory_tree_util.get_directory_listing(
                repo_path=self.repo_path,
                relative_path=path,
                recursive=recursive
            )

            # Add helpful context about the tool usage
            if result and not result.startswith("Path not found"):
                mode = "recursive" if recursive else "single level"
                context_msg = f"Directory listing for '{path}' ({mode}):\n{result}"
                result = context_msg

            # Update statistics
            self.tool_usage_stats['list_files']['total_chars'] += len(result)
            self.tool_usage_stats['list_files']['paths_accessed'].append(path)

            execution_time = time.time() - start_time
            logger.info(f"[TOOL] list_files completed - Path: {path}, Recursive: {recursive}, "
                       f"Content: {len(result)} chars, Time: {execution_time:.2f}s")

            return result

        except Exception as e:
            execution_time = time.time() - start_time
            error_msg = f"Error getting directory listing for '{path}': {str(e)}"
            logger.error(f"[TOOL] list_files failed - Path: {path}, Error: {str(e)}, Time: {execution_time:.2f}s")
            return error_msg

    def execute_inspect_directory_hierarchy_tool(
        self: ToolsBase,
        directory_path: str = None,
        reason: str = None
    ) -> str:
        """
        Execute inspectDirectoryHierarchy tool to retrieve directory structure.

        Args:
            directory_path: Optional directory path to inspect (relative to repo root) or dict containing path info
            reason: Reason why this tool is being used (optional for backward compatibility)

        Returns:
            str: Directory hierarchy structure or error message
        """
        start_time = time.time()

        # Initialize tool usage stats if not present
        if 'inspectDirectoryHierarchy' not in self.tool_usage_stats:
            self.tool_usage_stats['inspectDirectoryHierarchy'] = {
                'count': 0, 'total_chars': 0, 'directories_accessed': []
            }

        self.tool_usage_stats['inspectDirectoryHierarchy']['count'] += 1

        # Validate directory_path parameter
        if directory_path is not None:
            if not isinstance(directory_path, str):
                error_msg = f"Error: directory_path parameter must be a string, got {type(directory_path)}: {directory_path}"
                logger.error(f"[TOOL] inspectDirectoryHierarchy - Invalid input type: {error_msg}")
                return error_msg
            directory_path = directory_path.strip() if directory_path else None

        logger.info(f"[TOOL] inspectDirectoryHierarchy called #{self.tool_usage_stats['inspectDirectoryHierarchy']['count']} - Path: {directory_path or 'root'}")
        logger.info(f"[AI REASONING] {reason if reason else 'No reason provided'}")

        try:
            if directory_path:
                # First try exact path match
                try:
                    hierarchy = RepositoryDirHierarchy(self.repo_path)
                    structure = hierarchy.get_directory_hierarchy_by_path(directory_path)

                    if structure:
                        result = f"Directory hierarchy for '{directory_path}':\n{structure}"
                    else:
                        # If exact path not found, try to find directories with matching name
                        dir_name = os.path.basename(directory_path.rstrip('/'))
                        matching_dirs = hierarchy.find_directories_by_name(dir_name)

                        if matching_dirs:
                            result_parts = [f"Directory '{directory_path}' not found at exact path, but found {len(matching_dirs)} directories with name '{dir_name}':"]

                            for i, dir_node in enumerate(matching_dirs, 1):
                                # Get relative path from repo root
                                relative_path = os.path.relpath(dir_node.path, self.repo_path)
                                result_parts.append(f"\n{i}. {relative_path}/")

                                # Get structure for this directory
                                dir_structure = hierarchy.get_directory_hierarchy_by_path(relative_path)
                                if dir_structure:
                                    # Limit to first few lines to avoid overwhelming output
                                    structure_lines = dir_structure.split('\n')[:10]
                                    if len(structure_lines) == 10:
                                        structure_lines.append("   ... (truncated)")
                                    result_parts.append('\n'.join(f"   {line}" for line in structure_lines))

                            result = '\n'.join(result_parts)
                        else:
                            result = f"Directory '{directory_path}' not found in repository"

                except Exception as e:
                    result = f"Error inspecting directory '{directory_path}': {str(e)}"
            else:
                # Get full repository structure
                try:
                    # The method will use the OutputDirectoryProvider singleton internally
                    structure = RepositoryDirHierarchy.get_directory_structure_for_repo(self.repo_path)
                    result = f"Repository directory structure:\n{structure}"
                except Exception as e:
                    result = f"Error getting repository directory structure: {str(e)}"

            # Update statistics
            self.tool_usage_stats['inspectDirectoryHierarchy']['total_chars'] += len(result)
            self.tool_usage_stats['inspectDirectoryHierarchy']['directories_accessed'].append(directory_path or 'root')

            execution_time = time.time() - start_time
            logger.info(f"[TOOL] inspectDirectoryHierarchy completed - Path: {directory_path or 'root'}, "
                       f"Content: {len(result)} chars, Time: {execution_time:.2f}s")

            return result

        except Exception as e:
            execution_time = time.time() - start_time
            error_msg = f"Error inspecting directory hierarchy for '{directory_path or 'root'}': {str(e)}"
            logger.error(f"[TOOL] inspectDirectoryHierarchy failed - Path: {directory_path or 'root'}, Error: {str(e)}, Time: {execution_time:.2f}s")
            return error_msg