#!/usr/bin/env python3
"""
Tests for hindsight/core/llm/tools/file_tools.py - File Tools Module.

This module tests:
- readFile: Read file contents with automatic pruning for large files
- getFileContentByLines: Read specific line ranges from files
- checkFileSize: Check file existence and size information
"""

import json
import os
import tempfile
import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from hindsight.core.llm.tools.tools import Tools
from hindsight.utils.directory_tree_util import DirectoryTreeUtil


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def temp_repo():
    """Create a temporary repository with test files."""
    temp_dir = tempfile.mkdtemp()
    
    # Create test files
    test_file = os.path.join(temp_dir, "test.py")
    with open(test_file, 'w') as f:
        f.write("""def hello():
    '''Say hello'''
    print("Hello, World!")
    return True

def goodbye():
    '''Say goodbye'''
    print("Goodbye!")
    return False

class MyClass:
    def __init__(self):
        self.value = 42
    
    def get_value(self):
        return self.value
""")
    
    # Create a large file for testing pruning
    large_file = os.path.join(temp_dir, "large_file.py")
    with open(large_file, 'w') as f:
        # Write a file larger than MAX_FILE_CHARACTERS_FOR_READ_FILE
        for i in range(5000):
            f.write(f"def function_{i}():\n")
            f.write(f"    '''Function {i} docstring'''\n")
            f.write(f"    x = {i}\n")
            f.write(f"    return x * 2\n\n")
    
    # Create nested directory structure
    nested_dir = os.path.join(temp_dir, "src", "utils")
    os.makedirs(nested_dir, exist_ok=True)
    
    nested_file = os.path.join(nested_dir, "helper.py")
    with open(nested_file, 'w') as f:
        f.write("def helper_func():\n    pass\n")
    
    yield temp_dir
    
    if os.path.exists(temp_dir):
        shutil.rmtree(temp_dir)


@pytest.fixture
def tools_instance(temp_repo):
    """Create a Tools instance for testing."""
    directory_tree_util = DirectoryTreeUtil()
    
    tools = Tools(
        repo_path=temp_repo,
        directory_tree_util=directory_tree_util
    )
    
    return tools


# ============================================================================
# readFile Tool Tests
# ============================================================================

class TestReadFileTool:
    """Tests for readFile tool."""

    def test_read_file_success(self, tools_instance, temp_repo):
        """Test reading a file successfully."""
        result = tools_instance.execute_tool_use({
            "name": "readFile",
            "input": {"path": "test.py"}
        })
        
        assert "def hello" in result
        assert "def goodbye" in result
        assert "class MyClass" in result

    def test_read_file_with_line_numbers(self, tools_instance, temp_repo):
        """Test that readFile adds line numbers."""
        result = tools_instance.execute_tool_use({
            "name": "readFile",
            "input": {"path": "test.py"}
        })
        
        # Line numbers should be present
        assert "|" in result  # Line number separator

    def test_read_file_not_found(self, tools_instance):
        """Test reading a non-existent file."""
        result = tools_instance.execute_tool_use({
            "name": "readFile",
            "input": {"path": "nonexistent.py"}
        })
        
        assert "cannot be found" in result.lower() or "not found" in result.lower()

    def test_read_file_nested_path(self, tools_instance, temp_repo):
        """Test reading a file in nested directory."""
        result = tools_instance.execute_tool_use({
            "name": "readFile",
            "input": {"path": "src/utils/helper.py"}
        })
        
        assert "def helper_func" in result

    def test_read_file_missing_path_parameter(self, tools_instance):
        """Test readFile with missing path parameter."""
        result = tools_instance.execute_tool_use({
            "name": "readFile",
            "input": {}
        })
        
        assert "error" in result.lower() or "cannot be found" in result.lower()

    def test_read_file_handler_direct_call(self, tools_instance, temp_repo):
        """Test calling _handle_read_file directly."""
        result = tools_instance._handle_read_file(path="test.py")
        
        assert "def hello" in result

    def test_read_file_execute_read_file_tool(self, tools_instance, temp_repo):
        """Test calling execute_read_file_tool directly."""
        result = tools_instance.execute_read_file_tool("test.py")
        
        assert "def hello" in result

    def test_read_file_updates_stats(self, tools_instance, temp_repo):
        """Test that readFile updates tool usage stats."""
        initial_count = tools_instance.tool_usage_stats.get('readFile', {}).get('count', 0)
        
        tools_instance.execute_tool_use({
            "name": "readFile",
            "input": {"path": "test.py"}
        })
        
        new_count = tools_instance.tool_usage_stats['readFile']['count']
        assert new_count == initial_count + 1


