#!/usr/bin/env python3
"""
Tests for hindsight/utils/file_util.py

Tests the file utility module which provides:
- File reading and writing operations
- JSON file operations
- File information retrieval
- Directory operations
- Function context extraction
"""

import os
import json
import pytest
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

from hindsight.utils.file_util import (
    read_file,
    write_file,
    read_json_file,
    write_json_file,
    get_file_info,
    ensure_directory_exists,
    clear_directory_contents,
    get_file_line_count,
    read_file_with_line_numbers,
    extract_function_context,
    _create_truncated_function_name,
)


class TestReadFile:
    """Tests for read_file function."""

    def test_read_existing_file(self):
        """Test reading an existing file."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
            f.write("Hello, World!")
            temp_path = f.name
        
        try:
            content = read_file(temp_path)
            assert content == "Hello, World!"
        finally:
            os.unlink(temp_path)

    def test_read_nonexistent_file(self):
        """Test reading a nonexistent file returns None."""
        result = read_file("/nonexistent/path/file.txt")
        assert result is None

    def test_read_file_with_encoding(self):
        """Test reading file with specific encoding."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False, encoding='utf-8') as f:
            f.write("Unicode: 你好世界")
            temp_path = f.name
        
        try:
            content = read_file(temp_path, encoding='utf-8')
            assert "你好世界" in content
        finally:
            os.unlink(temp_path)


