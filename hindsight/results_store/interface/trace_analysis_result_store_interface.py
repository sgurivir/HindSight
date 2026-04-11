#!/usr/bin/env python3
"""
Trace Analysis Result Store Interface
Specialized publisher-subscriber interface for trace analysis results
"""

from typing import Any, Dict, List, Optional
from abc import ABC, abstractmethod
from .base_result_store_interface import BaseResultsCacheInterface, ResultSubscriber


class TraceAnalysisSubscriber(ResultSubscriber):
    """Specialized subscriber interface for trace analysis results"""

    @abstractmethod
    def on_trace_analyzed(self, trace_id: str, callstack: List[str], result: Dict[str, Any]) -> None:
        """
        Called when a trace analysis is completed

        Args:
            trace_id: Unique identifier for the trace
            callstack: List of function calls in the trace
            result: Analysis result data
        """
        pass

    @abstractmethod
    def on_callstack_pattern_detected(self, pattern: str, traces: List[Dict[str, Any]]) -> None:
        """
        Called when a recurring callstack pattern is detected

        Args:
            pattern: Description of the detected pattern
            traces: List of traces that match the pattern
        """
        pass

    @abstractmethod
    def on_trace_batch_completed(self, batch_results: List[Dict[str, Any]]) -> None:
        """
        Called when a batch of trace analyses is completed

        Args:
            batch_results: List of trace analysis results in the batch
        """
        pass


class TraceAnalysisResultStoreInterface(BaseResultsCacheInterface):
    """
    Publisher-subscriber result store interface specifically for trace analysis results
    """

    @abstractmethod
    def get_results_by_trace_id(self, repo_name: str, trace_id: str) -> Optional[Dict[str, Any]]:
        """
        Get result for a specific trace ID

        Args:
            repo_name: Name of the repository
            trace_id: Unique identifier for the trace

        Returns:
            Trace analysis result or None if not found
        """
        pass

    @abstractmethod
    def get_results_by_function(self, repo_name: str, function_name: str) -> List[Dict[str, Any]]:
        """
        Get all traces that include a specific function

        Args:
            repo_name: Name of the repository
            function_name: Name of the function

        Returns:
            List of trace results containing the function
        """
        pass

    @abstractmethod
    def get_traces_with_issues(self, repo_name: str) -> List[Dict[str, Any]]:
        """
        Get all traces that have detected issues or anomalies

        Args:
            repo_name: Name of the repository

        Returns:
            List of trace results with issues
        """
        pass