# ============================================================================
# getFileContentByLines Tool Tests
# ============================================================================

class TestGetFileContentByLinesTool:
    """Tests for getFileContentByLines tool."""

    def test_get_file_content_by_lines_success(self, tools_instance, temp_repo):
        """Test getting specific lines from a file."""
        result = tools_instance.execute_tool_use({
            "name": "getFileContentByLines",
            "input": {"path": "test.py", "startLine": 1, "endLine": 5}
        })
        
        assert "def hello" in result
        assert "File:" in result  # Header should be present

    def test_get_file_content_by_lines_with_line_numbers(self, tools_instance, temp_repo):
        """Test that line numbers are included in output."""
        result = tools_instance.execute_tool_use({
            "name": "getFileContentByLines",
            "input": {"path": "test.py", "startLine": 1, "endLine": 3}
        })
        
        # Should have line numbers like "   1 |"
        assert "1 |" in result or "1|" in result

    def test_get_file_content_by_lines_middle_section(self, tools_instance, temp_repo):
        """Test getting lines from middle of file."""
        result = tools_instance.execute_tool_use({
            "name": "getFileContentByLines",
            "input": {"path": "test.py", "startLine": 6, "endLine": 10}
        })
        
        assert "def goodbye" in result

    def test_get_file_content_by_lines_invalid_start_line(self, tools_instance, temp_repo):
        """Test with invalid start line (0 or negative)."""
        result = tools_instance.execute_tool_use({
            "name": "getFileContentByLines",
            "input": {"path": "test.py", "startLine": 0, "endLine": 5}
        })
        
        assert "error" in result.lower()

    def test_get_file_content_by_lines_start_greater_than_end(self, tools_instance, temp_repo):
        """Test with start line greater than end line."""
        result = tools_instance.execute_tool_use({
            "name": "getFileContentByLines",
            "input": {"path": "test.py", "startLine": 10, "endLine": 5}
        })
        
        assert "error" in result.lower()

    def test_get_file_content_by_lines_exceeds_file_length(self, tools_instance, temp_repo):
        """Test with end line exceeding file length."""
        result = tools_instance.execute_tool_use({
            "name": "getFileContentByLines",
            "input": {"path": "test.py", "startLine": 1, "endLine": 1000}
        })
        
        # Should adjust to file end and return content
        assert "def hello" in result
        # Should include total line count in header (Solution 1)
        assert "total" in result.lower()

    def test_get_file_content_by_lines_start_exceeds_file_length(self, tools_instance, temp_repo):
        """Test with start line exceeding file length - should return enhanced error message."""
        result = tools_instance.execute_tool_use({
            "name": "getFileContentByLines",
            "input": {"path": "test.py", "startLine": 1000, "endLine": 1005}
        })
        
        # Solution 2: Enhanced error message should include helpful guidance
        assert "error" in result.lower()
        assert "total lines" in result.lower() or "valid" in result.lower()
        # Should suggest using checkFileSize or provide valid range
        assert "suggestion" in result.lower() or "valid line range" in result.lower()

    def test_get_file_content_by_lines_file_not_found(self, tools_instance):
        """Test with non-existent file."""
        result = tools_instance.execute_tool_use({
            "name": "getFileContentByLines",
            "input": {"path": "nonexistent.py", "startLine": 1, "endLine": 5}
        })
        
        assert "cannot be found" in result.lower() or "not found" in result.lower()

    def test_get_file_content_by_lines_missing_parameters(self, tools_instance):
        """Test with missing required parameters."""
        result = tools_instance.execute_tool_use({
            "name": "getFileContentByLines",
            "input": {"path": "test.py"}  # Missing startLine and endLine
        })
        
        # Should handle gracefully (may use defaults or return error)
        assert result is not None

    def test_get_file_content_by_lines_with_reason(self, tools_instance, temp_repo):
        """Test with reason parameter."""
        result = tools_instance.execute_tool_use({
            "name": "getFileContentByLines",
            "input": {
                "path": "test.py",
                "startLine": 1,
                "endLine": 5,
                "reason": "Checking function definition"
            }
        })
        
        assert "def hello" in result

    def test_get_file_content_by_lines_handler_direct_call(self, tools_instance, temp_repo):
        """Test calling _handle_get_file_content_by_lines directly."""
        result = tools_instance._handle_get_file_content_by_lines(
            path="test.py",
            startLine=1,
            endLine=5
        )
        
        assert "def hello" in result

    def test_get_file_content_by_lines_execute_tool_direct(self, tools_instance, temp_repo):
        """Test calling execute_get_file_content_by_lines_tool directly."""
        result = tools_instance.execute_get_file_content_by_lines_tool(
            path="test.py",
            start_line=1,
            end_line=5
        )
        
        assert "def hello" in result

    def test_get_file_content_by_lines_updates_stats(self, tools_instance, temp_repo):
        """Test that getFileContentByLines updates tool usage stats."""
        tools_instance.execute_tool_use({
            "name": "getFileContentByLines",
            "input": {"path": "test.py", "startLine": 1, "endLine": 5}
        })
        
        assert tools_instance.tool_usage_stats['getFileContentByLines']['count'] >= 1

    def test_get_file_content_by_lines_header_includes_total_lines(self, tools_instance, temp_repo):
        """Test that response header includes total line count (Solution 1)."""
        result = tools_instance.execute_tool_use({
            "name": "getFileContentByLines",
            "input": {"path": "test.py", "startLine": 1, "endLine": 5}
        })
        
        # Header should include "of X total" format
        assert "total" in result.lower()
        # Should show the line range requested
        assert "lines 1-5" in result.lower() or "lines 1-5" in result

    def test_get_file_content_alias(self, tools_instance, temp_repo):
        """Test that getFileContent alias works."""
        result = tools_instance.execute_tool_use({
            "name": "getFileContent",
            "input": {"path": "test.py", "startLine": 1, "endLine": 5}
        })
        
        assert "def hello" in result


