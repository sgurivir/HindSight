#!/usr/bin/env python3
"""
File System Results Cache
Concrete implementation of ResultsCache that uses local file system
"""

import os
import json
import threading
import time
from contextlib import contextmanager
from typing import Any, Dict, List, Optional
from .interface.prior_results_store_interface import ResultsCache
from hindsight.utils.log_util import get_logger

# Import centralized schema
from hindsight.core.schema.code_analysis_result_schema import (
    CodeAnalysisResult,
    CodeAnalysisResultValidator
)

logger = get_logger(__name__)


class FileSystemResultsCache(ResultsCache):
    """
    File system implementation of ResultsCache.
    Stores results in JSON files and can lookup existing results with timeout support.
    """

    def __init__(self, base_output_dir: str):
        """
        Initialize the file system results cache.

        Args:
            base_output_dir: Base directory for output (e.g., ~/hindsight_artifacts)
        """
        self.base_output_dir = base_output_dir
        self._repo_dirs: Dict[str, str] = {}  # repo_name -> analysis_dir mapping
        self._result_index: Dict[str, str] = {}  # (file,func,checksum) -> file_path
        self._lock = threading.RLock()
        self.current_repo_name: Optional[str] = None

    @contextmanager
    def _timeout_context(self, timeout_seconds: float):
        """Context manager for timeout handling using threading.Timer."""
        timeout_occurred = threading.Event()

        def timeout_handler():
            timeout_occurred.set()

        # Set up timeout using threading.Timer (thread-safe)
        timer = threading.Timer(timeout_seconds, timeout_handler)
        timer.start()

        try:
            yield timeout_occurred
        finally:
            # Clean up timer
            timer.cancel()

    def has_result(self, file_name: str, function_name: str, checksum: str, timeout_seconds: float = 15.0) -> bool:
        """
        Check if result exists with timeout.

        Args:
            file_name: Name of the file
            function_name: Name of the function
            checksum: Function checksum
            timeout_seconds: Maximum time to wait for lookup

        Returns:
            True if result exists, False otherwise or on timeout
        """
        try:
            with self._timeout_context(timeout_seconds) as timeout_occurred:
                key = self._make_key(file_name, function_name, checksum)
                with self._lock:
                    # Check if timeout occurred during lock acquisition
                    if timeout_occurred.is_set():
                        logger.warning(f"Timeout checking result existence in file system store for {function_name}")
                        return False

                    exists = key in self._result_index
                    logger.info(f"DEBUG: has_result lookup - file='{file_name}', func='{function_name}', checksum='{checksum[:8]}...', key='{key}', exists={exists}, index_size={len(self._result_index)}")
                    if len(self._result_index) <= 5:
                        logger.info(f"DEBUG: All index keys: {list(self._result_index.keys())}")
                    else:
                        logger.info(f"DEBUG: Sample index keys: {list(self._result_index.keys())[:5]}")
                    return exists
        except Exception as e:
            logger.warning(f"Error checking result existence: {e}")
            return False

    def get_existing_result(self, file_name: str, function_name: str, checksum: str, timeout_seconds: float = 15.0) -> Optional[Dict[str, Any]]:
        """
        Load and return existing result with timeout.

        Args:
            file_name: Name of the file
            function_name: Name of the function
            checksum: Function checksum
            timeout_seconds: Maximum time to wait for lookup

        Returns:
            Existing result or None if not found or on timeout
        """
        try:
            with self._timeout_context(timeout_seconds) as timeout_occurred:
                key = self._make_key(file_name, function_name, checksum)
                with self._lock:
                    # Check if timeout occurred during lock acquisition
                    if timeout_occurred.is_set():
                        logger.warning(f"Timeout loading result from file system store for {function_name}")
                        return None

                    if key in self._result_index:
                        file_path = self._result_index[key]
                        try:
                            with open(file_path, 'r', encoding='utf-8') as f:
                                result_data = json.load(f)

                            # Use centralized schema to normalize and validate the result
                            normalized_result = CodeAnalysisResultValidator.normalize_result(
                                result_data,
                                file_path=file_name,
                                function=function_name,
                                checksum=checksum
                            )

                            # Validate the result
                            validation_errors = normalized_result.validate()
                            if validation_errors:
                                logger.warning(f"File system result validation failed for {function_name}: {validation_errors}")
                                return None

                            # Return the standardized dictionary format
                            logger.debug(f"Retrieved and validated existing result for {function_name} from file system")
                            return normalized_result.to_dict()

                        except Exception as e:
                            logger.warning(f"Failed to load or normalize file system result for {function_name}: {e}")
                            return None
        except Exception as e:
            logger.warning(f"Error loading existing result: {e}")

        return None

    def initialize_for_repo(self, repo_name: str) -> None:
        """
        Initialize the store for a specific repository.

        Args:
            repo_name: Name of the repository
        """
        with self._lock:
            self.current_repo_name = repo_name
            analysis_dir = self._get_analysis_dir(repo_name)
            self._build_result_index(repo_name, analysis_dir)
            logger.info(f"Initialized file system store for repo '{repo_name}' with {len(self._result_index)} existing results")

    def _get_analysis_dir(self, repo_name: str) -> str:
        """
        Get the analysis directory for a repository.

        Args:
            repo_name: Name of the repository

        Returns:
            Path to the analysis directory
        """
        if repo_name not in self._repo_dirs:
            # Create the directory structure: base_output_dir/repo_name/results/code_analysis
            repo_artifacts_dir = os.path.join(self.base_output_dir, repo_name)
            analysis_dir = os.path.join(repo_artifacts_dir, "results", "code_analysis")
            os.makedirs(analysis_dir, exist_ok=True)
            self._repo_dirs[repo_name] = analysis_dir

        return self._repo_dirs[repo_name]

    def _build_result_index(self, repo_name: str, analysis_dir: str) -> None:
        """
        Build index of existing result files for fast lookup.

        Args:
            repo_name: Name of the repository
            analysis_dir: Path to the analysis directory
        """
        if not os.path.exists(analysis_dir):
            logger.debug(f"Analysis directory does not exist: {analysis_dir}")
            return

        indexed_count = 0
        for filename in os.listdir(analysis_dir):
            if filename.endswith('_analysis.json'):
                file_path = os.path.join(analysis_dir, filename)
                try:
                    with open(file_path, 'r', encoding='utf-8') as f:
                        result_data = json.load(f)

                    # Check if result_data is a dictionary (expected format)
                    if not isinstance(result_data, dict):
                        logger.warning(f"Skipping result file {file_path}: expected dict but got {type(result_data).__name__}")
                        continue

                    file_name = result_data.get('file_path', '')
                    function_name = result_data.get('function', '')
                    checksum = result_data.get('checksum', '')

                    if file_name and function_name and checksum:
                        key = self._make_key(file_name, function_name, checksum)
                        self._result_index[key] = file_path
                        indexed_count += 1
                        logger.debug(f"Indexed: {filename} -> key: {key}")

                except Exception as e:
                    logger.warning(f"Error indexing result file {file_path}: {e}")

        logger.info(f"Indexed {indexed_count} result files for repo '{repo_name}' in {analysis_dir}")
        if indexed_count > 0:
            logger.debug(f"Sample keys in index: {list(self._result_index.keys())[:3]}")

    def _make_key(self, file_name: str, function_name: str, checksum: str) -> str:
        """
        Create lookup key from parameters.
        Ensures file paths are relative to repository root for consistent lookups.

        Args:
            file_name: Name of the file (can be relative or absolute path)
            function_name: Name of the function
            checksum: Function checksum

        Returns:
            Lookup key string using relative file path
        """
        # Convert file path to relative path from repository root
        # This ensures consistent keys whether we get absolute or relative paths
        relative_file = file_name

        # If it's an absolute path, try to make it relative
        if os.path.isabs(file_name):
            # Try to find the repository root and make path relative
            # Look for common repository indicators in the path
            path_parts = file_name.split(os.sep)

            # Find potential repo root by looking for common repo directory names
            # or use the last meaningful directory before the file
            for i, part in enumerate(path_parts):
                if part and not part.startswith('.'):
                    # Take everything from this point as the relative path
                    relative_file = '/'.join(path_parts[i:])
                    break

        # Clean up the relative path
        relative_file = relative_file.lstrip('./').lstrip('/')

        key = f"{relative_file}:{function_name}:{checksum}"
        return key

    def _generate_filename(self, result: Dict[str, Any]) -> str:
        """
        Generate filename using the same logic as the current implementation.

        Args:
            result: The result data

        Returns:
            Generated filename
        """
        function_name = result.get('function', 'unknown')
        file_path = result.get('file_path', 'unknown')
        checksum = result.get('checksum', 'unknown')

        # Create safe filename components
        safe_function_name = "".join(c for c in function_name if c.isalnum() or c in ('_', '-'))
        safe_file_name = "".join(c for c in os.path.basename(file_path) if c.isalnum() or c in ('_', '-', '.'))

        # Truncate function name and file name to prevent filesystem length issues
        max_function_name_length = 100
        max_file_name_length = 50
        
        if len(safe_function_name) > max_function_name_length:
            safe_function_name = safe_function_name[:max_function_name_length]
            
        if len(safe_file_name) > max_file_name_length:
            safe_file_name = safe_file_name[:max_file_name_length]

        # Use checksum or generate hash from file path
        if checksum and checksum != "None" and checksum != "unknown":
            checksum_hash = checksum[:8] if len(checksum) > 8 else checksum
        else:
            # Fallback to simple hash of file path
            checksum_hash = str(abs(hash(file_path)))[:8]

        # Generate filename matching current format: function_file_checksum_analysis.json
        filename = f"{safe_function_name}_{safe_file_name}_{checksum_hash}_analysis.json"

        return filename

    # Implement CodeAnalysisSubscriber methods

    def on_result_added(self, result_id: str, result: Dict[str, Any]) -> None:
        """
        Called when a new result is added to the store.
        Writes the result to a JSON file and updates the index.

        Args:
            result_id: Unique identifier for the result
            result: The result data that was added
        """
        try:
            # Use current repo name if available
            repo_name = self.current_repo_name or self._extract_repo_name(result)
            analysis_dir = self._get_analysis_dir(repo_name)

            # Generate filename using the same logic as current implementation
            filename = self._generate_filename(result)
            file_path = os.path.join(analysis_dir, filename)

            # Use centralized schema to normalize and validate the result before storage
            try:
                normalized_result = CodeAnalysisResultValidator.normalize_result(
                    result,
                    file_path=result.get('file_path', ''),
                    function=result.get('function', ''),
                    checksum=result.get('checksum', '')
                )

                # Validate the result
                validation_errors = normalized_result.validate()
                if validation_errors:
                    logger.warning(f"Result validation failed, skipping storage: {validation_errors}")
                    return

                # Store the standardized format
                standardized_dict = normalized_result.to_dict()

                # Write the standardized result to JSON file
                with open(file_path, 'w', encoding='utf-8') as f:
                    json.dump(standardized_dict, f, indent=2, ensure_ascii=False)

            except Exception as e:
                logger.error(f"Failed to normalize and store result: {e}")
                return

            # Update index
            with self._lock:
                file_name = result.get('file_path', '')
                function_name = result.get('function', '')
                checksum = result.get('checksum', '')

                if file_name and function_name and checksum:
                    key = self._make_key(file_name, function_name, checksum)
                    self._result_index[key] = file_path

            logger.debug(f"Stored result to {filename}")

        except Exception as e:
            # Log error but don't raise to avoid breaking the publisher
            logger.error(f"Error writing result to file: {e}")

    def on_result_updated(self, result_id: str, old_result: Dict[str, Any], new_result: Dict[str, Any]) -> None:
        """
        Called when an existing result is updated.
        Updates the corresponding JSON file and index.

        Args:
            result_id: Unique identifier for the result
            old_result: The previous result data
            new_result: The updated result data
        """
        try:
            # Remove old file and index entry if it exists
            repo_name = self.current_repo_name or self._extract_repo_name(old_result)
            analysis_dir = self._get_analysis_dir(repo_name)
            old_filename = self._generate_filename(old_result)
            old_file_path = os.path.join(analysis_dir, old_filename)

            if os.path.exists(old_file_path):
                os.remove(old_file_path)

            # Remove from index
            with self._lock:
                old_file_name = old_result.get('file_path', '')
                old_function_name = old_result.get('function', '')
                old_checksum = old_result.get('checksum', '')

                if old_file_name and old_function_name and old_checksum:
                    old_key = self._make_key(old_file_name, old_function_name, old_checksum)
                    self._result_index.pop(old_key, None)

            # Write new file
            self.on_result_added(result_id, new_result)

        except Exception as e:
            logger.error(f"Error updating result file: {e}")


    def on_function_analyzed(self, function_name: str, file_path: str, result: Dict[str, Any]) -> None:
        """
        Called when a function analysis is completed.
        This is handled by on_result_added.

        Args:
            function_name: Name of the analyzed function
            file_path: Path to the file containing the function
            result: Analysis result data
        """
        # Generate a unique result ID for this function analysis
        result_id = f"{function_name}_{file_path}_{result.get('checksum', 'unknown')}"
        self.on_result_added(result_id, result)

    def on_analysis_batch_completed(self, batch_results: List[Dict[str, Any]]) -> None:
        """
        Called when a batch of analyses is completed.
        Writes all results in the batch.

        Args:
            batch_results: List of analysis results in the batch
        """
        for i, result in enumerate(batch_results):
            result_id = f"batch_result_{i}"
            self.on_result_added(result_id, result)

    def _extract_repo_name(self, result: Dict[str, Any]) -> str:
        """
        Extract repository name from result data.

        Args:
            result: The result data

        Returns:
            Repository name or default
        """
        # Try to extract repo name from file path
        file_path = result.get('file_path', '')
        if file_path:
            # If file_path is absolute, extract the repo name from it
            if os.path.isabs(file_path):
                # Extract repo name from absolute path
                path_parts = file_path.split(os.sep)
                if len(path_parts) > 1:
                    return path_parts[-2] if path_parts[-1] else path_parts[-3]

        # Default repo name if we can't extract it
        return "default_repo"