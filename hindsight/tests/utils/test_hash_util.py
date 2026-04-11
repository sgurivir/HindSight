#!/usr/bin/env python3
"""
Tests for hindsight.utils.hash_util module.
"""

import os
import sys
import json
import tempfile
import hashlib
from pathlib import Path

import pytest

# Add project root to path
project_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(project_root))

from hindsight.utils.hash_util import HashUtil


class TestHashForContentMD5:
    """Tests for HashUtil.hash_for_content_md5()"""

    def test_hash_for_content_md5_basic(self):
        """Test basic MD5 hash generation for content."""
        content = "Hello, World!"
        result = HashUtil.hash_for_content_md5(content)
        
        # Verify it's a valid MD5 hash (32 hex characters)
        assert len(result) == 32
        assert all(c in '0123456789abcdef' for c in result)
        
        # Verify it matches expected MD5
        expected = hashlib.md5(content.encode('utf-8')).hexdigest()
        assert result == expected

    def test_hash_for_content_md5_empty_string(self):
        """Test MD5 hash for empty string returns 'None'."""
        result = HashUtil.hash_for_content_md5("")
        assert result == "None"

    def test_hash_for_content_md5_none(self):
        """Test MD5 hash for None returns 'None'."""
        result = HashUtil.hash_for_content_md5(None)
        assert result == "None"

    def test_hash_for_content_md5_consistency(self):
        """Test that same content produces same hash."""
        content = "Test content for hashing"
        hash1 = HashUtil.hash_for_content_md5(content)
        hash2 = HashUtil.hash_for_content_md5(content)
        assert hash1 == hash2

    def test_hash_for_content_md5_different_content(self):
        """Test that different content produces different hashes."""
        hash1 = HashUtil.hash_for_content_md5("Content A")
        hash2 = HashUtil.hash_for_content_md5("Content B")
        assert hash1 != hash2

    def test_hash_for_content_md5_unicode(self):
        """Test MD5 hash with unicode content."""
        content = "Unicode: 你好世界 🌍"
        result = HashUtil.hash_for_content_md5(content)
        assert len(result) == 32
        assert all(c in '0123456789abcdef' for c in result)


class TestHashForRecordSHA256:
    """Tests for HashUtil.hash_for_record_sha256()"""

    def test_hash_for_record_sha256_string(self):
        """Test SHA256 hash for string record."""
        record = "test record"
        result = HashUtil.hash_for_record_sha256(record)
        
        # Verify it's a valid SHA256 hash (64 hex characters)
        assert len(result) == 64
        assert all(c in '0123456789abcdef' for c in result)

    def test_hash_for_record_sha256_dict(self):
        """Test SHA256 hash for dictionary record."""
        record = {"key": "value", "number": 42}
        result = HashUtil.hash_for_record_sha256(record)
        
        assert len(result) == 64
        assert all(c in '0123456789abcdef' for c in result)

    def test_hash_for_record_sha256_dict_order_independent(self):
        """Test that dict hash is consistent regardless of key order."""
        record1 = {"a": 1, "b": 2, "c": 3}
        record2 = {"c": 3, "a": 1, "b": 2}
        
        hash1 = HashUtil.hash_for_record_sha256(record1)
        hash2 = HashUtil.hash_for_record_sha256(record2)
        
        # Should be same because keys are sorted
        assert hash1 == hash2

    def test_hash_for_record_sha256_other_types(self):
        """Test SHA256 hash for other types (converted to string)."""
        result = HashUtil.hash_for_record_sha256(12345)
        assert len(result) == 64


class TestHashForFileMD5:
    """Tests for HashUtil.hash_for_file_md5()"""

    def test_hash_for_file_md5_basic(self, temp_dir):
        """Test MD5 hash for file contents."""
        # Create a test file
        file_path = os.path.join(temp_dir, "test.txt")
        content = "Test file content"
        with open(file_path, 'w') as f:
            f.write(content)
        
        result = HashUtil.hash_for_file_md5(file_path)
        
        # Verify it's a valid MD5 hash
        assert len(result) == 32
        assert all(c in '0123456789abcdef' for c in result)

    def test_hash_for_file_md5_path_object(self, temp_dir):
        """Test MD5 hash with Path object."""
        file_path = Path(temp_dir) / "test.txt"
        with open(file_path, 'w') as f:
            f.write("Test content")
        
        result = HashUtil.hash_for_file_md5(file_path)
        assert len(result) == 32

    def test_hash_for_file_md5_nonexistent_file(self):
        """Test MD5 hash for non-existent file returns empty string."""
        result = HashUtil.hash_for_file_md5("/nonexistent/path/file.txt")
        assert result == ""

    def test_hash_for_file_md5_consistency(self, temp_dir):
        """Test that same file produces same hash."""
        file_path = os.path.join(temp_dir, "test.txt")
        with open(file_path, 'w') as f:
            f.write("Consistent content")
        
        hash1 = HashUtil.hash_for_file_md5(file_path)
        hash2 = HashUtil.hash_for_file_md5(file_path)
        assert hash1 == hash2


