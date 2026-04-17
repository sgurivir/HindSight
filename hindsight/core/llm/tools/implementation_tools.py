#!/usr/bin/env python3
# Created by Sridhar Gurivireddy
"""
Implementation Tools Module - Code implementation retrieval tools.

This module provides tools for:
- getImplementation: Retrieve class/function implementations from registry
- getSummaryOfFile: Generate file summaries using CodeContextPruner
"""

import json
import os
import time
from typing import Dict, Any, List, Optional, Tuple

from ...lang_util.code_context_pruner import CodeContextPruner
from ...lang_util.all_supported_extensions import ALL_SUPPORTED_EXTENSIONS
from ....utils.file_util import read_file_with_line_numbers
from ....utils.log_util import get_logger
from .base import ToolsBase, MAX_FILE_CHARACTERS


logger = get_logger(__name__)


class ImplementationToolsMixin:
    """
    Mixin class providing implementation retrieval tool implementations.
    
    This mixin should be used with ToolsBase to provide implementation-related tools:
    - execute_get_implementation_tool
    - execute_get_summary_of_file_tool
    """

    def _load_class_registry(self: ToolsBase) -> Optional[List[Dict[str, Any]]]:
        """
        Load class registry from merged class definitions file.

        Returns:
            List of class entries or None if not found
        """
        if self._class_registry_loaded:
            return self._class_registry_cache

        # Try to find class registry files in artifacts directory
        artifacts_temp_dir = self._get_artifacts_dir()

        all_potential_paths = [
            # Check artifacts directory for all data types files
            os.path.join(artifacts_temp_dir, "merged_defined_data_types.json"),
            os.path.join(artifacts_temp_dir, "merged_defined_classes.json"),
            # Check merged symbols (contains both functions and classes)
            os.path.join(artifacts_temp_dir, "merged_functions.json"),
        ]

        for path in all_potential_paths:
            if not os.path.exists(path):
                logger.debug(f"Class registry path not found: {path}")
                continue

            try:
                with open(path, 'r', encoding='utf-8') as f:
                    data = json.load(f)

                    # Handle different file formats
                    if 'data_type_to_location_and_checksum' in data:
                        # New dictionary format with data_type_to_location_and_checksum key
                        class_dict = data['data_type_to_location_and_checksum']
                        self._class_registry_cache = []
                        for class_name, class_info in class_dict.items():
                            # Extract file_name from code blocks if present
                            files = []
                            if 'code' in class_info:
                                code_blocks = class_info['code']
                                if type(code_blocks).__name__ == 'list':
                                    for code_block in code_blocks:
                                        if 'file_name' in code_block:
                                            files.append(code_block['file_name'])
                                elif 'file_name' in code_blocks:
                                    files.append(code_blocks['file_name'])
                            
                            # Create entry in expected format
                            self._class_registry_cache.append({
                                'data_type_name': class_name,
                                'files': files
                            })
                    elif 'data_type_to_location' in data:
                        # Dictionary format with data_type_to_location key
                        self._class_registry_cache = data['data_type_to_location']
                    elif type(data).__name__ == 'list':
                        # Direct array format
                        self._class_registry_cache = data
                    elif 'classes' in data:
                        # Handle case where data might be wrapped in other metadata
                        self._class_registry_cache = data['classes']
                    else:
                        logger.warning(f"[TOOL] Unexpected data format in {path}")
                        continue

                    self._class_registry_loaded = True
                    logger.info(f"[TOOL] Loaded class registry from {path} with {len(self._class_registry_cache)} entries")
                    return self._class_registry_cache
            except Exception as e:
                logger.warning(f"[TOOL] Failed to load class registry from {path}: {e}")
                continue

        self._class_registry_loaded = True
        self._class_registry_cache = []
        logger.warning("✗ [TOOL] No class registry file found. getImplementation will have limited functionality.")
        return self._class_registry_cache

    def _find_class_files(self: ToolsBase, class_name: str) -> List[Dict[str, Any]]:
        """
        Find files and code blocks associated with a class name using the class registry.

        Args:
            class_name: Name of the class to find

        Returns:
            List of dictionaries containing file paths and optional location info
        """
        registry = self._load_class_registry()
        if not registry:
            return []

        # Search for exact match first
        for entry in registry:
            if entry.get("data_type_name") == class_name:
                files = entry.get("files", [])
                # Convert to list of dicts if needed
                result = []
                for file_entry in files:
                    if isinstance(file_entry, dict):
                        result.append(file_entry)
                    elif isinstance(file_entry, str):
                        result.append({'file_name': file_entry})
                return result

        # Search for partial matches (case-insensitive)
        class_name_lower = class_name.lower()
        matches = []
        for entry in registry:
            entry_name = entry.get("data_type_name", "")
            if class_name_lower in entry_name.lower():
                files = entry.get("files", [])
                for file_entry in files:
                    if isinstance(file_entry, dict):
                        matches.append(file_entry)
                    elif isinstance(file_entry, str):
                        matches.append({'file_name': file_entry})

        return matches

    def _find_function_files(self: ToolsBase, function_name: str) -> List[Dict[str, Any]]:
        """
        Find files and code blocks associated with a function name using the merged functions registry.

        Args:
            function_name: Name of the function to find

        Returns:
            List of dictionaries containing file paths and location info
        """
        artifacts_temp_dir = self._get_artifacts_dir()

        # Try to find function registry files in artifacts directory
        function_registry_paths = [
            os.path.join(artifacts_temp_dir, "merged_functions.json"),
            os.path.join(artifacts_temp_dir, "clang_defined_functions.json"),
            os.path.join(artifacts_temp_dir, "swift_defined_functions.json"),
            os.path.join(artifacts_temp_dir, "java_defined_functions.json"),
            os.path.join(artifacts_temp_dir, "kotlin_defined_functions.json"),
        ]

        for registry_path in function_registry_paths:
            if not os.path.exists(registry_path):
                continue

            try:
                with open(registry_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)

                    # Handle different file formats
                    functions_data = []
                    if 'function_to_location' in data:
                        # New schema format
                        function_data = data['function_to_location']
                        for func_name, definitions in function_data.items():
                            if type(definitions).__name__ == 'list':
                                for defn in definitions:
                                    if 'file_name' in defn:
                                        functions_data.append({
                                            'name': func_name,
                                            'context': {
                                                'file': defn.get('file_name', ''),
                                                'start': defn.get('start', 0),
                                                'end': defn.get('end', 0)
                                            }
                                        })
                    elif type(data).__name__ == 'dict':
                        # Dict format keyed directly by function names
                        for func_name, func_info in data.items():
                            if 'code' in func_info:
                                code_blocks = func_info['code']
                                if type(code_blocks).__name__ == 'list':
                                    for code_block in code_blocks:
                                        if 'file_name' in code_block:
                                            functions_data.append({
                                                'name': func_name,
                                                'context': {
                                                    'file': code_block['file_name'],
                                                    'start': code_block.get('start', 0),
                                                    'end': code_block.get('end', 0)
                                                }
                                            })
                                elif 'file_name' in code_blocks:
                                    functions_data.append({
                                        'name': func_name,
                                        'context': {
                                            'file': code_blocks['file_name'],
                                            'start': code_blocks.get('start', 0),
                                            'end': code_blocks.get('end', 0)
                                        }
                                    })
                    elif type(data).__name__ == 'list':
                        functions_data = data

                    # Search for exact match first
                    for func_entry in functions_data:
                        if func_entry.get("name") == function_name:
                            context = func_entry.get("context", {})
                            file_path = context.get("file", "")
                            if file_path:
                                return [{
                                    'file_name': file_path,
                                    'start': context.get('start', 0),
                                    'end': context.get('end', 0)
                                }]

                    # Search for suffix matches
                    matches = []
                    seen_entries = set()
                    for func_entry in functions_data:
                        entry_name = func_entry.get("name", "")
                        if entry_name.endswith(function_name) or entry_name.endswith('::' + function_name):
                            context = func_entry.get("context", {})
                            file_path = context.get("file", "")
                            if file_path:
                                entry_key = (file_path, context.get('start', 0), context.get('end', 0))
                                if entry_key not in seen_entries:
                                    seen_entries.add(entry_key)
                                    matches.append({
                                        'file_name': file_path,
                                        'start': context.get('start', 0),
                                        'end': context.get('end', 0)
                                    })
                    
                    if matches:
                        return matches

                    # Search for partial matches (case-insensitive) as fallback
                    function_name_lower = function_name.lower()
                    for func_entry in functions_data:
                        entry_name = func_entry.get("name", "")
                        if function_name_lower in entry_name.lower():
                            context = func_entry.get("context", {})
                            file_path = context.get("file", "")
                            if file_path:
                                entry_key = (file_path, context.get('start', 0), context.get('end', 0))
                                if entry_key not in seen_entries:
                                    seen_entries.add(entry_key)
                                    matches.append({
                                        'file_name': file_path,
                                        'start': context.get('start', 0),
                                        'end': context.get('end', 0)
                                    })

                    if matches:
                        return matches

            except Exception as e:
                logger.debug(f"[TOOL] Failed to load function registry from {registry_path}: {e}")
                continue

        return []

    def _check_file_character_count(self: ToolsBase, file_path: str) -> Tuple[bool, int]:
        """
        Check if a file exceeds the character limit using FileContentProvider data.

        Args:
            file_path: Path to the file to check

        Returns:
            Tuple[bool, int]: (is_within_limit, character_count)
        """
        filename = os.path.basename(file_path)

        # Check if file exists in the mapping
        if self.file_content_provider and filename in self.file_content_provider.name_to_path_mapping:
            file_infos = self.file_content_provider.name_to_path_mapping[filename]

            # Find the matching file info by path
            for file_info in file_infos:
                if isinstance(file_info, dict):
                    info_path = file_info.get('path', '')
                    if info_path == file_path or info_path.endswith(file_path) or file_path.endswith(info_path):
                        char_count = file_info.get('number_of_characters', -1)
                        if char_count > 0:
                            is_within_limit = char_count <= MAX_FILE_CHARACTERS
                            return is_within_limit, char_count

        # If file info not found or character count not available, allow the file
        return True, -1

    def execute_get_implementation_tool(self: ToolsBase, name: str, reason: str = None) -> str:
        """
        Execute getImplementation tool to retrieve class or function implementation from multiple files.

        Args:
            name: Name of the class or function to retrieve implementation for
            reason: Reason why this tool is being used (optional for backward compatibility)

        Returns:
            str: Implementation content from all associated files or error message
        """
        start_time = time.time()
        self.tool_usage_stats['getImplementation']['count'] += 1

        logger.info(f"[TOOL] getImplementation called #{self.tool_usage_stats['getImplementation']['count']} - Name: {name} | [AI REASONING] {reason if reason else 'No reason provided'}")

        try:
            # First try to find as a class
            class_files = self._find_class_files(name)
            logger.debug(f"[TOOL] getImplementation - Class search for '{name}' returned: {class_files}")

            # If not found as class, try to find as function
            function_files = []
            if not class_files:
                function_files = self._find_function_files(name)
                logger.debug(f"[TOOL] getImplementation - Function search for '{name}' returned: {function_files}")

            # Determine what we found and set appropriate variables
            if class_files:
                implementation_files = class_files
                implementation_type = "Class"
                logger.info(f"[TOOL] getImplementation - Found class: {name} ")
            elif function_files:
                implementation_files = function_files
                implementation_type = "Function"
                logger.info(f"[TOOL] getImplementation - Found function: {name} ")
            else:
                # If not found in registry, try a direct file search as fallback
                logger.info(f"[TOOL] getImplementation - '{name}' not found in registry, trying direct file search")

                SEARCH_EXTENSIONS = tuple(ALL_SUPPORTED_EXTENSIONS + ['.proto'])
                
                potential_files = []
                search_patterns = [f"{name}{ext}" for ext in SEARCH_EXTENSIONS]

                for root, _, files in os.walk(self.repo_path):
                    for file in files:
                        if any(file.lower() == pattern.lower() for pattern in search_patterns):
                            relative_path = os.path.relpath(os.path.join(root, file), self.repo_path)
                            potential_files.append(relative_path)
                        elif file.endswith(SEARCH_EXTENSIONS):
                            try:
                                full_path = os.path.join(root, file)
                                with open(full_path, 'r', encoding='utf-8', errors='ignore') as f:
                                    content = f.read()
                                    if name in content:
                                        relative_path = os.path.relpath(full_path, self.repo_path)
                                        if relative_path not in potential_files:
                                            potential_files.append(relative_path)
                            except Exception:
                                continue

                if potential_files:
                    implementation_files = potential_files[:3]
                    implementation_type = "Potential Implementation"
                    logger.info(f"[TOOL] getImplementation - Found potential files for {name}: {implementation_files}")
                else:
                    error_msg = f"Error: '{name}' not found in class or function registry, and no potential implementation files found."
                    logger.warning(f"[TOOL] getImplementation - Not found: {error_msg}")
                    return error_msg

            # Group code blocks by file to avoid duplicate processing
            files_by_path = {}
            for entry in implementation_files:
                if isinstance(entry, dict):
                    file_name = entry.get('file_name', '')
                    start = entry.get('start', 0)
                    end = entry.get('end', 0)
                    
                    if file_name:
                        if file_name not in files_by_path:
                            files_by_path[file_name] = []
                        if start > 0 and end > 0:
                            files_by_path[file_name].append({'start': start, 'end': end})
                elif isinstance(entry, str):
                    if entry not in files_by_path:
                        files_by_path[entry] = []
            
            logger.info(f"[TOOL] getImplementation - Processing {len(files_by_path)} unique files for '{name}'")
            
            # Read all associated files
            result_parts = [f"{implementation_type} Implementation: {name}"]
            total_size = 0
            files_read = []

            for file_path, code_blocks in files_by_path.items():
                # Validate file path
                validation_error = self._validate_file_path(file_path)
                if validation_error:
                    # Try to resolve the file path
                    resolved_file_path = self._try_resolve_file_path(file_path)
                    if resolved_file_path:
                        file_path = resolved_file_path
                    else:
                        result_parts.append(f"\n--- File: {file_path} (NOT FOUND) ---")
                        result_parts.append(f"Error: File could not be located in the repository")
                        continue

                full_path = os.path.join(self.repo_path, file_path)
                if not os.path.exists(full_path):
                    result_parts.append(f"\n--- File: {file_path} (NOT FOUND) ---")
                    result_parts.append("Error: File does not exist at the specified path")
                    continue

                # If we have code blocks with location info, use location-based extraction
                if code_blocks:
                    content = self._extract_code_blocks(full_path, file_path, code_blocks)
                    result_parts.append(content)
                    files_read.append(file_path)
                else:
                    # No location info - fall back to reading entire file with size check and pruning
                    content = self._read_full_file_with_pruning(full_path, file_path)
                    if content:
                        result_parts.append(content)
                        files_read.append(file_path)

            final_result = "\n".join(result_parts)

            # Update statistics
            self.tool_usage_stats['getImplementation']['total_chars'] += len(final_result)
            self.tool_usage_stats['getImplementation']['classes_accessed'].append(name)

            execution_time = time.time() - start_time
            logger.info(f"[TOOL] getImplementation completed - {implementation_type}: {name}, "
                       f"Files: {len(files_read)}, Content: {len(final_result)} chars, "
                       f"Time: {execution_time:.2f}s")

            return final_result

        except Exception as e:
            execution_time = time.time() - start_time
            error_msg = f"Error retrieving implementation for '{name}': {str(e)}"
            logger.error(f"[TOOL] getImplementation failed - Name: {name}, Error: {str(e)}, Time: {execution_time:.2f}s")
            return error_msg

    def _try_resolve_file_path(self: ToolsBase, file_path: str) -> Optional[str]:
        """Try to resolve a file path using various methods."""
        resolved_file_path = None

        if self.file_content_provider:
            filename = os.path.basename(file_path)
            logger.debug(f"[TOOL] getImplementation - Using FileContentProvider to resolve: {filename}")

            if hasattr(self.file_content_provider, 'resolve_file_path'):
                resolved_path = self.file_content_provider.resolve_file_path(filename, file_path)
                if resolved_path:
                    resolved_file_path = str(resolved_path)
                    logger.info(f"[TOOL] getImplementation - FileContentProvider.resolve_file_path resolved to: {resolved_file_path}")

            if not resolved_file_path and hasattr(self.file_content_provider, 'guess_path'):
                dir_path = os.path.dirname(file_path) if os.path.dirname(file_path) else ""
                guessed_path = self.file_content_provider.guess_path(filename, dir_path)
                if guessed_path:
                    resolved_file_path = guessed_path
                    logger.info(f"[TOOL] getImplementation - FileContentProvider.guess_path resolved to: {resolved_file_path}")

        if not resolved_file_path:
            filename = os.path.basename(file_path)
            matching_files = []
            for root, _, files in os.walk(self.repo_path):
                if filename in files:
                    relative_path = os.path.relpath(os.path.join(root, filename), self.repo_path)
                    matching_files.append(relative_path)

            if len(matching_files) == 1:
                resolved_file_path = matching_files[0]
            elif len(matching_files) > 1:
                original_dir = os.path.dirname(file_path)
                for match in matching_files:
                    if original_dir in match:
                        resolved_file_path = match
                        break
                if not resolved_file_path:
                    resolved_file_path = matching_files[0]

        return resolved_file_path

    def _extract_code_blocks(self: ToolsBase, full_path: str, file_path: str, code_blocks: List[Dict]) -> str:
        """Extract code blocks from a file based on line ranges."""
        total_lines = sum(block['end'] - block['start'] + 1 for block in code_blocks)
        
        try:
            with open(full_path, 'r', encoding='utf-8', errors='ignore') as f:
                all_lines = f.readlines()
            
            result_parts = []
            if total_lines < 500:
                result_parts.append(f"\n=== Path: {file_path} ===")
                
                for block in code_blocks:
                    start_line = block['start']
                    end_line = block['end']
                    
                    result_parts.append(f"=== Start_line: {start_line} ===")
                    result_parts.append("")
                    
                    if start_line > 0 and end_line <= len(all_lines):
                        extracted_lines = all_lines[start_line-1:end_line]
                        numbered_lines = []
                        for i, line in enumerate(extracted_lines):
                            line_num = start_line + i
                            numbered_lines.append(f"{line_num:4d} | {line.rstrip()}")
                        
                        content = '\n'.join(numbered_lines)
                        result_parts.append(content)
                        result_parts.append("")
                    else:
                        result_parts.append(f"Error: Invalid line range {start_line}-{end_line} for file with {len(all_lines)} lines")
                        result_parts.append("")
            else:
                result_parts.append(f"\n=== File: {file_path} (Large - showing line ranges only) ===")
                for block in code_blocks:
                    result_parts.append(f"Path: {file_path}")
                    result_parts.append(f"Start_line: {block['start']}")
                    result_parts.append(f"End_line: {block['end']}")
                    result_parts.append("---")
            
            return '\n'.join(result_parts)
        
        except Exception as e:
            logger.error(f"[TOOL] getImplementation - Failed to extract code blocks from {file_path}: {e}")
            return f"\n--- File: {file_path} (READ ERROR) ---\nError: Failed to extract code blocks - {str(e)}"

    def _read_full_file_with_pruning(self: ToolsBase, full_path: str, file_path: str) -> Optional[str]:
        """Read a full file with size validation and pruning."""
        size_validation_error = self._validate_file_size(file_path)
        if size_validation_error:
            return f"\n--- File: {file_path} (TOO LARGE) ---\nError: {size_validation_error}\nSuggestion: Use getFileContentByLines tool to read specific sections"

        content = read_file_with_line_numbers(file_path, self.repo_path, 1)
        if content is not None and content.strip():
            try:
                processed_content = CodeContextPruner.prune_code(content)
                return f"\n--- File: {file_path} ---\n{processed_content}"
            except Exception as e:
                logger.warning(f"[TOOL] getImplementation - CodeContextPruner.prune_code failed for {file_path}: {e}")
                return f"\n--- File: {file_path} ---\n{content}"
        else:
            try:
                with open(full_path, 'r', encoding='utf-8', errors='ignore') as f:
                    alt_content = f.read()
                    if alt_content and alt_content.strip():
                        try:
                            numbered_content = CodeContextPruner.add_line_numbers(alt_content, 1)
                            processed_content = CodeContextPruner.prune_code(numbered_content)
                            return f"\n--- File: {file_path} ---\n{processed_content}"
                        except Exception:
                            lines = alt_content.split('\n')
                            numbered_lines = [f"{i+1:4d} | {line}" for i, line in enumerate(lines)]
                            return f"\n--- File: {file_path} ---\n" + '\n'.join(numbered_lines)
                    else:
                        return f"\n--- File: {file_path} (EMPTY FILE) ---\nWarning: File exists but appears to be empty"
            except Exception as e:
                return f"\n--- File: {file_path} (READ ERROR) ---\nError: Failed to read file content - {str(e)}"

    def execute_get_summary_of_file_tool(self: ToolsBase, path: str, reason: str = None) -> str:
        """
        Execute getSummaryOfFile tool to retrieve file summary using CodeContextPruner.

        Args:
            path: File path to get summary for
            reason: Reason why this tool is being used (optional)

        Returns:
            str: File summary content or error message
        """
        start_time = time.time()
        self.tool_usage_stats['getSummaryOfFile']['count'] += 1

        # Validate path parameter
        if not path or not isinstance(path, str):
            error_msg = "Error: 'path' parameter is required and must be a file path string."
            logger.error(f"[TOOL] getSummaryOfFile - Invalid path parameter: {path}")
            return error_msg

        file_path = path.strip()
        if not file_path:
            error_msg = "Error: 'path' parameter cannot be empty."
            logger.error(f"[TOOL] getSummaryOfFile - Empty path provided")
            return error_msg

        logger.info(f"[TOOL] getSummaryOfFile called #{self.tool_usage_stats['getSummaryOfFile']['count']} - Path: {file_path} | [AI REASONING] {reason if reason else 'No reason provided'}")

        try:
            summary_content = self._find_summary_by_path(file_path)

            if summary_content:
                final_result = f"Summary for file: {file_path}\n{summary_content}"
                self.tool_usage_stats['getSummaryOfFile']['files_accessed'].append(file_path)
            else:
                # Try to find files with the same name as fallback
                filename = os.path.basename(file_path)
                matching_files = self._find_files_by_name(filename)

                if len(matching_files) == 1:
                    found_file_path = matching_files[0]
                    fallback_summary = self._find_summary_by_path(found_file_path)

                    if fallback_summary:
                        final_result = f"Summary for file: {found_file_path} (found by filename '{filename}')\n{fallback_summary}"
                        self.tool_usage_stats['getSummaryOfFile']['files_accessed'].append(found_file_path)
                    else:
                        final_result = f"No summary available for file '{found_file_path}' (found by filename '{filename}')"
                elif len(matching_files) > 1:
                    final_result = f"Multiple files found with name '{filename}': {matching_files}. Please specify the full path."
                else:
                    final_result = f"No summary available for file '{file_path}'. File not found in repository."

            self.tool_usage_stats['getSummaryOfFile']['total_chars'] += len(final_result)

            execution_time = time.time() - start_time
            logger.info(f"[TOOL] getSummaryOfFile completed - File: {file_path}, "
                       f"Content: {len(final_result)} chars, Time: {execution_time:.2f}s")

            return final_result

        except Exception as e:
            execution_time = time.time() - start_time
            error_msg = f"Error retrieving summary for file '{file_path}': {str(e)}"
            logger.error(f"[TOOL] getSummaryOfFile failed - File: {file_path}, Error: {str(e)}, Time: {execution_time:.2f}s")
            return error_msg

    def _find_summary_by_path(self: ToolsBase, file_path: str) -> Optional[str]:
        """
        Find summary for a file using CodeContextPruner to generate summary.

        Args:
            file_path: Path to the file (relative to repo_path)

        Returns:
            str: Summary content or None if not found
        """
        try:
            # Get summary using CodeContextPruner
            summary_content = self._get_summary_using_pruner(file_path)
            if summary_content:
                logger.debug(f"Successfully generated file summary using CodeContextPruner: {file_path}")
                return summary_content

            logger.debug(f"No summary generated for file: {file_path}")
            return None

        except Exception as e:
            logger.debug(f"Could not generate file summary for {file_path}: {e}")
            return None

    def _get_summary_using_pruner(self: ToolsBase, file_path: str) -> Optional[str]:
        """
        Get file summary using CodeContextPruner to generate a pruned version of the file.

        Args:
            file_path: Path to the file (relative to repo_path)

        Returns:
            str: File summary content using CodeContextPruner or None if not found
        """
        try:
            # Construct full path
            full_path = os.path.join(self.repo_path, file_path)

            # Check if file exists
            if not os.path.exists(full_path):
                logger.debug(f"File not found for summary generation: {file_path}")
                return None

            # Read the file content
            try:
                with open(full_path, 'r', encoding='utf-8', errors='ignore') as f:
                    file_content = f.read()
            except Exception as e:
                logger.debug(f"Error reading file {file_path}: {e}")
                return None

            if not file_content.strip():
                return f"File: {file_path}\nNote: File is empty"

            # Add line numbers to the content
            numbered_content = CodeContextPruner.add_line_numbers(file_content, 1)

            # Apply CodeContextPruner to prune code and generate summary
            pruned_content = CodeContextPruner.prune_code(numbered_content)

            # Create a summary header
            file_stats = os.stat(full_path)
            file_size = file_stats.st_size
            file_ext = os.path.splitext(file_path)[1].lower()

            summary_header = f"File: {file_path}\n"
            summary_header += f"Size: {file_size:,} bytes\n"
            summary_header += f"Type: {file_ext} file\n"
            summary_header += f"Lines: {len(file_content.splitlines())}\n\n"
            summary_header += "Pruned Content (signatures and comments, implementations removed):\n"
            summary_header += "=" * 50 + "\n"

            # Combine header with pruned content
            final_summary = summary_header + pruned_content

            logger.debug(f"Generated CodeContextPruner summary for {file_path}: {len(final_summary)} chars")
            return final_summary

        except Exception as e:
            logger.debug(f"Error generating summary using CodeContextPruner for {file_path}: {e}")
            # Fallback to basic summary
            return self._create_basic_file_summary(file_path)

    def _create_basic_file_summary(self: ToolsBase, file_path: str) -> Optional[str]:
        """
        Create a basic summary of a file using merged_call_graph.json and merged_defined_classes.json files.

        Args:
            file_path: Path to the file (relative to repo_path)

        Returns:
            str: Basic file summary with data types and methods information or None if analysis fails
        """
        try:
            artifacts_temp_dir = self._get_artifacts_dir()

            # Paths to the JSON files
            nested_call_graph_path = os.path.join(artifacts_temp_dir, "merged_call_graph.json")
            defined_classes_path = os.path.join(artifacts_temp_dir, "merged_defined_classes.json")

            summary_parts = []
            summary_parts.append(f"File: {file_path}")
            summary_parts.append("")

            # Load and process merged_defined_classes.json
            classes_data = self._load_json_file(defined_classes_path)
            if classes_data:
                file_classes = self._extract_file_classes(classes_data, file_path)
                if file_classes:
                    for class_info in file_classes:
                        class_name = class_info.get('data_type_name', 'Unknown')
                        files = class_info.get('files', [])
                        for file_info in files:
                            if isinstance(file_info, dict):
                                file_name = file_info.get('file_name', '')
                                start_line = file_info.get('start', '')
                                end_line = file_info.get('end', '')
                                if file_name and (file_name == file_path or file_path.endswith(file_name)):
                                    summary_parts.append(f"== {class_name} is at")
                                    summary_parts.append(f"File: {file_name}")
                                    summary_parts.append(f"starting_line : {start_line} , ending_line : {end_line}")
                                    summary_parts.append("")

            # Load and process merged_call_graph.json
            call_graph_data = self._load_json_file(nested_call_graph_path)
            if call_graph_data:
                file_methods = self._extract_file_methods(call_graph_data, file_path)
                if file_methods:
                    for method_info in file_methods:
                        method_name = method_info.get('function', 'Unknown')
                        context = method_info.get('context', {})
                        file_name = context.get('file', '')
                        start_line = context.get('start', '')
                        end_line = context.get('end', '')
                        if file_name and (file_name == file_path or file_path.endswith(file_name)):
                            summary_parts.append(f"== {method_name} is at")
                            summary_parts.append(f"File: {file_name}")
                            summary_parts.append(f"starting_line : {start_line} ,  ending_line : {end_line}")
                            summary_parts.append("")

            # If no data found in JSON files, fall back to basic file analysis
            if len(summary_parts) <= 2:
                return self._create_fallback_file_summary(file_path)

            return "\n".join(summary_parts)

        except Exception as e:
            logger.debug(f"Error creating basic summary for {file_path}: {e}")
            return self._create_fallback_file_summary(file_path)

    def _load_json_file(self: ToolsBase, file_path: str) -> Optional[Dict]:
        """Load JSON file and return parsed data."""
        try:
            if os.path.exists(file_path):
                with open(file_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
        except Exception as e:
            logger.debug(f"Error loading JSON file {file_path}: {e}")
        return None

    def _extract_file_classes(self: ToolsBase, classes_data: Dict, target_file: str) -> List[Dict]:
        """Extract class information for a specific file from merged_defined_classes.json."""
        file_classes = []

        if isinstance(classes_data, list):
            for class_entry in classes_data:
                if isinstance(class_entry, dict):
                    files = class_entry.get('files', [])
                    for file_info in files:
                        if isinstance(file_info, dict):
                            file_name = file_info.get('file_name', '')
                            if file_name and (file_name == target_file or target_file.endswith(file_name)):
                                file_classes.append(class_entry)
                                break

        return file_classes

    def _extract_file_methods(self: ToolsBase, call_graph_data: Dict, target_file: str) -> List[Dict]:
        """Extract method information for a specific file from merged_call_graph.json."""
        file_methods = []

        if isinstance(call_graph_data, dict) and 'call_graph' in call_graph_data:
            call_graph = call_graph_data.get('call_graph', [])

            for file_entry in call_graph:
                file_name = file_entry.get('file', '')
                if file_name and (file_name == target_file or target_file.endswith(file_name)):
                    functions = file_entry.get('functions', [])
                    for func_entry in functions:
                        func_entry_copy = func_entry.copy()
                        if 'context' not in func_entry_copy:
                            func_entry_copy['context'] = {}
                        if 'file' not in func_entry_copy['context']:
                            func_entry_copy['context']['file'] = file_name
                        file_methods.append(func_entry_copy)

        return file_methods

    def _create_fallback_file_summary(self: ToolsBase, file_path: str) -> Optional[str]:
        """Create a fallback summary when JSON files are not available."""
        try:
            full_path = os.path.join(self.repo_path, file_path)

            if not os.path.exists(full_path):
                return None

            file_size = os.path.getsize(full_path)
            file_ext = os.path.splitext(file_path)[1].lower()

            summary_parts = []
            summary_parts.append(f"File: {file_path}")
            summary_parts.append(f"Size: {file_size:,} bytes")
            summary_parts.append(f"Type: {file_ext} file")
            summary_parts.append("")
            summary_parts.append("Note: No structured analysis data available from merged_call_graph.json or merged_defined_classes.json")

            return "\n".join(summary_parts)

        except Exception as e:
            logger.debug(f"Error creating fallback summary for {file_path}: {e}")
            return f"File: {file_path}\nError: Could not analyze file - {e}"