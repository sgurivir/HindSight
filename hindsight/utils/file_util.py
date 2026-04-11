#!/usr/bin/env python3
# Created by Sridhar Gurivireddy
"""
File Utility Module
Provides centralized file operations including reading, writing, JSON parsing, and file info

Note: Line number utilities have been extracted to line_number_util.py
Note: Artifacts directory utilities have been extracted to artifacts_util.py
These are re-exported here for backward compatibility.
"""

# Standard library imports
import os
import json
import shutil
from pathlib import Path
from typing import Optional, Dict, Any, Union, Tuple

# Local imports
from .artifacts import get_repo_artifacts_dir
from .log_util import get_logger

# Re-export from line_number_util for backward compatibility
from .line_number_util import (
    validate_line_number_consistency,
    normalize_line_number_format,
    extract_line_range_from_content,
    get_line_number_from_content_line,
    remove_line_numbers_from_content,
    add_line_numbers,
    has_line_numbers,
)

# Re-export from artifacts_util for backward compatibility
from .artifacts_util import (
    get_platform_temp_dir,
    get_repository_folder_name,
    get_artifacts_temp_dir,
    get_artifacts_temp_file_path,
    get_artifacts_temp_subdir_path,
)


logger = get_logger(__name__)

# File reading and processing utilities for AST Call Graph Parser
FILE_READ_ENCODING = "utf-8"
FILE_READ_ERRORS = "ignore"
SMALL_FILE_LINE_THRESHOLD = 1000


def read_file(file_path: str, encoding: str = 'utf-8', errors: str = 'ignore') -> Optional[str]:
    """
    Read the content of a file.

    Args:
        file_path (str): Path to the file to read
        encoding (str): File encoding, defaults to 'utf-8'
        errors (str): How to handle encoding errors, defaults to 'ignore'

    Returns:
        str: File content or None if error
    """
    try:
        if not os.path.exists(file_path):
            logger.error(f"File not found: {file_path}")
            return None

        with open(file_path, 'r', encoding=encoding, errors=errors) as file:
            content = file.read()
            #logger.info(f"Successfully read file: {file_path} ({len(content)} characters)")
            return content

    except Exception as e:
        logger.error(f"Error reading file {file_path}: {e}")
        return None


def write_file(file_path: str, content: str, encoding: str = 'utf-8', create_dirs: bool = True) -> bool:
    """
    Write content to a file.

    Args:
        file_path (str): Path to the file to write
        content (str): Content to write
        encoding (str): File encoding, defaults to 'utf-8'
        create_dirs (bool): Whether to create parent directories if they don't exist

    Returns:
        bool: True if successful, False otherwise
    """
    try:
        if create_dirs:
            dir_path = os.path.dirname(file_path)
            if dir_path:  # Only create directories if there's actually a directory path
                os.makedirs(dir_path, exist_ok=True)

        with open(file_path, 'w', encoding=encoding) as file:
            file.write(content)
            logger.info(f"Successfully wrote file: {file_path} ({len(content)} characters)")
            return True

    except Exception as e:
        logger.error(f"Error writing file {file_path}: {e}")
        return False


def read_json_file(file_path: str) -> Optional[Union[Dict, list]]:
    """
    Read and parse JSON file. Attempts to clean malformed JSON content if initial parsing fails.

    Args:
        file_path (str): Path to the JSON file

    Returns:
        Union[Dict, list]: Parsed JSON object or None if error
    """
    try:
        if not os.path.exists(file_path):
            logger.info(f"JSON file not found: {file_path}")
            return None

        with open(file_path, 'r', encoding='utf-8') as file:
            try:
                # First attempt: try to parse as-is
                content = json.load(file)
                logger.info(f"Successfully loaded JSON from: {file_path}")
                return content
            except json.JSONDecodeError as e:
                logger.warning(f"Initial JSON parsing failed for {file_path}: {e}")
                logger.info(f"Attempting to clean and re-parse JSON content...")

                # Second attempt: read as text and clean the content
                file.seek(0)  # Reset file pointer to beginning
                raw_content = file.read()

                # Import here to avoid circular imports
                from .json_util import clean_json_response

                # Clean the JSON content
                cleaned_content = clean_json_response(raw_content)

                if cleaned_content:
                    try:
                        parsed_content = json.loads(cleaned_content)
                        logger.info(f"Successfully loaded cleaned JSON from: {file_path}")
                        return parsed_content
                    except json.JSONDecodeError as e2:
                        logger.error(f"Error parsing cleaned JSON content from {file_path}: {e2}")
                        logger.debug(f"Cleaned content preview: {cleaned_content[:200]}...")
                        return None
                else:
                    logger.error(f"No valid JSON content found after cleaning {file_path}")
                    return None

    except Exception as e:
        logger.error(f"Error reading JSON file {file_path}: {e}")
        return None