class TestHashForPromptSHA256:
    """Tests for HashUtil.hash_for_prompt_sha256()"""

    def test_hash_for_prompt_sha256_default_truncation(self):
        """Test SHA256 hash with default truncation length."""
        prompt = "System prompt for LLM"
        result = HashUtil.hash_for_prompt_sha256(prompt)
        
        # Default truncation is 16 characters
        assert len(result) == 16
        assert all(c in '0123456789abcdef' for c in result)

    def test_hash_for_prompt_sha256_custom_truncation(self):
        """Test SHA256 hash with custom truncation length."""
        prompt = "System prompt for LLM"
        result = HashUtil.hash_for_prompt_sha256(prompt, truncate_length=8)
        
        assert len(result) == 8

    def test_hash_for_prompt_sha256_full_length(self):
        """Test SHA256 hash with full length (64 characters)."""
        prompt = "System prompt for LLM"
        result = HashUtil.hash_for_prompt_sha256(prompt, truncate_length=64)
        
        assert len(result) == 64


class TestHashForSignatureMD5:
    """Tests for HashUtil.hash_for_signature_md5()"""

    def test_hash_for_signature_md5_basic(self):
        """Test MD5 hash for trace signatures."""
        functions = ["func1", "func2", "func3"]
        file_paths = ["file1.py", "file2.py"]
        
        result = HashUtil.hash_for_signature_md5(functions, file_paths)
        
        assert len(result) == 32
        assert all(c in '0123456789abcdef' for c in result)

    def test_hash_for_signature_md5_order_independent(self):
        """Test that signature hash is order-independent."""
        functions1 = ["func1", "func2", "func3"]
        functions2 = ["func3", "func1", "func2"]
        file_paths1 = ["file1.py", "file2.py"]
        file_paths2 = ["file2.py", "file1.py"]
        
        hash1 = HashUtil.hash_for_signature_md5(functions1, file_paths1)
        hash2 = HashUtil.hash_for_signature_md5(functions2, file_paths2)
        
        # Should be same because lists are sorted
        assert hash1 == hash2

    def test_hash_for_signature_md5_empty_lists(self):
        """Test signature hash with empty lists."""
        result = HashUtil.hash_for_signature_md5([], [])
        assert len(result) == 32


class TestHashForCallstackMD5:
    """Tests for HashUtil.hash_for_callstack_md5()"""

    def test_hash_for_callstack_md5_basic(self):
        """Test MD5 hash for callstack text."""
        callstack = "at func1()\nat func2()\nat func3()"
        result = HashUtil.hash_for_callstack_md5(callstack)
        
        # Default truncation is 12 characters
        assert len(result) == 12
        assert all(c in '0123456789abcdef' for c in result)

    def test_hash_for_callstack_md5_empty(self):
        """Test callstack hash for empty string returns 'empty'."""
        result = HashUtil.hash_for_callstack_md5("")
        assert result == "empty"

    def test_hash_for_callstack_md5_none(self):
        """Test callstack hash for None returns 'empty'."""
        result = HashUtil.hash_for_callstack_md5(None)
        assert result == "empty"

    def test_hash_for_callstack_md5_custom_truncation(self):
        """Test callstack hash with custom truncation."""
        callstack = "at func1()\nat func2()"
        result = HashUtil.hash_for_callstack_md5(callstack, truncate_length=8)
        
        assert len(result) == 8


class TestHashForCallstackContextMD5:
    """Tests for HashUtil.hash_for_callstack_context_md5()"""

    def test_hash_for_callstack_context_md5_basic(self):
        """Test MD5 hash for callstack with context."""
        callstack_with_context = {
            "callstack": ["func1", "func2"],
            "context": {"key": "value"}
        }
        result = HashUtil.hash_for_callstack_context_md5(callstack_with_context)
        
        assert len(result) == 12
        assert all(c in '0123456789abcdef' for c in result)

    def test_hash_for_callstack_context_md5_custom_truncation(self):
        """Test callstack context hash with custom truncation."""
        callstack_with_context = {"callstack": ["func1"]}
        result = HashUtil.hash_for_callstack_context_md5(callstack_with_context, truncate_length=16)
        
        assert len(result) == 16