# ============================================================================
# checkFileSize Tool Tests
# ============================================================================

class TestCheckFileSizeTool:
    """Tests for checkFileSize tool."""

    def test_check_file_size_success(self, tools_instance, temp_repo):
        """Test checking file size successfully."""
        result = tools_instance.execute_tool_use({
            "name": "checkFileSize",
            "input": {"path": "test.py"}
        })
        
        result_data = json.loads(result)
        
        assert result_data["file_available"] is True
        assert "size_bytes" in result_data
        assert "size_characters" in result_data
        assert "line_count" in result_data

    def test_check_file_size_file_not_found(self, tools_instance):
        """Test checking size of non-existent file."""
        result = tools_instance.execute_tool_use({
            "name": "checkFileSize",
            "input": {"path": "nonexistent.py"}
        })
        
        result_data = json.loads(result)
        
        assert result_data["file_available"] is False
        assert "error" in result_data

    def test_check_file_size_within_limits(self, tools_instance, temp_repo):
        """Test that small files are within limits."""
        result = tools_instance.execute_tool_use({
            "name": "checkFileSize",
            "input": {"path": "test.py"}
        })
        
        result_data = json.loads(result)
        
        assert result_data["file_available"] is True
        assert result_data["within_size_limit"] is True
        assert result_data["recommended_for_readFile"] is True

    def test_check_file_size_large_file(self, tools_instance, temp_repo):
        """Test checking size of large file."""
        result = tools_instance.execute_tool_use({
            "name": "checkFileSize",
            "input": {"path": "large_file.py"}
        })
        
        result_data = json.loads(result)
        
        assert result_data["file_available"] is True
        assert "size_bytes" in result_data
        # Large file may or may not be within limits depending on constants

    def test_check_file_size_includes_limits_info(self, tools_instance, temp_repo):
        """Test that response includes size limits information."""
        result = tools_instance.execute_tool_use({
            "name": "checkFileSize",
            "input": {"path": "test.py"}
        })
        
        result_data = json.loads(result)
        
        assert "size_limits" in result_data
        assert "max_characters" in result_data["size_limits"]
        assert "max_bytes" in result_data["size_limits"]

    def test_check_file_size_nested_path(self, tools_instance, temp_repo):
        """Test checking size of file in nested directory."""
        result = tools_instance.execute_tool_use({
            "name": "checkFileSize",
            "input": {"path": "src/utils/helper.py"}
        })
        
        result_data = json.loads(result)
        
        assert result_data["file_available"] is True

    def test_check_file_size_with_reason(self, tools_instance, temp_repo):
        """Test checkFileSize with reason parameter."""
        result = tools_instance.execute_tool_use({
            "name": "checkFileSize",
            "input": {
                "path": "test.py",
                "reason": "Checking if file is small enough to read"
            }
        })
        
        result_data = json.loads(result)
        
        assert result_data["file_available"] is True

    def test_check_file_size_empty_path(self, tools_instance):
        """Test checkFileSize with empty path."""
        result = tools_instance.execute_tool_use({
            "name": "checkFileSize",
            "input": {"path": ""}
        })
        
        result_data = json.loads(result)
        
        assert result_data["file_available"] is False
        assert "error" in result_data

    def test_check_file_size_invalid_path_type(self, tools_instance):
        """Test checkFileSize with invalid path type."""
        result = tools_instance.execute_tool_use({
            "name": "checkFileSize",
            "input": {"path": 123}  # Invalid type
        })
        
        result_data = json.loads(result)
        
        assert result_data["file_available"] is False

    def test_check_file_size_handler_direct_call(self, tools_instance, temp_repo):
        """Test calling _handle_check_file_size directly."""
        result = tools_instance._handle_check_file_size(path="test.py")
        
        result_data = json.loads(result)
        
        assert result_data["file_available"] is True

    def test_check_file_size_execute_tool_direct(self, tools_instance, temp_repo):
        """Test calling execute_check_file_size_tool directly."""
        result = tools_instance.execute_check_file_size_tool(path="test.py")
        
        result_data = json.loads(result)
        
        assert result_data["file_available"] is True

    def test_check_file_size_updates_stats(self, tools_instance, temp_repo):
        """Test that checkFileSize updates tool usage stats."""
        initial_count = tools_instance.tool_usage_stats.get('checkFileSize', {}).get('count', 0)
        
        tools_instance.execute_tool_use({
            "name": "checkFileSize",
            "input": {"path": "test.py"}
        })
        
        new_count = tools_instance.tool_usage_stats['checkFileSize']['count']
        assert new_count == initial_count + 1

    def test_check_file_size_file_path_display(self, tools_instance, temp_repo):
        """Test that file_path is included in response."""
        result = tools_instance.execute_tool_use({
            "name": "checkFileSize",
            "input": {"path": "test.py"}
        })
        
        result_data = json.loads(result)
        
        assert "file_path" in result_data
        assert "test.py" in result_data["file_path"]


