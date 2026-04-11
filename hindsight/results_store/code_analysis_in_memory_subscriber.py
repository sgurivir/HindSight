#!/usr/bin/env python3
"""
In-memory Code Analysis Results Subscriber
Implements in-memory result handling for code analysis results
"""

from typing import Any, Dict, List
from threading import Lock
from hindsight.results_store.interface.code_analysis_result_store_interface import CodeAnalysisSubscriber


class CodeAnalysisInMemoryResultsSubscriber(CodeAnalysisSubscriber):
    """
    In-memory subscriber for code analysis results.
    Stores all results in memory for immediate access by the calling code.
    """

    def __init__(self):
        """Initialize the in-memory subscriber."""
        self._results: List[Dict[str, Any]] = []
        self._function_results: List[Dict[str, Any]] = []
        self._batch_results: List[List[Dict[str, Any]]] = []
        self._lock = Lock()

    def on_result_added(self, result_id: str, result: Dict[str, Any]) -> None:
        """
        Called when a new result is added to the store.

        Args:
            result_id: Unique identifier for the result
            result: The result data that was added
        """
        with self._lock:
            # Store the complete result with its ID
            result_with_id = result.copy()
            result_with_id['result_id'] = result_id
            self._results.append(result_with_id)

    def on_result_updated(self, result_id: str, old_result: Dict[str, Any], new_result: Dict[str, Any]) -> None:
        """
        Called when an existing result is updated.

        Args:
            result_id: Unique identifier for the result
            old_result: The previous result data
            new_result: The updated result data
        """
        with self._lock:
            # Find and update the result
            for i, result in enumerate(self._results):
                if result.get('result_id') == result_id:
                    updated_result = new_result.copy()
                    updated_result['result_id'] = result_id
                    self._results[i] = updated_result
                    break


    def on_function_analyzed(self, function_name: str, file_path: str, result: Dict[str, Any]) -> None:
        """
        Called when a function analysis is completed.

        Args:
            function_name: Name of the analyzed function
            file_path: Path to the file containing the function
            result: Analysis result data
        """
        with self._lock:
            # Store function-specific result
            function_result = {
                'function_name': function_name,
                'file_path': file_path,
                'result': result,
                'type': 'function_analysis'
            }
            self._function_results.append(function_result)

    def on_analysis_batch_completed(self, batch_results: List[Dict[str, Any]]) -> None:
        """
        Called when a batch of analyses is completed.

        Args:
            batch_results: List of analysis results in the batch
        """
        with self._lock:
            # Store the batch results
            self._batch_results.append(batch_results.copy())

    def get_all_results(self) -> List[Dict[str, Any]]:
        """
        Get all stored results.

        Returns:
            List of all results stored in memory
        """
        with self._lock:
            return self._results.copy()

    def get_function_results(self) -> List[Dict[str, Any]]:
        """
        Get all function analysis results.

        Returns:
            List of all function analysis results
        """
        with self._lock:
            return self._function_results.copy()

    def get_batch_results(self) -> List[List[Dict[str, Any]]]:
        """
        Get all batch results.

        Returns:
            List of all batch results
        """
        with self._lock:
            return self._batch_results.copy()

    def get_results_count(self) -> int:
        """
        Get the total number of results stored.

        Returns:
            Number of results stored in memory
        """
        with self._lock:
            return len(self._results)

    def get_function_results_count(self) -> int:
        """
        Get the total number of function results stored.

        Returns:
            Number of function results stored in memory
        """
        with self._lock:
            return len(self._function_results)

    def clear_results(self) -> None:
        """Clear all stored results."""
        with self._lock:
            self._results.clear()
            self._function_results.clear()
            self._batch_results.clear()


    def get_results_by_function(self, function_name: str) -> List[Dict[str, Any]]:
        """
        Get all results for a specific function.

        Args:
            function_name: Name of the function

        Returns:
            List of results for the specified function
        """
        with self._lock:
            function_results = []

            # Check main results
            for result in self._results:
                if result.get('function') == function_name:
                    function_results.append(result)

            # Check function results
            for func_result in self._function_results:
                if func_result.get('function_name') == function_name:
                    function_results.append(func_result)

            return function_results