def write_json_file(file_path: str, data: Union[Dict, list], indent: int = 2, create_dirs: bool = True) -> bool:
    """
    Write data to JSON file.

    Args:
        file_path (str): Path to the JSON file
        data (Union[Dict, list]): Data to write
        indent (int): JSON indentation, defaults to 2
        create_dirs (bool): Whether to create parent directories if they don't exist

    Returns:
        bool: True if successful, False otherwise
    """
    try:
        if create_dirs:
            dir_path = os.path.dirname(file_path)
            if dir_path:  # Only create directories if there's actually a directory path
                os.makedirs(dir_path, exist_ok=True)

        with open(file_path, 'w', encoding='utf-8') as file:
            json.dump(data, file, indent=indent, ensure_ascii=False)
            logger.info(f"Successfully wrote JSON to: {file_path}")
            return True

    except Exception as e:
        logger.error(f"Error writing JSON file {file_path}: {e}")
        return False


def get_file_info(file_path: str) -> Dict[str, Any]:
    """
    Get file information including size, extension, and existence.

    Args:
        file_path (str): Path to the file

    Returns:
        dict: File information
    """
    try:
        path_obj = Path(file_path)

        if not os.path.exists(file_path):
            return {
                "name": path_obj.name,
                "extension": path_obj.suffix,
                "exists": False,
                "size": 0,
                "absolute_path": str(path_obj.resolve())
            }

        stat = os.stat(file_path)

        return {
            "name": path_obj.name,
            "extension": path_obj.suffix,
            "size": stat.st_size,
            "exists": True,
            "absolute_path": str(path_obj.resolve()),
            "modified_time": stat.st_mtime,
            "created_time": stat.st_ctime
        }
    except Exception as e:
        logger.error(f"Error getting file info for {file_path}: {e}")
        return {
            "exists": False,
            "error": str(e)
        }


def ensure_directory_exists(directory_path: str) -> bool:
    """
    Ensure a directory exists, create it if it doesn't.

    Args:
        directory_path (str): Path to the directory

    Returns:
        bool: True if directory exists or was created successfully
    """
    try:
        os.makedirs(directory_path, exist_ok=True)
        logger.debug(f"Directory ensured: {directory_path}")
        return True
    except Exception as e:
        logger.error(f"Error creating directory {directory_path}: {e}")
        return False


def clear_directory_contents(directory_path: str) -> bool:
    """
    Clear all contents in a directory while keeping the directory itself.

    Args:
        directory_path (str): Path to the directory to clear

    Returns:
        bool: True if successful, False otherwise
    """
    try:
        dir_path = Path(directory_path)

        if not dir_path.exists():
            logger.info(f"Directory does not exist: {directory_path}")
            return True

        if not dir_path.is_dir():
            logger.error(f"Path is not a directory: {directory_path}")
            return False

        logger.info(f"Clearing contents of directory: {directory_path}")

        # Remove all contents but keep the directory itself
        for item in dir_path.iterdir():
            if item.is_dir():
                shutil.rmtree(item)
                logger.debug(f"Removed directory: {item}")
            else:
                item.unlink()
                logger.debug(f"Removed file: {item}")

        logger.info(f"Successfully cleared directory contents: {directory_path}")
        return True

    except Exception as e:
        logger.error(f"Error clearing directory contents {directory_path}: {e}")
        return False




def get_file_line_count(file_path: str, repo_path: str, file_line_counts: Dict[str, int]) -> Tuple[int, bool]:
    """
    Get the total line count of a file, with caching and validation.
    
    Returns:
        Tuple[int, bool]: (line_count, is_accurate)
    """
    if file_path in file_line_counts:
        return file_line_counts[file_path], True

    # Try with the original path first
    actual_path = file_path

    # If file doesn't exist, try with the repo prefix
    if not os.path.exists(actual_path):
        actual_path = f"{repo_path}/{file_path}"

    try:
        if os.path.exists(actual_path):
            with open(actual_path, 'r', encoding=FILE_READ_ENCODING, errors=FILE_READ_ERRORS) as f:
                line_count = sum(1 for _ in f)
            file_line_counts[file_path] = line_count
            logger.debug(f"File {file_path}: {line_count} lines")
            return line_count, True
        else:
            # If file doesn't exist, assume it's large to preserve structure
            logger.warning(f"File {file_path}: not found, assuming {SMALL_FILE_LINE_THRESHOLD}+ lines")
            file_line_counts[file_path] = SMALL_FILE_LINE_THRESHOLD + 1
            return SMALL_FILE_LINE_THRESHOLD + 1, False
    except Exception as e:
        # On error, assume large file
        logger.warning(f"Error reading {file_path}: {e}, assuming {SMALL_FILE_LINE_THRESHOLD}+ lines")
        file_line_counts[file_path] = SMALL_FILE_LINE_THRESHOLD + 1
        return SMALL_FILE_LINE_THRESHOLD + 1, False