class TestWriteFile:
    """Tests for write_file function."""

    def test_write_file_basic(self):
        """Test basic file writing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = os.path.join(tmpdir, "test.txt")
            
            result = write_file(file_path, "Test content")
            
            assert result is True
            assert os.path.exists(file_path)
            with open(file_path, 'r') as f:
                assert f.read() == "Test content"

    def test_write_file_creates_directories(self):
        """Test that write_file creates parent directories."""
        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = os.path.join(tmpdir, "subdir", "nested", "test.txt")
            
            result = write_file(file_path, "Nested content", create_dirs=True)
            
            assert result is True
            assert os.path.exists(file_path)

    def test_write_file_no_create_dirs(self):
        """Test write_file without creating directories."""
        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = os.path.join(tmpdir, "nonexistent", "test.txt")
            
            result = write_file(file_path, "Content", create_dirs=False)
            
            assert result is False


class TestReadJsonFile:
    """Tests for read_json_file function."""

    def test_read_valid_json(self):
        """Test reading valid JSON file."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump({"key": "value", "number": 42}, f)
            temp_path = f.name
        
        try:
            result = read_json_file(temp_path)
            assert result == {"key": "value", "number": 42}
        finally:
            os.unlink(temp_path)

    def test_read_json_array(self):
        """Test reading JSON array."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump([1, 2, 3, "four"], f)
            temp_path = f.name
        
        try:
            result = read_json_file(temp_path)
            assert result == [1, 2, 3, "four"]
        finally:
            os.unlink(temp_path)

    def test_read_nonexistent_json(self):
        """Test reading nonexistent JSON file returns None."""
        result = read_json_file("/nonexistent/path/file.json")
        assert result is None

    def test_read_invalid_json(self):
        """Test reading invalid JSON file."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            f.write("not valid json {")
            temp_path = f.name
        
        try:
            result = read_json_file(temp_path)
            # Should return None or attempt to clean the JSON
            # The function has fallback logic to clean malformed JSON
        finally:
            os.unlink(temp_path)


class TestWriteJsonFile:
    """Tests for write_json_file function."""

    def test_write_json_dict(self):
        """Test writing JSON dictionary."""
        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = os.path.join(tmpdir, "test.json")
            data = {"key": "value", "list": [1, 2, 3]}
            
            result = write_json_file(file_path, data)
            
            assert result is True
            with open(file_path, 'r') as f:
                loaded = json.load(f)
            assert loaded == data

    def test_write_json_list(self):
        """Test writing JSON list."""
        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = os.path.join(tmpdir, "test.json")
            data = [1, 2, {"nested": "value"}]
            
            result = write_json_file(file_path, data)
            
            assert result is True
            with open(file_path, 'r') as f:
                loaded = json.load(f)
            assert loaded == data

    def test_write_json_with_indent(self):
        """Test writing JSON with custom indent."""
        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = os.path.join(tmpdir, "test.json")
            data = {"key": "value"}
            
            result = write_json_file(file_path, data, indent=4)
            
            assert result is True
            with open(file_path, 'r') as f:
                content = f.read()
            # Should have 4-space indentation
            assert "    " in content


class TestGetFileInfo:
    """Tests for get_file_info function."""

    def test_get_info_existing_file(self):
        """Test getting info for existing file."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
            f.write("Test content")
            temp_path = f.name
        
        try:
            info = get_file_info(temp_path)
            
            assert info['exists'] is True
            assert info['extension'] == '.txt'
            assert info['size'] > 0
            assert 'name' in info
            assert 'absolute_path' in info
            assert 'modified_time' in info
        finally:
            os.unlink(temp_path)

    def test_get_info_nonexistent_file(self):
        """Test getting info for nonexistent file."""
        info = get_file_info("/nonexistent/path/file.txt")
        
        assert info['exists'] is False
        assert info['size'] == 0


class TestEnsureDirectoryExists:
    """Tests for ensure_directory_exists function."""

    def test_create_new_directory(self):
        """Test creating a new directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            new_dir = os.path.join(tmpdir, "new_directory")
            
            result = ensure_directory_exists(new_dir)
            
            assert result is True
            assert os.path.isdir(new_dir)

    def test_existing_directory(self):
        """Test with existing directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            result = ensure_directory_exists(tmpdir)
            
            assert result is True

    def test_nested_directory_creation(self):
        """Test creating nested directories."""
        with tempfile.TemporaryDirectory() as tmpdir:
            nested_dir = os.path.join(tmpdir, "a", "b", "c")
            
            result = ensure_directory_exists(nested_dir)
            
            assert result is True
            assert os.path.isdir(nested_dir)


class TestClearDirectoryContents:
    """Tests for clear_directory_contents function."""

    def test_clear_directory_with_files(self):
        """Test clearing directory with files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create some files
            Path(tmpdir, "file1.txt").write_text("content1")
            Path(tmpdir, "file2.txt").write_text("content2")
            
            result = clear_directory_contents(tmpdir)
            
            assert result is True
            assert os.path.isdir(tmpdir)  # Directory still exists
            assert len(os.listdir(tmpdir)) == 0  # But is empty

    def test_clear_directory_with_subdirs(self):
        """Test clearing directory with subdirectories."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create subdirectory with files
            subdir = Path(tmpdir, "subdir")
            subdir.mkdir()
            Path(subdir, "file.txt").write_text("content")
            
            result = clear_directory_contents(tmpdir)
            
            assert result is True
            assert len(os.listdir(tmpdir)) == 0

    def test_clear_nonexistent_directory(self):
        """Test clearing nonexistent directory returns True."""
        result = clear_directory_contents("/nonexistent/path")
        assert result is True

    def test_clear_file_not_directory(self):
        """Test clearing a file (not directory) returns False."""
        with tempfile.NamedTemporaryFile(delete=False) as f:
            temp_path = f.name
        
        try:
            result = clear_directory_contents(temp_path)
            assert result is False
        finally:
            os.unlink(temp_path)


class TestGetFileLineCount:
    """Tests for get_file_line_count function."""

    def test_count_lines_existing_file(self):
        """Test counting lines in existing file."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
            f.write("line1\nline2\nline3\n")
            temp_path = f.name
        
        try:
            file_line_counts = {}
            count, is_accurate = get_file_line_count(temp_path, "/tmp", file_line_counts)
            
            assert count == 3
            assert is_accurate is True
            assert temp_path in file_line_counts
        finally:
            os.unlink(temp_path)

    def test_count_lines_cached(self):
        """Test that line count is cached."""
        file_line_counts = {"test.py": 100}
        
        count, is_accurate = get_file_line_count("test.py", "/tmp", file_line_counts)
        
        assert count == 100
        assert is_accurate is True

    def test_count_lines_nonexistent_file(self):
        """Test counting lines in nonexistent file."""
        file_line_counts = {}
        count, is_accurate = get_file_line_count("/nonexistent/file.py", "/tmp", file_line_counts)
        
        # Should return threshold + 1 and is_accurate = False
        assert count > 1000
        assert is_accurate is False


class TestReadFileWithLineNumbers:
    """Tests for read_file_with_line_numbers function."""

    def test_read_with_line_numbers(self):
        """Test reading file with line numbers."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
            f.write("line1\nline2\nline3\nline4\nline5\n")
            temp_path = f.name
        
        try:
            result = read_file_with_line_numbers(temp_path, "/tmp", 2, 4)
            
            assert "2 |" in result or "   2 |" in result
            assert "line2" in result
            assert "line3" in result
            assert "line4" in result
        finally:
            os.unlink(temp_path)

    def test_read_with_line_numbers_to_end(self):
        """Test reading file with line numbers to end."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
            f.write("line1\nline2\nline3\n")
            temp_path = f.name
        
        try:
            result = read_file_with_line_numbers(temp_path, "/tmp", 2, None)
            
            assert "line2" in result
            assert "line3" in result
        finally:
            os.unlink(temp_path)

    def test_read_nonexistent_file_with_line_numbers(self):
        """Test reading nonexistent file."""
        result = read_file_with_line_numbers("/nonexistent/file.py", "/tmp", 1, 10)
        
        assert "not found" in result.lower() or "error" in result.lower()


class TestExtractFunctionContext:
    """Tests for extract_function_context function."""

    def test_extract_function_context_basic(self):
        """Test extracting function context."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a source file
            file_path = os.path.join(tmpdir, "test.py")
            with open(file_path, 'w') as f:
                f.write("def func1():\n    pass\n\ndef func2():\n    return 42\n")
            
            entry = {
                'function': 'func2',
                'context': {
                    'file': 'test.py',
                    'start': 4,
                    'end': 5
                }
            }
            
            result = extract_function_context(entry, tmpdir, preserve_line_numbers=True)
            
            assert "func2" in result
            assert "return 42" in result

    def test_extract_function_context_no_file(self):
        """Test extracting context with no file in context."""
        entry = {
            'function': 'test',
            'context': {}
        }
        
        result = extract_function_context(entry, "/tmp", preserve_line_numbers=True)
        
        assert result == ""

    def test_extract_function_context_no_line_numbers(self):
        """Test extracting context with no line numbers."""
        entry = {
            'function': 'test',
            'context': {
                'file': 'test.py',
                'start': None,
                'end': None
            }
        }
        
        result = extract_function_context(entry, "/tmp", preserve_line_numbers=True)
        
        assert result == ""


class TestCreateTruncatedFunctionName:
    """Tests for _create_truncated_function_name function."""

    def test_truncate_long_function_name(self):
        """Test truncating long function name."""
        long_name = "LocationInternal_CLLocationInternalClient_CoreMotion_asynchronousRemoteObject"
        
        result = _create_truncated_function_name(long_name)
        
        # Should truncate all parts except the last
        assert "asynchronousRemoteObject" in result
        assert len(result) < len(long_name)

    def test_truncate_short_function_name(self):
        """Test with short function name (no underscores)."""
        short_name = "myFunction"
        
        result = _create_truncated_function_name(short_name)
        
        assert result == short_name

    def test_truncate_with_special_characters(self):
        """Test truncation with special characters."""
        name_with_special = "Class::method/path"
        
        result = _create_truncated_function_name(name_with_special)
        
        # Should sanitize special characters
        assert "::" not in result
        assert "/" not in result

    def test_truncate_single_underscore(self):
        """Test with single underscore."""
        name = "prefix_suffix"
        
        result = _create_truncated_function_name(name)
        
        # prefix should be truncated to 3 chars, suffix kept
        assert "suffix" in result
