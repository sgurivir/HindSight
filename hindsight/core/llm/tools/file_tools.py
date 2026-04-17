#!/usr/bin/env python3
# Created by Sridhar Gurivireddy
"""
File Tools Module - File reading and content retrieval tools.

This module provides tools for:
- readFile: Read file contents with automatic pruning for large files
- getFileContentByLines: Read specific line ranges from files
- checkFileSize: Check file existence and size information
"""

import json
import os
import time
from pathlib import Path
from typing import Optional

from ...constants import MAX_FILE_CHARACTERS_FOR_READ_FILE
from ...lang_util.code_context_aggressive_pruner import CodeContextAggressivePruner
from ...lang_util.code_context_pruner import CodeContextPruner
from ....utils.log_util import get_logger
from .base import ToolsBase, MAX_FILE_SIZE_BYTES, MAX_FILE_CHARACTERS


logger = get_logger(__name__)


class FileToolsMixin:
    """
    Mixin class providing file reading tool implementations.
    
    This mixin should be used with ToolsBase to provide file-related tools:
    - execute_read_file_tool
    - execute_get_file_content_by_lines_tool
    - execute_check_file_size_tool
    """

    def execute_read_file_tool(self: ToolsBase, file_path: str) -> str:
        """
        Execute readFile tool with path resolution.
        
        Args:
            file_path: Path to the file to read
            
        Returns:
            str: File content (possibly pruned) or error message
        """
        start_time = time.time()
        # keep existing accounting
        try:
            self.tool_usage_stats['readFile']['count'] += 1
        except Exception:
            pass

        resolved_path, original_string = self._resolve_file_path(file_path)

        if not resolved_path:
            msg = f"File '{original_string}' cannot be found"
            try:
                logger.error(f"[TOOL] readFile - {msg}")
            except Exception:
                pass
            return msg

        # Read file (keep tolerant behavior)
        try:
            text = resolved_path.read_text(encoding="utf-8", errors="ignore")

            # Check if file size exceeds the limit
            if len(text) > MAX_FILE_CHARACTERS_FOR_READ_FILE:
                try:
                    # Use CodeContextAggressivePruner for large files
                    # Handle both absolute and relative paths properly
                    repo_path_obj = Path(self.repo_path).resolve()
                    resolved_path_obj = resolved_path.resolve()

                    try:
                        relative_path = str(resolved_path_obj.relative_to(repo_path_obj))
                    except ValueError:
                        # If relative_to fails, use the original file path as fallback
                        relative_path = str(resolved_path)

                    pruned_text = CodeContextAggressivePruner.prune_file(str(repo_path_obj), relative_path)

                    # Add comment header for pruned files
                    comment_header = f"// File is too large. Here is pruned context for file {relative_path}\n"
                    final_text = comment_header + pruned_text

                    try:
                        logger.info(f"[TOOL] readFile - Read and pruned large file: {resolved_path} "
                                    f"({len(text)} chars -> {len(final_text)} chars after pruning) in {time.time()-start_time:.3f}s")
                    except Exception:
                        pass
                    return final_text
                except Exception as e:
                    # If pruning fails, fall back to regular processing with truncation warning
                    try:
                        logger.warning(f"[TOOL] readFile - Pruning failed for large file {resolved_path}: {e}, using regular processing")
                    except Exception:
                        pass

            # Apply CodeContextPruner to add line numbers and prune code for normal-sized files
            try:
                # First add line numbers
                numbered_text = CodeContextPruner.add_line_numbers(text, 1)
                # Then prune code (keep signatures and comments, remove implementations) while preserving line numbers
                processed_text = CodeContextPruner.prune_code(numbered_text)

                try:
                    logger.info(f"[TOOL] readFile - Read OK: {resolved_path} "
                                f"({len(text)} bytes -> {len(processed_text)} bytes after processing) in {time.time()-start_time:.3f}s")
                except Exception:
                    pass
                return processed_text
            except Exception as e:
                # If CodeContextPruner fails, fall back to original text
                try:
                    logger.warning(f"[TOOL] readFile - CodeContextPruner failed for {resolved_path}: {e}, returning original text")
                except Exception:
                    pass
                try:
                    logger.info(f"[TOOL] readFile - Read OK: {resolved_path} "
                                f"({len(text)} bytes) in {time.time()-start_time:.3f}s")
                except Exception:
                    pass
                return text
        except Exception as e:
            err = f"Error: Failed to read file '{resolved_path}': {e}"
            try:
                logger.error(f"[TOOL] readFile - {err}")
            except Exception:
                pass
            return err

    def execute_get_file_content_by_lines_tool(
        self: ToolsBase,
        path: str,
        start_line: int,
        end_line: int,
        reason: str = None
    ) -> str:
        """
        Execute getFileContentByLines tool to retrieve content between specific line numbers from a file.

        Args:
            path: Path to the file (relative to repo root)
            start_line: Starting line number (1-based, inclusive)
            end_line: Ending line number (1-based, inclusive)
            reason: Reason why this tool is being used (optional)

        Returns:
            str: File content between the specified lines or error message
        """
        start_time = time.time()
        
        # Initialize tool usage stats if not present
        if 'getFileContentByLines' not in self.tool_usage_stats:
            self.tool_usage_stats['getFileContentByLines'] = {
                'count': 0, 'total_chars': 0, 'files_accessed': []
            }

        self.tool_usage_stats['getFileContentByLines']['count'] += 1

        # Debug logging for parameter investigation
        logger.debug(f"[TOOL] getFileContentByLines DEBUG - Raw parameters received:")
        logger.debug(f"  path: {repr(path)} (type: {type(path)})")
        logger.debug(f"  start_line: {repr(start_line)} (type: {type(start_line)})")
        logger.debug(f"  end_line: {repr(end_line)} (type: {type(end_line)})")
        logger.debug(f"  reason: {repr(reason)} (type: {type(reason)})")

        logger.info(f"[TOOL] getFileContentByLines called #{self.tool_usage_stats['getFileContentByLines']['count']} - Path: {path}, Lines: {start_line}-{end_line} | [AI REASONING] {reason if reason else 'No reason provided'}")

        try:
            # Validate input parameters
            if not path or not isinstance(path, str):
                error_msg = "Error: path parameter is required and must be a string"
                logger.error(f"[TOOL] getFileContentByLines - {error_msg}")
                logger.error(f"[TOOL] getFileContentByLines - DEBUG: Received path={repr(path)}, type={type(path)}")
                return error_msg

            if not isinstance(start_line, int) or start_line < 1:
                error_msg = "Error: startLine must be a positive integer (1-based)"
                logger.error(f"[TOOL] getFileContentByLines - {error_msg}")
                return error_msg

            if not isinstance(end_line, int) or end_line < 1:
                error_msg = "Error: endLine must be a positive integer (1-based)"
                logger.error(f"[TOOL] getFileContentByLines - {error_msg}")
                return error_msg

            if start_line > end_line:
                error_msg = f"Error: startLine ({start_line}) cannot be greater than endLine ({end_line})"
                logger.error(f"[TOOL] getFileContentByLines - {error_msg}")
                return error_msg

            # Clean up the path
            path = path.strip()

            # Use existing file resolution logic
            resolved_path, original_string = self._resolve_file_path(path)

            if not resolved_path:
                error_msg = f"File '{original_string}' cannot be found"
                logger.error(f"[TOOL] getFileContentByLines - {error_msg}")
                return error_msg

            # Read file content
            try:
                text = resolved_path.read_text(encoding="utf-8", errors="ignore")
                lines = text.split('\n')
                
                # Validate line numbers against actual file content
                total_lines = len(lines)
                if start_line > total_lines:
                    # Return JSON response with explicit end_of_file signal to prevent pagination loops
                    # This helps the LLM understand that there is no more content to read
                    error_response = {
                        "end_of_file": True,
                        "error": f"startLine ({start_line}) exceeds file length",
                        "file": path,
                        "total_lines": total_lines,
                        "valid_range": f"1-{total_lines}",
                        "message": f"File has only {total_lines} lines. There is no more content to read.",
                        "suggestion": f"The entire file content is available in lines 1-{total_lines}. Do not request lines beyond {total_lines}."
                    }
                    logger.warning(f"[TOOL] getFileContentByLines - startLine ({start_line}) exceeds file length ({total_lines} lines) - returning end_of_file signal")
                    return json.dumps(error_response, indent=2)

                if end_line > total_lines:
                    logger.warning(f"[TOOL] getFileContentByLines - endLine ({end_line}) exceeds file length ({total_lines} lines), adjusting to file end")
                    end_line = total_lines

                # Extract the requested lines (convert to 0-based indexing)
                extracted_lines = lines[start_line-1:end_line]
                
                # Add line numbers to the extracted content
                numbered_lines = []
                for i, line in enumerate(extracted_lines):
                    line_number = start_line + i
                    numbered_lines.append(f"{line_number:4d} | {line}")
                
                result = '\n'.join(numbered_lines)
                
                # Solution 1: Enhanced header with total line count
                header = f"File: {path} (lines {start_line}-{min(end_line, total_lines)} of {total_lines} total)\n"
                header += "=" * 50 + "\n"
                final_result = header + result

                # Update statistics
                self.tool_usage_stats['getFileContentByLines']['total_chars'] += len(final_result)
                self.tool_usage_stats['getFileContentByLines']['files_accessed'].append(path)

                execution_time = time.time() - start_time
                logger.info(f"[TOOL] getFileContentByLines completed - Path: {path}, "
                           f"Lines: {start_line}-{min(end_line, total_lines)}, "
                           f"Content: {len(final_result)} chars, Time: {execution_time:.2f}s")

                return final_result

            except Exception as e:
                error_msg = f"Error: Failed to read file '{resolved_path}': {e}"
                logger.error(f"[TOOL] getFileContentByLines - {error_msg}")
                return error_msg

        except Exception as e:
            execution_time = time.time() - start_time
            error_msg = f"Error retrieving file content by lines for '{path}': {str(e)}"
            logger.error(f"[TOOL] getFileContentByLines failed - Path: {path}, Error: {str(e)}, Time: {execution_time:.2f}s")
            return error_msg

    def execute_check_file_size_tool(self: ToolsBase, path: str, reason: str = None) -> str:
        """
        Execute checkFileSize tool to check if a file exists and get its size information.
        This tool should be used before readFile to determine if the file is within size limits.

        Args:
            path: Path to the file to check (can be relative path or just filename)
            reason: Reason why this tool is being used (optional)

        Returns:
            str: JSON response with file availability and size information
        """
        start_time = time.time()
        self.tool_usage_stats['checkFileSize']['count'] += 1

        logger.info(f"[TOOL] checkFileSize called #{self.tool_usage_stats['checkFileSize']['count']} - Path: {path} | [AI REASONING] {reason if reason else 'No reason provided'}")

        try:
            # Validate path parameter
            if not isinstance(path, str):
                error_result = {
                    "file_available": False,
                    "error": f"Invalid path parameter. Expected string, got {type(path)}: {path}"
                }
                return json.dumps(error_result, indent=2)

            path = path.strip()
            if not path:
                error_result = {
                    "file_available": False,
                    "error": "Path parameter is required and cannot be empty"
                }
                return json.dumps(error_result, indent=2)

            # Try to resolve the file path using existing resolution logic
            resolved_path, original_string = self._resolve_file_path(path)

            if not resolved_path:
                # File not found - try to search for it using fallback methods
                filename = os.path.basename(path)
                
                # Try using FileContentProvider if available
                if self.file_content_provider and hasattr(self.file_content_provider, 'guess_path'):
                    dir_path = os.path.dirname(path) if os.path.dirname(path) else ""
                    guessed_path = self.file_content_provider.guess_path(filename, dir_path)
                    if guessed_path:
                        # Validate the guessed path
                        guessed_validation_error = self._validate_file_path(guessed_path)
                        if guessed_validation_error is None:
                            resolved_path = Path(os.path.join(self.repo_path, guessed_path))
                            logger.info(f"[TOOL] checkFileSize - FileContentProvider resolved to: {guessed_path}")

                # If still not found, try directory walking
                if not resolved_path:
                    matching_files = []
                    for root, _, files in os.walk(self.repo_path):
                        if filename in files:
                            relative_path = os.path.relpath(os.path.join(root, filename), self.repo_path)
                            matching_files.append(relative_path)

                    if len(matching_files) == 1:
                        resolved_path = Path(os.path.join(self.repo_path, matching_files[0]))
                        logger.info(f"[TOOL] checkFileSize - Found unique file: {matching_files[0]}")
                    elif len(matching_files) > 1:
                        # Multiple files found
                        result = {
                            "file_available": False,
                            "error": f"Multiple files found with name '{filename}': {matching_files}. Please specify the full path."
                        }
                        self.tool_usage_stats['checkFileSize']['files_checked'].append(path)
                        execution_time = time.time() - start_time
                        logger.info(f"[TOOL] checkFileSize completed - Path: {path}, Multiple matches found, Time: {execution_time:.2f}s")
                        return json.dumps(result, indent=2)

            if not resolved_path or not resolved_path.exists():
                # File not found
                result = {
                    "file_available": False,
                    "error": f"File '{path}' not found in repository"
                }
            else:
                # File found - get size information
                try:
                    file_size_bytes = resolved_path.stat().st_size
                    
                    # Read file to get character count
                    try:
                        with open(resolved_path, 'r', encoding='utf-8', errors='ignore') as f:
                            content = f.read()
                            char_count = len(content)
                            line_count = len(content.splitlines())
                    except Exception:
                        # If we can't read as text, just use byte count
                        char_count = file_size_bytes
                        line_count = -1

                    # Determine if file is within recommended limits
                    within_size_limit = char_count <= MAX_FILE_CHARACTERS_FOR_READ_FILE
                    within_byte_limit = file_size_bytes <= MAX_FILE_SIZE_BYTES

                    # Get relative path for display
                    try:
                        display_path = str(resolved_path.relative_to(Path(self.repo_path)))
                    except ValueError:
                        display_path = str(resolved_path)

                    result = {
                        "file_available": True,
                        "file_path": display_path,
                        "size_bytes": file_size_bytes,
                        "size_characters": char_count,
                        "line_count": line_count if line_count > 0 else None,
                        "within_size_limit": within_size_limit and within_byte_limit,
                        "recommended_for_readFile": within_size_limit and within_byte_limit,
                        "size_limits": {
                            "max_characters": MAX_FILE_CHARACTERS_FOR_READ_FILE,
                            "max_bytes": MAX_FILE_SIZE_BYTES
                        }
                    }

                    if not (within_size_limit and within_byte_limit):
                        if not within_size_limit:
                            result["warning"] = f"File exceeds character limit ({char_count:,} > {MAX_FILE_CHARACTERS_FOR_READ_FILE:,} characters). Consider using getSummaryOfFile or getFileContentByLines instead."
                        elif not within_byte_limit:
                            result["warning"] = f"File exceeds byte limit ({file_size_bytes:,} > {MAX_FILE_SIZE_BYTES:,} bytes). Consider using getSummaryOfFile or getFileContentByLines instead."

                except Exception as e:
                    result = {
                        "file_available": True,
                        "error": f"File found but could not read size information: {e}"
                    }

            # Update statistics
            json_result = json.dumps(result, indent=2)
            self.tool_usage_stats['checkFileSize']['total_chars'] += len(json_result)
            self.tool_usage_stats['checkFileSize']['files_checked'].append(path)

            execution_time = time.time() - start_time
            logger.info(f"[TOOL] checkFileSize completed - Path: {path}, "
                       f"Available: {result.get('file_available', False)}, "
                       f"Size: {result.get('size_characters', 'unknown')} chars, "
                       f"Time: {execution_time:.2f}s")

            return json_result

        except Exception as e:
            execution_time = time.time() - start_time
            error_result = {
                "file_available": False,
                "error": f"Error checking file size for '{path}': {str(e)}"
            }
            logger.error(f"[TOOL] checkFileSize failed - Path: {path}, Error: {str(e)}, Time: {execution_time:.2f}s")
            return json.dumps(error_result, indent=2)