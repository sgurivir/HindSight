#!/usr/bin/env python3
"""
Diff Analysis Results Publisher
Concrete implementation of publisher for diff analysis results
"""

import os
import json
import uuid
import threading
from typing import Any, Dict, List, Optional
from datetime import datetime
from .interface.diff_analysis_result_store_interface import DiffAnalysisResultCacheInterface
from hindsight.utils.log_util import get_logger

logger = get_logger(__name__)


class DiffAnalysisResultsPublisher(DiffAnalysisResultCacheInterface):
    """
    Concrete publisher for diff analysis results.
    Manages in-memory storage and notifies subscribers of result changes.
    """

    def __init__(self):
        super().__init__()
        self._results: Dict[str, Dict[str, Any]] = {}  # result_id -> result_data
        self._repo_results: Dict[str, List[str]] = {}  # repo_name -> list of result_ids
        self._storage_location: Optional[str] = None
        self._lock = threading.RLock()  # Reentrant lock for thread safety

    def initialize(self, location: str) -> None:
        """
        Initialize the publisher with a storage location

        Args:
            location: Base path for storing results
        """
        self._storage_location = location
        os.makedirs(location, exist_ok=True)

    def publish_result(self, repo_name: str, result: Dict[str, Any]) -> str:
        """
        Publish a new result to the store for a specific repository

        Args:
            repo_name: Name of the repository
            result: The result data to store

        Returns:
            Unique identifier for the stored result
        """
        with self._lock:
            result_id = str(uuid.uuid4())

            # Store the result
            self._results[result_id] = result.copy()

            # Track by repository
            if repo_name not in self._repo_results:
                self._repo_results[repo_name] = []
            self._repo_results[repo_name].append(result_id)

            # Notify subscribers
            self._notify_result_added(result_id, result)

            return result_id

    def add_diff_result(self, repo_name: str, old_commit: str, new_commit: str, 
                       changed_files: List[str], issues: List[Dict[str, Any]]) -> str:
        """
        Add a diff analysis result for a specific commit comparison

        Args:
            repo_name: Name of the repository being analyzed
            old_commit: Old commit hash
            new_commit: New commit hash
            changed_files: List of files that changed between commits
            issues: List of analysis issues found in the diff

        Returns:
            Unique identifier for the stored result
        """
        # Create standardized result structure
        result = {
            "analysis_info": {
                "repo_name": repo_name,
                "old_commit": old_commit,
                "new_commit": new_commit,
                "changed_files": changed_files,
                "analysis_timestamp": datetime.now().isoformat(),
                "total_issues": len(issues)
            },
            "issues": issues
        }

        return self.publish_result(repo_name, result)

    def get_results(self, repo_name: str, filters: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """
        Get results from the store for a specific repository, optionally filtered

        Args:
            repo_name: Name of the repository
            filters: Optional dictionary of filter criteria

        Returns:
            List of results matching the filters for the repository
        """
        with self._lock:
            if repo_name not in self._repo_results:
                return []

            results = []
            for result_id in self._repo_results[repo_name]:
                if result_id in self._results:
                    result = self._results[result_id]

                    # Apply filters if provided
                    if filters:
                        if not self._matches_filters(result, filters):
                            continue

                    results.append(result.copy())

            return results

    def get_all_diff_results(self, repo_name: str) -> List[Dict[str, Any]]:
        """
        Get all diff analysis results for a specific repository

        Args:
            repo_name: Name of the repository

        Returns:
            List of all diff analysis results for the repository
        """
        return self.get_results(repo_name)

    def update_result(self, repo_name: str, result_id: str, updated_result: Dict[str, Any]) -> bool:
        """
        Update an existing result for a specific repository

        Args:
            repo_name: Name of the repository
            result_id: Unique identifier for the result
            updated_result: The updated result data

        Returns:
            True if the result was updated, False if not found
        """
        with self._lock:
            if (repo_name in self._repo_results and
                result_id in self._repo_results[repo_name] and
                result_id in self._results):

                old_result = self._results[result_id].copy()
                self._results[result_id] = updated_result.copy()

                # Notify subscribers
                self._notify_result_updated(result_id, old_result, updated_result)

                return True
            return False



    def _matches_filters(self, result: Dict[str, Any], filters: Dict[str, Any]) -> bool:
        """
        Check if a result matches the given filters

        Args:
            result: The result to check
            filters: Dictionary of filter criteria

        Returns:
            True if the result matches all filters, False otherwise
        """
        for key, value in filters.items():
            # Support nested key access (e.g., "analysis_info.old_commit")
            if '.' in key:
                keys = key.split('.')
                current = result
                for k in keys:
                    if isinstance(current, dict) and k in current:
                        current = current[k]
                    else:
                        return False
                if current != value:
                    return False
            else:
                if key not in result or result[key] != value:
                    return False
        return True