class TestHashForFileIdentifierMD5:
    """Tests for HashUtil.hash_for_file_identifier_md5()"""

    def test_hash_for_file_identifier_md5_basic(self):
        """Test MD5 hash for file identifier."""
        file_path = "/path/to/file.py"
        result = HashUtil.hash_for_file_identifier_md5(file_path)
        
        # Default truncation is 8 characters
        assert len(result) == 8
        assert all(c in '0123456789abcdef' for c in result)

    def test_hash_for_file_identifier_md5_custom_truncation(self):
        """Test file identifier hash with custom truncation."""
        file_path = "/path/to/file.py"
        result = HashUtil.hash_for_file_identifier_md5(file_path, truncate_length=16)
        
        assert len(result) == 16


class TestHashForFunctionAnalysisSHA256:
    """Tests for HashUtil.hash_for_function_analysis_sha256()"""

    def test_hash_for_function_analysis_sha256_basic(self):
        """Test SHA256 hash for function analysis."""
        function_data = "def example_function(): pass"
        result = HashUtil.hash_for_function_analysis_sha256(function_data)
        
        # Default truncation is 16 characters
        assert len(result) == 16
        assert all(c in '0123456789abcdef' for c in result)

    def test_hash_for_function_analysis_sha256_custom_truncation(self):
        """Test function analysis hash with custom truncation."""
        function_data = "def example_function(): pass"
        result = HashUtil.hash_for_function_analysis_sha256(function_data, truncate_length=32)
        
        assert len(result) == 32


class TestHashForDummyChecksumMD5:
    """Tests for HashUtil.hash_for_dummy_checksum_md5()"""

    def test_hash_for_dummy_checksum_md5_basic(self):
        """Test MD5 hash for dummy checksum."""
        identifier = "test_identifier"
        result = HashUtil.hash_for_dummy_checksum_md5(identifier)
        
        assert len(result) == 32
        assert all(c in '0123456789abcdef' for c in result)

    def test_hash_for_dummy_checksum_md5_consistency(self):
        """Test that same identifier produces same dummy checksum."""
        identifier = "test_identifier"
        hash1 = HashUtil.hash_for_dummy_checksum_md5(identifier)
        hash2 = HashUtil.hash_for_dummy_checksum_md5(identifier)
        assert hash1 == hash2


class TestHashForCombinedComponentsMD5:
    """Tests for HashUtil.hash_for_combined_components_md5()"""

    def test_hash_for_combined_components_md5_basic(self):
        """Test MD5 hash for combined components."""
        components = ["component1", "component2", "component3"]
        result = HashUtil.hash_for_combined_components_md5(components)
        
        assert len(result) == 32
        assert all(c in '0123456789abcdef' for c in result)

    def test_hash_for_combined_components_md5_empty_list(self):
        """Test combined components hash for empty list returns 'None'."""
        result = HashUtil.hash_for_combined_components_md5([])
        assert result == "None"

    def test_hash_for_combined_components_md5_single_component(self):
        """Test combined components hash with single component."""
        components = ["single_component"]
        result = HashUtil.hash_for_combined_components_md5(components)
        
        assert len(result) == 32


class TestHashForDataTypesMD5:
    """Tests for HashUtil.hash_for_data_types_md5()"""

    def test_hash_for_data_types_md5_basic(self):
        """Test MD5 hash for data type checksums."""
        data_type_checksums = ["checksum1", "checksum2", "checksum3"]
        result = HashUtil.hash_for_data_types_md5(data_type_checksums)
        
        assert len(result) == 32
        assert all(c in '0123456789abcdef' for c in result)

    def test_hash_for_data_types_md5_empty_list(self):
        """Test data types hash for empty list returns 'None'."""
        result = HashUtil.hash_for_data_types_md5([])
        assert result == "None"


class TestHashForFunctionsMD5:
    """Tests for HashUtil.hash_for_functions_md5()"""

    def test_hash_for_functions_md5_basic(self):
        """Test MD5 hash for function checksums."""
        function_checksums = ["func_checksum1", "func_checksum2"]
        result = HashUtil.hash_for_functions_md5(function_checksums)
        
        assert len(result) == 32
        assert all(c in '0123456789abcdef' for c in result)

    def test_hash_for_functions_md5_empty_list(self):
        """Test functions hash for empty list returns 'None'."""
        result = HashUtil.hash_for_functions_md5([])
        assert result == "None"