# ============================================================================
# Tool Integration Tests
# ============================================================================

class TestFileToolsIntegration:
    """Integration tests for file tools working together."""

    def test_check_size_then_read(self, tools_instance, temp_repo):
        """Test workflow: check size, then read if within limits."""
        # First check size
        size_result = tools_instance.execute_tool_use({
            "name": "checkFileSize",
            "input": {"path": "test.py"}
        })
        
        size_data = json.loads(size_result)
        
        # If within limits, read the file
        if size_data["recommended_for_readFile"]:
            read_result = tools_instance.execute_tool_use({
                "name": "readFile",
                "input": {"path": "test.py"}
            })
            
            assert "def hello" in read_result

    def test_check_size_then_get_lines(self, tools_instance, temp_repo):
        """Test workflow: check size, then get specific lines."""
        # First check size
        size_result = tools_instance.execute_tool_use({
            "name": "checkFileSize",
            "input": {"path": "test.py"}
        })
        
        size_data = json.loads(size_result)
        
        # Get specific lines based on line count
        if size_data.get("line_count"):
            lines_result = tools_instance.execute_tool_use({
                "name": "getFileContentByLines",
                "input": {
                    "path": "test.py",
                    "startLine": 1,
                    "endLine": min(5, size_data["line_count"])
                }
            })
            
            assert "def hello" in lines_result

    def test_all_file_tools_same_file(self, tools_instance, temp_repo):
        """Test all file tools on the same file."""
        # checkFileSize
        size_result = tools_instance.execute_tool_use({
            "name": "checkFileSize",
            "input": {"path": "test.py"}
        })
        size_data = json.loads(size_result)
        assert size_data["file_available"] is True
        
        # readFile
        read_result = tools_instance.execute_tool_use({
            "name": "readFile",
            "input": {"path": "test.py"}
        })
        assert "def hello" in read_result
        
        # getFileContentByLines
        lines_result = tools_instance.execute_tool_use({
            "name": "getFileContentByLines",
            "input": {"path": "test.py", "startLine": 1, "endLine": 5}
        })
        assert "def hello" in lines_result
