#!/usr/bin/env python3
"""
Code Analysis Results Local File System Subscriber
Concrete implementation of subscriber that writes code analysis results to JSON files on local filesystem
"""

import os
import json
import threading
from typing import Any, Dict, List, Optional
from .interface.code_analysis_result_store_interface import CodeAnalysisSubscriber


class CodeAnalysysResultsLocalFSSubscriber(CodeAnalysisSubscriber):
    """
    Concrete subscriber that writes code analysis results to JSON files on local filesystem.
    Maintains the exact same file structure and naming as the current implementation.
    """

    def __init__(self, base_output_dir: str):
        """
        Initialize the file subscriber

        Args:
            base_output_dir: Base directory for output (e.g., ~/hindsight_artifacts)
        """
        self.base_output_dir = base_output_dir
        self._repo_dirs: Dict[str, str] = {}  # repo_name -> analysis_dir mapping
        self._lock = threading.RLock()  # Reentrant lock for thread safety

    def initialize_repo(self, repo_name: str) -> None:
        """
        Initialize the output directory for a specific repository

        Args:
            repo_name: Name of the repository
        """
        with self._lock:
            # Create the directory structure: base_output_dir/repo_name/results/code_analysis
            repo_artifacts_dir = os.path.join(self.base_output_dir, repo_name)
            analysis_dir = os.path.join(repo_artifacts_dir, "results", "code_analysis")
            
            os.makedirs(analysis_dir, exist_ok=True)
            self._repo_dirs[repo_name] = analysis_dir

    def get_analysis_dir(self, repo_name: str) -> str:
        """
        Get the analysis directory for a repository

        Args:
            repo_name: Name of the repository

        Returns:
            Path to the analysis directory
        """
        with self._lock:
            if repo_name not in self._repo_dirs:
                self.initialize_repo(repo_name)
            return self._repo_dirs[repo_name]

    def set_category_filter(self, category_filter) -> None:
        """
        Set the category filter to apply when loading existing results.
        
        Args:
            category_filter: CategoryBasedFilter instance for Level 1 filtering
        """
        self._category_filter = category_filter

    def load_existing_results(self, repo_name: str, publisher, category_filter=None) -> int:
        """
        Load existing analysis results from files and index them in the publisher for cache lookups.
        This enables checksum-based caching and avoids re-analyzing unchanged functions.
        
        NOTE: This method only builds the index for cache lookups. It does NOT add results
        to the publisher's results collection. Results are added during the analysis loop
        when cached results are "republished" - this prevents duplicate issues in reports.

        Args:
            repo_name: Name of the repository
            publisher: The publisher instance to index results in (for cache lookups only)
            category_filter: Optional CategoryBasedFilter (not used - filtering happens during republish)

        Returns:
            Number of results indexed for caching
        """
        with self._lock:
            analysis_dir = self.get_analysis_dir(repo_name)

            if not os.path.exists(analysis_dir):
                return 0

            indexed_count = 0

            # Find all analysis JSON files and index them for cache lookups
            for filename in os.listdir(analysis_dir):
                if filename.endswith('_analysis.json'):
                    file_path = os.path.join(analysis_dir, filename)

                    try:
                        with open(file_path, 'r', encoding='utf-8') as f:
                            result_data = json.load(f)

                        # Extract the required fields for indexing
                        file_path_field = result_data.get('file_path', '')
                        function_name = result_data.get('function', '')
                        function_checksum = result_data.get('checksum', '')

                        if file_path_field and function_name and function_checksum:
                            # Only index the result for cache lookups - do NOT add to publisher's results
                            # This prevents duplicate issues when cached results are republished later
                            publisher.index_existing_result(
                                file_path=file_path_field,
                                function=function_name,
                                function_checksum=function_checksum,
                                result_data=result_data
                            )
                            indexed_count += 1

                    except Exception as e:
                        print(f"Error indexing existing result from {file_path}: {e}")
                        continue

            return indexed_count

    def load_existing_results_for_report(self, repo_name: str, publisher) -> int:
        """
        Load existing analysis results from files directly into the publisher's results collection.
        This is used by --generate-report-from-existing-issues to generate reports without
        running the analysis loop.
        
        Unlike load_existing_results(), this method DOES add results to the publisher's
        results collection, making them available via get_results() for report generation.

        Args:
            repo_name: Name of the repository
            publisher: The publisher instance to load results into

        Returns:
            Number of results loaded for report generation
        """
        with self._lock:
            analysis_dir = self.get_analysis_dir(repo_name)

            if not os.path.exists(analysis_dir):
                return 0

            loaded_count = 0

            # Find all analysis JSON files and load them into the publisher
            for filename in os.listdir(analysis_dir):
                if filename.endswith('_analysis.json'):
                    file_path = os.path.join(analysis_dir, filename)

                    try:
                        with open(file_path, 'r', encoding='utf-8') as f:
                            result_data = json.load(f)

                        # Extract the required fields
                        file_path_field = result_data.get('file_path', '')
                        function_name = result_data.get('function', '')
                        function_checksum = result_data.get('checksum', '')

                        if file_path_field and function_name and function_checksum:
                            # Load the result directly into the publisher's results collection
                            result_id = publisher.load_existing_result_for_report(
                                repo_name=repo_name,
                                file_path=file_path_field,
                                function=function_name,
                                function_checksum=function_checksum,
                                result_data=result_data
                            )
                            if result_id:
                                loaded_count += 1

                    except Exception as e:
                        print(f"Error loading existing result from {file_path}: {e}")
                        continue

            return loaded_count

    def on_result_added(self, result_id: str, result: Dict[str, Any]) -> None:
        """
        Called when a new result is added to the store.
        Writes the result to a JSON file using the same naming convention as current implementation.

        Args:
            result_id: Unique identifier for the result
            result: The result data that was added
        """
        try:
            # Extract repository name from the result or use current repo name
            repo_name = self._extract_repo_name_with_fallback(result)
            
            # Check if this is a diff analysis result and use appropriate subdirectory
            file_path_field = result.get('file_path', '')
            if file_path_field == 'diff_analysis':
                # This is a diff analysis result - use results/diff_analysis subdirectory
                repo_artifacts_dir = os.path.join(self.base_output_dir, repo_name)
                analysis_dir = os.path.join(repo_artifacts_dir, "results", "diff_analysis")
                os.makedirs(analysis_dir, exist_ok=True)
            else:
                # Regular code analysis result - use existing logic
                analysis_dir = self.get_analysis_dir(repo_name)

            # Generate filename using the same logic as current implementation
            filename = self._generate_filename(result)
            file_path = os.path.join(analysis_dir, filename)

            # Write the result to JSON file with the same format as current implementation
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(result, f, indent=2, ensure_ascii=False)

        except Exception as e:
            # Log error but don't raise to avoid breaking the publisher
            print(f"Error writing result to file: {e}")

    def on_result_updated(self, result_id: str, old_result: Dict[str, Any], new_result: Dict[str, Any]) -> None:
        """
        Called when an existing result is updated.
        Updates the corresponding JSON file.

        Args:
            result_id: Unique identifier for the result
            old_result: The previous result data
            new_result: The updated result data
        """
        try:
            # Remove old file if it exists
            repo_name = self._extract_repo_name_with_fallback(old_result)
            analysis_dir = self.get_analysis_dir(repo_name)
            old_filename = self._generate_filename(old_result)
            old_file_path = os.path.join(analysis_dir, old_filename)

            if os.path.exists(old_file_path):
                os.remove(old_file_path)

            # Write new file
            self.on_result_added(result_id, new_result)

        except Exception as e:
            print(f"Error updating result file: {e}")


    def on_function_analyzed(self, function_name: str, file_path: str, result: Dict[str, Any]) -> None:
        """
        Called when a function analysis is completed.
        This is handled by on_result_added, so we can use that.

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
            # Otherwise, use a default repo name
            if os.path.isabs(file_path):
                # Extract repo name from absolute path
                path_parts = file_path.split(os.sep)
                if len(path_parts) > 1:
                    return path_parts[-2] if path_parts[-1] else path_parts[-3]

        # Default repo name if we can't extract it
        return "default_repo"

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

    def set_repo_name(self, repo_name: str) -> None:
        """
        Set the repository name for this subscriber instance.
        This allows the subscriber to know which repository it's handling.

        Args:
            repo_name: Name of the repository
        """
        self.current_repo_name = repo_name
        self.initialize_repo(repo_name)

    def _extract_repo_name_with_fallback(self, result: Dict[str, Any]) -> str:
        """
        Extract repository name with fallback to current repo name.

        Args:
            result: The result data

        Returns:
            Repository name
        """
        # Use current repo name if set
        if hasattr(self, 'current_repo_name'):
            return self.current_repo_name

        # Otherwise try to extract from result
        return self._extract_repo_name(result)