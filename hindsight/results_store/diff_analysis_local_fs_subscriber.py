#!/usr/bin/env python3
"""
Diff Analysis Results Local File System Subscriber
Concrete implementation of subscriber that writes diff analysis results to JSON files on local filesystem
"""

import os
import json
import threading
from typing import Any, Dict, List
from datetime import datetime
from .interface.diff_analysis_result_store_interface import DiffAnalysisSubscriber


class DiffAnalysisLocalFSSubscriber(DiffAnalysisSubscriber):
    """
    Concrete subscriber that writes diff analysis results to JSON files on local filesystem.
    Stores results in results/diff_analysis/ subdirectory by default.
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
            # Create the directory structure: base_output_dir/repo_name/results/diff_analysis
            repo_artifacts_dir = os.path.join(self.base_output_dir, repo_name)
            analysis_dir = os.path.join(repo_artifacts_dir, "results", "diff_analysis")

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

    def load_existing_results(self, repo_name: str, publisher) -> int:
        """
        Load existing diff analysis results from files into the publisher.
        This enables caching and avoids re-analyzing the same commit ranges.

        Args:
            repo_name: Name of the repository
            publisher: The publisher instance to load results into

        Returns:
            Number of results loaded
        """
        with self._lock:
            analysis_dir = self.get_analysis_dir(repo_name)

            if not os.path.exists(analysis_dir):
                return 0

            loaded_count = 0

            # Find all diff analysis JSON files
            for filename in os.listdir(analysis_dir):
                if filename.endswith('_diff_analysis.json'):
                    file_path = os.path.join(analysis_dir, filename)

                    try:
                        with open(file_path, 'r', encoding='utf-8') as f:
                            result_data = json.load(f)

                        # Extract the required fields for publisher
                        analysis_info = result_data.get('analysis_info', {})
                        old_commit = analysis_info.get('old_commit', '')
                        new_commit = analysis_info.get('new_commit', '')
                        changed_files = analysis_info.get('changed_files', [])
                        issues = result_data.get('issues', [])

                        if old_commit and new_commit:
                            # Add to publisher using the same method as during analysis
                            publisher.add_diff_result(
                                repo_name=repo_name,
                                old_commit=old_commit,
                                new_commit=new_commit,
                                changed_files=changed_files,
                                issues=issues
                            )
                            loaded_count += 1

                    except Exception as e:
                        print(f"Error loading existing diff result from {file_path}: {e}")
                        continue

            return loaded_count

    def on_result_added(self, result_id: str, result: Dict[str, Any]) -> None:
        """
        Called when a new result is added to the store.
        Writes the result to a JSON file using timestamp-based naming.

        Args:
            result_id: Unique identifier for the result
            result: The result data that was added
        """
        try:
            # Extract repository name from the result
            repo_name = self._extract_repo_name_with_fallback(result)
            analysis_dir = self.get_analysis_dir(repo_name)

            # Generate filename using timestamp and commit info
            filename = self._generate_filename(result)
            file_path = os.path.join(analysis_dir, filename)

            # Write the result to JSON file
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(result, f, indent=2, ensure_ascii=False)

        except Exception as e:
            # Log error but don't raise to avoid breaking the publisher
            print(f"Error writing diff analysis result to file: {e}")

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
            print(f"Error updating diff analysis result file: {e}")

    def on_diff_analyzed(self, repo_name: str, old_commit: str, new_commit: str, result: Dict[str, Any]) -> None:
        """
        Called when a diff analysis is completed.
        This is handled by on_result_added, so we can use that.

        Args:
            repo_name: Name of the repository
            old_commit: Old commit hash
            new_commit: New commit hash
            result: Analysis result data
        """
        # Generate a unique result ID for this diff analysis
        result_id = f"{repo_name}_{old_commit[:8]}_{new_commit[:8]}"
        self.on_result_added(result_id, result)

    def on_diff_batch_completed(self, batch_results: List[Dict[str, Any]]) -> None:
        """
        Called when a batch of diff analyses is completed.
        Writes all results in the batch.

        Args:
            batch_results: List of diff analysis results in the batch
        """
        for i, result in enumerate(batch_results):
            result_id = f"batch_diff_result_{i}"
            self.on_result_added(result_id, result)

    def _extract_repo_name(self, result: Dict[str, Any]) -> str:
        """
        Extract repository name from result data.

        Args:
            result: The result data

        Returns:
            Repository name or default
        """
        # Try to extract repo name from analysis_info
        analysis_info = result.get('analysis_info', {})
        repo_name = analysis_info.get('repo_name', '')
        
        if repo_name:
            return repo_name

        # Default repo name if we can't extract it
        return "default_repo"

    def _generate_filename(self, result: Dict[str, Any]) -> str:
        """
        Generate filename using timestamp and commit information.

        Args:
            result: The result data

        Returns:
            Generated filename
        """
        analysis_info = result.get('analysis_info', {})
        old_commit = analysis_info.get('old_commit', 'unknown')[:8]
        new_commit = analysis_info.get('new_commit', 'unknown')[:8]
        
        # Use timestamp from analysis_info or current time
        timestamp_str = analysis_info.get('analysis_timestamp', '')
        if timestamp_str:
            try:
                # Parse ISO timestamp and convert to filename-safe format
                timestamp = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
                timestamp_formatted = timestamp.strftime('%Y%m%d_%H%M%S')
            except:
                timestamp_formatted = datetime.now().strftime('%Y%m%d_%H%M%S')
        else:
            timestamp_formatted = datetime.now().strftime('%Y%m%d_%H%M%S')

        # Generate filename: diff_analysis_oldcommit_newcommit_timestamp.json
        filename = f"diff_analysis_{old_commit}_{new_commit}_{timestamp_formatted}.json"

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