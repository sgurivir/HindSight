#!/usr/bin/env python3
"""
Hash Utility Module
Centralized hash generation for all Hindsight components
Author: Sridhar Gurivireddy
"""

import hashlib
import json
from pathlib import Path
from typing import Union, List, Dict, Any


class HashUtil:
    """
    Centralized utility class for all hash generation in Hindsight.
    Provides consistent hash methods for different use cases across the codebase.
    """

    @staticmethod
    def hash_for_content_md5(content: str) -> str:
        """
        Generate MD5 hash for content-based hashing (AST functions, data types, call graphs).
        Used for content that needs to survive machine reboots with consistent checksums.

        Args:
            content: String content to hash

        Returns:
            str: MD5 hash as hexadecimal string
        """
        if not content:
            return "None"
        return hashlib.md5(content.encode('utf-8')).hexdigest()

    @staticmethod
    def hash_for_record_sha256(record: Union[str, Dict[str, Any], Any]) -> str:
        """
        Generate SHA256 hash for record tracking and unique identification.
        Used for analyzed records registry and similar tracking systems.

        Args:
            record: Record to hash (string, dict, or other serializable object)

        Returns:
            str: SHA256 hash as hexadecimal string
        """
        # Convert record to string representation for hashing
        if isinstance(record, dict):
            # Sort keys for consistent hashing
            record_str = json.dumps(record, sort_keys=True)
        elif isinstance(record, str):
            record_str = record
        else:
            # Convert other types to string
            record_str = str(record)

        # Generate SHA256 hash
        return hashlib.sha256(record_str.encode('utf-8')).hexdigest()

    @staticmethod
    def hash_for_file_md5(file_path: Union[str, Path]) -> str:
        """
        Generate MD5 hash for file contents to detect changes.
        Used for file caching and change detection in project summary generation.

        Args:
            file_path: Path to the file to hash

        Returns:
            str: MD5 hash of file contents, empty string on error
        """
        try:
            hash_md5 = hashlib.md5()
            with open(file_path, "rb") as f:
                for chunk in iter(lambda: f.read(4096), b""):
                    hash_md5.update(chunk)
            return hash_md5.hexdigest()
        except Exception:
            return ""

    @staticmethod
    def hash_for_prompt_sha256(system_prompt: str, truncate_length: int = 16) -> str:
        """
        Generate SHA256 hash for system prompts in TTL management.
        Used for optimizing Claude API usage by tracking prompt cache status.

        Args:
            system_prompt: System prompt content
            truncate_length: Length to truncate hash to (default: 16)

        Returns:
            str: Truncated SHA256 hash as hexadecimal string
        """
        full_hash = hashlib.sha256(system_prompt.encode('utf-8')).hexdigest()
        return full_hash[:truncate_length]

    @staticmethod
    def hash_for_signature_md5(functions: List[str], file_paths: List[str]) -> str:
        """
        Generate MD5 hash for trace signatures to prevent duplicate analysis.
        Creates unique signatures for function/file combinations.

        Args:
            functions: List of function names
            file_paths: List of file paths

        Returns:
            str: MD5 hash as hexadecimal string
        """
        # Sort to ensure consistent signature regardless of order
        sorted_functions = sorted(functions)
        sorted_file_paths = sorted(file_paths)

        # Create a combined string and hash it
        combined = json.dumps({
            'functions': sorted_functions,
            'file_paths': sorted_file_paths
        }, sort_keys=True)

        return hashlib.md5(combined.encode()).hexdigest()

    @staticmethod
    def hash_for_callstack_md5(callstack_text: str, truncate_length: int = 12) -> str:
        """
        Generate MD5 hash for callstack deduplication in reports.
        Creates consistent IDs for callstacks with same content.

        Args:
            callstack_text: Callstack text content
            truncate_length: Length to truncate hash to (default: 12)

        Returns:
            str: Truncated MD5 hash as hexadecimal string
        """
        if not callstack_text:
            return "empty"
        full_hash = hashlib.md5(callstack_text.encode()).hexdigest()
        return full_hash[:truncate_length]

    @staticmethod
    def hash_for_callstack_context_md5(callstack_with_context: Dict[str, Any], truncate_length: int = 12) -> str:
        """
        Generate MD5 hash for callstack with context in reports.
        Creates consistent IDs for callstacks with context data.

        Args:
            callstack_with_context: Dictionary containing callstack and context
            truncate_length: Length to truncate hash to (default: 12)

        Returns:
            str: Truncated MD5 hash as hexadecimal string
        """
        combined_json = json.dumps(callstack_with_context, sort_keys=True)
        full_hash = hashlib.md5(combined_json.encode()).hexdigest()
        return full_hash[:truncate_length]

    @staticmethod
    def hash_for_file_identifier_md5(file_path: str, truncate_length: int = 8) -> str:
        """
        Generate MD5 hash for file identification and output naming.
        Used for deterministic file identifiers in code analysis.

        Args:
            file_path: File path to hash
            truncate_length: Length to truncate hash to (default: 8)

        Returns:
            str: Truncated MD5 hash as hexadecimal string
        """
        full_hash = hashlib.md5(file_path.encode('utf-8')).hexdigest()
        return full_hash[:truncate_length]

    @staticmethod
    def hash_for_function_analysis_sha256(function_data: str, truncate_length: int = 16) -> str:
        """
        Generate SHA256 hash for function analysis tracking.
        Creates unique identifiers for function analysis sessions.

        Args:
            function_data: Function data to hash
            truncate_length: Length to truncate hash to (default: 16)

        Returns:
            str: Truncated SHA256 hash as hexadecimal string
        """
        full_hash = hashlib.sha256(function_data.encode('utf-8')).hexdigest()
        return full_hash[:truncate_length]

    @staticmethod
    def hash_for_dummy_checksum_md5(identifier: str) -> str:
        """
        Generate MD5 hash for dummy checksums when real content isn't available.
        Used as fallback in AST function signature generation.

        Args:
            identifier: Identifier to create dummy checksum for

        Returns:
            str: MD5 hash as hexadecimal string
        """
        dummy_content = f"dummy_{identifier}"
        return hashlib.md5(dummy_content.encode('utf-8')).hexdigest()

    @staticmethod
    def hash_for_combined_components_md5(components: List[str]) -> str:
        """
        Generate MD5 hash for combined components in call graph checksums.
        Used for creating final checksums from multiple components.

        Args:
            components: List of component strings to combine and hash

        Returns:
            str: MD5 hash as hexadecimal string
        """
        if not components:
            return "None"
        combined_content = "|".join(components)
        return hashlib.md5(combined_content.encode('utf-8')).hexdigest()

    @staticmethod
    def hash_for_data_types_md5(data_type_checksums: List[str]) -> str:
        """
        Generate MD5 hash for data type checksums in call graph functions.

        Args:
            data_type_checksums: List of data type checksum strings

        Returns:
            str: MD5 hash as hexadecimal string
        """
        if not data_type_checksums:
            return "None"
        combined_data_types = ",".join(data_type_checksums)
        return hashlib.md5(combined_data_types.encode('utf-8')).hexdigest()

    @staticmethod
    def hash_for_functions_md5(function_checksums: List[str]) -> str:
        """
        Generate MD5 hash for function checksums in call graph functions.

        Args:
            function_checksums: List of function checksum strings

        Returns:
            str: MD5 hash as hexadecimal string
        """
        if not function_checksums:
            return "None"
        combined_functions = ",".join(function_checksums)
        return hashlib.md5(combined_functions.encode('utf-8')).hexdigest()