#!/usr/bin/env python3
"""
Diff Analysis Result Store Interface
Specialized publisher-subscriber interface for diff analysis results
"""

from typing import Any, Dict, List, Optional
from abc import ABC, abstractmethod
from .base_result_store_interface import BaseResultsCacheInterface, ResultSubscriber


class DiffAnalysisSubscriber(ResultSubscriber):
    """Specialized subscriber interface for diff analysis results"""

    @abstractmethod
    def on_diff_analyzed(self, repo_name: str, old_commit: str, new_commit: str, result: Dict[str, Any]) -> None:
        """
        Called when a diff analysis is completed

        Args:
            repo_name: Name of the repository
            old_commit: Old commit hash
            new_commit: New commit hash
            result: Analysis result data
        """
        pass

    @abstractmethod
    def on_diff_batch_completed(self, batch_results: List[Dict[str, Any]]) -> None:
        """
        Called when a batch of diff analyses is completed

        Args:
            batch_results: List of diff analysis results in the batch
        """
        pass


class DiffAnalysisResultCacheInterface(BaseResultsCacheInterface):
    """
    Publisher-subscriber result store interface specifically for diff analysis results
    """

    @abstractmethod
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
        pass

    @abstractmethod
    def get_all_diff_results(self, repo_name: str) -> List[Dict[str, Any]]:
        """
        Get all diff analysis results for a specific repository

        Args:
            repo_name: Name of the repository

        Returns:
            List of all diff analysis results for the repository
        """
        pass


