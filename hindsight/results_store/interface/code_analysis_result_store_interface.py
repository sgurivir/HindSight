#!/usr/bin/env python3
"""
Code Analysis Result Store Interface
Specialized publisher-subscriber interface for code analysis results
"""

from typing import Any, Dict, List, Optional
from abc import ABC, abstractmethod
from .base_result_store_interface import BaseResultsCacheInterface, ResultSubscriber


class CodeAnalysisSubscriber(ResultSubscriber):
    """Specialized subscriber interface for code analysis results"""

    @abstractmethod
    def on_function_analyzed(self, function_name: str, file_path: str, result: Dict[str, Any]) -> None:
        """
        Called when a function analysis is completed

        Args:
            function_name: Name of the analyzed function
            file_path: Path to the file containing the function
            result: Analysis result data
        """
        pass

    @abstractmethod
    def on_analysis_batch_completed(self, batch_results: List[Dict[str, Any]]) -> None:
        """
        Called when a batch of analyses is completed

        Args:
            batch_results: List of analysis results in the batch
        """
        pass


class CodeAnalysisResultCacheInterface(BaseResultsCacheInterface):
    """
    Publisher-subscriber result store interface specifically for code analysis results
    """

    @abstractmethod
    def add_result(self, repo_name: str, file_path: str, function: str, function_checksum: str, results: List[Dict[str, Any]]) -> str:
        """
        Add a code analysis result for a specific function

        Args:
            repo_name: Name of the repository being analyzed
            file_path: Path to the file containing the function
            function: Name of the analyzed function
            function_checksum: Checksum of the function content
            results: List of analysis results/issues for the function

        Returns:
            Unique identifier for the stored result
        """
        pass

    @abstractmethod
    def get_all_results(self, repo_name: str) -> List[Dict[str, Any]]:
        """
        Get all results for a specific repository

        Args:
            repo_name: Name of the repository

        Returns:
            List of all results for the repository
        """
        pass


    @abstractmethod
    def get_results_by_function(self, repo_name: str, function_name: str, checksum: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Get all results for a specific function

        Args:
            repo_name: Name of the repository
            function_name: Name of the function
            checksum: Optional function checksum to filter by specific version

        Returns:
            List of results for the function (optionally filtered by checksum)
        """
        pass
