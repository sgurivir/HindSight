#!/usr/bin/env python3
"""
Prior Results Store Interface
Interface for entities that are both subscribers and result stores
"""

from typing import Any, Dict, Optional
from abc import ABC, abstractmethod
from .code_analysis_result_store_interface import CodeAnalysisSubscriber


class ResultsCache(CodeAnalysisSubscriber, ABC):
    """
    Interface for entities that are both subscribers and result stores.
    Can lookup existing results and store new ones.
    """

    @abstractmethod
    def has_result(self, file_name: str, function_name: str, checksum: str, timeout_seconds: float = 15.0) -> bool:
        """
        Check if a result already exists for the given parameters.

        Args:
            file_name: Name of the file
            function_name: Name of the function
            checksum: Function checksum
            timeout_seconds: Maximum time to wait for lookup (default 15 seconds)

        Returns:
            True if result exists, False otherwise or on timeout
        """
        pass

    @abstractmethod
    def get_existing_result(self, file_name: str, function_name: str, checksum: str, timeout_seconds: float = 15.0) -> Optional[Dict[str, Any]]:
        """
        Get existing result if it exists.

        Args:
            file_name: Name of the file
            function_name: Name of the function
            checksum: Function checksum
            timeout_seconds: Maximum time to wait for lookup (default 15 seconds)

        Returns:
            Existing result or None if not found or on timeout
        """
        pass

    @abstractmethod
    def initialize_for_repo(self, repo_name: str) -> None:
        """
        Initialize the store for a specific repository.

        Args:
            repo_name: Name of the repository
        """
        pass