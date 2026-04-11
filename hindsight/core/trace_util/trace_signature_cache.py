#!/usr/bin/env python3
"""
Duplicate Elimination Utility for aggregatedMicroStackShot Plugin
Handles tracking and elimination of duplicate function/file combinations during LLM analysis.
"""

import os
import json
from typing import Dict, List, Set, Tuple, Any

from ...utils.file_util import read_json_file, write_json_file
from ...utils.hash_util import HashUtil


class TraceSignatureCache:
    """
    Utility class to handle trace signature caching for LLM analysis.
    Tracks analyzed functions and file paths to avoid redundant analysis.
    """

    def __init__(self, cache_file_path: str, logger=None):
        """
        Initialize the duplicate elimination utility.

        Args:
            cache_file_path: Path to the cache file for tracking analyzed items
            logger: Logger instance for logging operations
        """
        self.cache_file_path = cache_file_path
        self.logger = logger
        self.analyzed_cache = self._load_analyzed_cache()

    def _load_analyzed_cache(self) -> Set[str]:
        """
        Load the analyzed cache from file.

        Returns:
            Set of analyzed signatures
        """
        if os.path.exists(self.cache_file_path):
            try:
                cache_data = read_json_file(self.cache_file_path)
                if cache_data is not None:
                    return set(cache_data.get('analyzed_signatures', []))
                return set()
            except Exception as e:
                if self.logger:
                    self.logger.warning(f"Failed to load analyzed cache: {e}")
                return set()
        return set()

    def _save_analyzed_cache(self):
        """Save the analyzed cache to file, merging with existing data to prevent overwrites."""
        try:
            # Ensure directory exists
            os.makedirs(os.path.dirname(self.cache_file_path), exist_ok=True)

            # Read current file state to merge with our cache (prevents race conditions)
            existing_signatures = set()
            if os.path.exists(self.cache_file_path):
                try:
                    existing_data = read_json_file(self.cache_file_path)
                    if existing_data is not None:
                        existing_signatures = set(existing_data.get('analyzed_signatures', []))
                except Exception as e:
                    if self.logger:
                        self.logger.warning(f"Could not read existing cache file for merging: {e}")

            # Merge existing signatures with current cache
            merged_signatures = existing_signatures.union(self.analyzed_cache)

            # Update in-memory cache to include any signatures that might have missed
            self.analyzed_cache = merged_signatures

            # Save merged data
            cache_data = {
                'analyzed_signatures': list(merged_signatures)
            }

            write_json_file(self.cache_file_path, cache_data, indent=2)

            if self.logger:
                self.logger.debug(f"Saved cache with {len(merged_signatures)} signatures "
                                f"(merged {len(existing_signatures)} existing + {len(self.analyzed_cache)} current)")

        except Exception as e:
            if self.logger:
                self.logger.error(f"Failed to save analyzed cache: {e}")

    def extract_functions_and_files(self, trace_file_path: str) -> Tuple[List[str], List[str]]:
        """
        Extract top-level functions and file paths from a generated_AST_trace_graphs file.
        Excludes functions from the 'invoking' section.

        Args:
            trace_file_path: Path to the trace file

        Returns:
            Tuple of (functions, file_paths) lists
        """
        functions = []
        file_paths = []

        try:
            trace_data = read_json_file(trace_file_path)
            if trace_data is None:
                return functions, file_paths

            # Extract from context section (excluding invoking)
            context_items = trace_data.get('context', [])

            for item in context_items:
                # Extract function name
                function_name = item.get('function')
                if function_name and function_name not in functions:
                    functions.append(function_name)

                # Extract file path from context
                context_info = item.get('context', {})
                file_path = context_info.get('file')
                if file_path and file_path not in file_paths:
                    file_paths.append(file_path)

            if self.logger:
                self.logger.debug(f"Extracted {len(functions)} functions and {len(file_paths)} file paths from {os.path.basename(trace_file_path)}")

        except Exception as e:
            if self.logger:
                self.logger.error(f"Failed to extract functions and files from {trace_file_path}: {e}")

        return functions, file_paths

    def create_signature(self, functions: List[str], file_paths: List[str]) -> str:
        """
        Create a unique signature for the combination of functions and file paths.

        Args:
            functions: List of function names
            file_paths: List of file paths

        Returns:
            Unique signature string
        """
        return HashUtil.hash_for_signature_md5(functions, file_paths)

    def is_already_analyzed(self, trace_file_path: str) -> bool:
        """
        Check if a trace file has already been analyzed based on its functions and file paths.

        Args:
            trace_file_path: Path to the trace file

        Returns:
            True if already analyzed, False otherwise
        """
        try:
            functions, file_paths = self.extract_functions_and_files(trace_file_path)

            signature = self.create_signature(functions, file_paths)

            # Check if this signature exists in the cache
            is_duplicate = signature in self.analyzed_cache

            if is_duplicate and self.logger:
                self.logger.debug(f"Found duplicate signature for {os.path.basename(trace_file_path)}: {signature}")

            return is_duplicate

        except Exception as e:
            if self.logger:
                self.logger.error(f"Error checking if {trace_file_path} is already analyzed: {e}")
            return False

    def mark_as_analyzed(self, trace_file_path: str):
        """
        Mark a trace file as analyzed by storing its function/file signature.

        Args:
            trace_file_path: Path to the trace file that was analyzed
        """
        try:
            functions, file_paths = self.extract_functions_and_files(trace_file_path)

            signature = self.create_signature(functions, file_paths)

            # Add signature to cache
            self.analyzed_cache.add(signature)

            # Save cache
            self._save_analyzed_cache()

            if self.logger:
                self.logger.debug(f"Marked {os.path.basename(trace_file_path)} as analyzed "
                                f"(functions: {len(functions)}, files: {len(file_paths)}, signature: {signature})")

        except Exception as e:
            if self.logger:
                self.logger.error(f"Error marking {trace_file_path} as analyzed: {e}")

    def get_analysis_stats(self) -> Dict[str, Any]:
        """
        Get statistics about the analyzed cache.

        Returns:
            Dictionary with cache statistics
        """
        return {
            'total_unique_signatures': len(self.analyzed_cache),
            'cache_file_path': self.cache_file_path
        }

    def clear_cache(self):
        """Clear the analyzed cache."""
        self.analyzed_cache = set()
        if os.path.exists(self.cache_file_path):
            try:
                os.remove(self.cache_file_path)
                if self.logger:
                    self.logger.info(f"Cleared analyzed cache: {self.cache_file_path}")
            except Exception as e:
                if self.logger:
                    self.logger.error(f"Failed to remove cache file {self.cache_file_path}: {e}")