def read_file_with_line_numbers(file_path: str, repo_path: str, start_line: int, end_line: int = None) -> str:
    """
    Read file content with line numbers, supporting line ranges and proper offset.
    
    Args:
        file_path: Path to the file
        repo_path: Repository root path
        start_line: Starting line number (1-based, inclusive)
        end_line: Ending line number (1-based, inclusive). If None, read to end of file
        
    Returns:
        str: File content with line numbers, preserving original line numbers
    """
    # Try with the original path first
    actual_path = file_path

    # If file doesn't exist, try with the repo prefix
    if not os.path.exists(actual_path):
        actual_path = f"{repo_path}/{file_path}"

    try:
        if os.path.exists(actual_path):
            with open(actual_path, 'r', encoding=FILE_READ_ENCODING, errors=FILE_READ_ERRORS) as f:
                lines = f.readlines()
                
                # Validate line numbers
                total_lines = len(lines)
                if start_line < 1:
                    start_line = 1
                if start_line > total_lines:
                    return f"Error: Start line {start_line} exceeds file length ({total_lines} lines)"
                
                if end_line is None:
                    end_line = total_lines
                elif end_line > total_lines:
                    end_line = total_lines
                elif end_line < start_line:
                    return f"Error: End line {end_line} is before start line {start_line}"
                
                # Extract the requested range (convert to 0-based indexing)
                selected_lines = lines[start_line-1:end_line]
                
                # Add line numbers preserving original file line numbers
                numbered_lines = []
                for i, line in enumerate(selected_lines):
                    original_line_number = start_line + i
                    numbered_lines.append(f"{original_line_number:4d} | {line.rstrip()}")
                
                return '\n'.join(numbered_lines)
        else:
            return f"File not found: {file_path}"
    except Exception as e:
        return f"Error reading file {file_path}: {str(e)}"


def extract_function_context(entry: Dict, repo_path: str, preserve_line_numbers: bool) -> str:
    """
    Extract function context from entry for large files with better line number handling.
    
    Args:
        entry: Function entry containing context information
        repo_path: Repository root path
        preserve_line_numbers: Whether to preserve original file line numbers
        
    Returns:
        str: Function context with proper line numbers
    """
    context = entry.get('context', {})
    if not context.get('file'):
        return ""

    file_path = context['file']
    file_path = f"{repo_path}/{file_path}"
    start_line = context.get('start')
    end_line = context.get('end')

    function_name = entry.get('function', 'Unknown')

    if start_line is None or end_line is None:
        return ""

    try:
        if os.path.exists(file_path):
            with open(file_path, 'r', encoding=FILE_READ_ENCODING, errors=FILE_READ_ERRORS) as f:
                lines = f.readlines()
                
                # Validate line numbers
                if start_line <= len(lines) and end_line <= len(lines) and start_line > 0 and end_line > 0:
                    function_lines = lines[start_line-1:end_line]
                    
                    if preserve_line_numbers:
                        # Preserve original file line numbers
                        numbered_lines = [f"{start_line+i:4d} | {line.rstrip()}"
                                        for i, line in enumerate(function_lines)]
                    else:
                        # Use sequential numbering starting from 1
                        numbered_lines = [f"{i+1:4d} | {line.rstrip()}"
                                        for i, line in enumerate(function_lines)]
                    
                    result = '\n'.join(numbered_lines)
                    return result
                else:
                    return f"Error: Invalid line range {start_line}-{end_line} for file with {len(lines)} lines"
        return f"Could not extract function context from {file_path}"
    except Exception as e:
        return f"Error extracting function context: {str(e)}"


def load_ast_tracking_data(tracking_file: str) -> Dict[str, list]:
    """Load AST tracking data from external JSON file."""
    if os.path.exists(tracking_file):
        try:
            with open(tracking_file, 'r', encoding=FILE_READ_ENCODING) as f:
                return json.load(f)
        except Exception as e:
            print(f"Warning: Could not load tracking file {tracking_file}: {e}")
    return {}


def _create_truncated_function_name(function_name: str) -> str:
    """
    Create truncated function name using first 3 letters of each part except the last.

    Example:
    LocationInternal_CLLocationInternalClient_CoreMotion_asynchronousRemoteObject
    becomes: Loc_CLL_Cor_asynchronousRemoteObject

    Args:
        function_name: Original function name

    Returns:
        Truncated function name
    """
    # First sanitize the function name
    safe_name = function_name.replace("::", "_").replace(":", "_").replace("/", "_").replace("\\", "_")

    # Split by underscores to get individual parts
    parts = safe_name.split("_")

    if len(parts) <= 1:
        # If no underscores or only one part, return as-is
        return safe_name

    # Truncate all parts except the last one to first 3 characters
    truncated_parts = []
    for i, part in enumerate(parts):
        if i == len(parts) - 1:
            # Last part - keep full name
            truncated_parts.append(part)
        else:
            # Truncate to first 3 characters, but ensure we have at least 1 character
            if len(part) >= 3:
                truncated_parts.append(part[:3])
            elif len(part) > 0:
                truncated_parts.append(part)
            # Skip empty parts

    return "_".join(truncated_parts)


# Note: The following functions are now imported from their respective modules:
# - get_platform_temp_dir, get_repository_folder_name, get_artifacts_temp_dir,
#   get_artifacts_temp_file_path, get_artifacts_temp_subdir_path (from artifacts_util)
# - validate_line_number_consistency, normalize_line_number_format,
#   extract_line_range_from_content, get_line_number_from_content_line,
#   remove_line_numbers_from_content, add_line_numbers, has_line_numbers (from line_number_util)