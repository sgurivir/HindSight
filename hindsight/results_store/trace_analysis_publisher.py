#!/usr/bin/env python3
"""
Trace Analysis Results Publisher
Concrete implementation of publisher for trace analysis results
"""

import os
import json
import uuid
import threading
import concurrent.futures
from typing import Any, Dict, List, Optional
from .interface.trace_analysis_result_store_interface import TraceAnalysisResultStoreInterface
from .interface.prior_results_store_interface import ResultsCache
from hindsight.utils.log_util import get_logger

logger = get_logger(__name__)


class TraceAnalysisResultsPublisher(TraceAnalysisResultStoreInterface):
    """
    Concrete publisher for trace analysis results.
    Manages in-memory storage and notifies subscribers of result changes.
    """

    def __init__(self):
        super().__init__()
        self._results: Dict[str, Dict[str, Any]] = {}  # result_id -> result_data
        self._repo_results: Dict[str, List[str]] = {}  # repo_name -> list of result_ids
        self._prior_result_stores: List[ResultsCache] = []  # Prior result stores for lookup
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
        if (repo_name in self._repo_results and
            result_id in self._repo_results[repo_name] and
            result_id in self._results):

            old_result = self._results[result_id].copy()
            self._results[result_id] = updated_result.copy()

            # Notify subscribers
            self._notify_result_updated(result_id, old_result, updated_result)

            return True
        return False


    # Trace-specific methods

    def add_trace_result(self, repo_name: str, trace_id: str, callstack: List[str], result: Dict[str, Any]) -> str:
        """
        Add a trace analysis result for a specific trace

        Args:
            repo_name: Name of the repository being analyzed
            trace_id: Unique identifier for the trace
            callstack: List of function calls in the trace
            result: Analysis result data for the trace

        Returns:
            Unique identifier for the stored result
        """
        with self._lock:
            # Create the enhanced result structure matching the trace analysis format
            enhanced_result = result.copy()
            enhanced_result.update({
                "trace_id": trace_id,
                "callstack": callstack,
                "repo_name": repo_name
            })

            return self.publish_result(repo_name, enhanced_result)

    def register_prior_result_store(self, store: ResultsCache) -> None:
        """
        Register a prior result store for lookup before analysis.

        Args:
            store: The prior result store to register
        """
        with self._lock:
            self._prior_result_stores.append(store)
            self.subscribe(store)  # Also register as subscriber
            logger.info(f"Registered prior result store: {type(store).__name__}")

    def check_existing_trace(self, trace_id: str, callstack: List[str]) -> Optional[Dict[str, Any]]:
        """
        Check all registered stores for existing trace with timeout.
        First successful lookup wins and stops other lookups.

        Args:
            trace_id: Unique identifier for the trace
            callstack: List of function calls in the trace

        Returns:
            Existing trace result if found in any store within timeout, None otherwise
        """
        if not self._prior_result_stores:
            return None

        # For trace analysis, we use trace_id as the "function_name" and callstack as "file_name"
        callstack_str = "->".join(callstack) if isinstance(callstack, list) else str(callstack)

        logger.debug(f"Checking {len(self._prior_result_stores)} prior result stores for trace {trace_id}")

        # Use ThreadPoolExecutor to query all stores concurrently
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(self._prior_result_stores)) as executor:
            # Submit all lookup tasks
            future_to_store = {
                executor.submit(self._lookup_trace_with_timeout, store, callstack_str, trace_id, ""): store
                for store in self._prior_result_stores
            }

            try:
                # Wait for first successful result with overall timeout of 15 seconds
                for future in concurrent.futures.as_completed(future_to_store, timeout=15.0):
                    try:
                        result = future.result()
                        if result is not None:
                            # First successful lookup wins - cancel remaining tasks
                            for remaining_future in future_to_store:
                                if remaining_future != future:
                                    remaining_future.cancel()

                            store = future_to_store[future]
                            logger.info(f"Found existing trace in store {type(store).__name__} for {trace_id}")
                            return result
                    except Exception as e:
                        store = future_to_store[future]
                        logger.warning(f"Error checking trace in store {type(store).__name__}: {e}")
                        continue

            except concurrent.futures.TimeoutError:
                logger.warning(f"Timeout (15s) checking for existing trace: {trace_id}")
                # Cancel all remaining tasks
                for future in future_to_store:
                    future.cancel()

        return None

    def _lookup_trace_with_timeout(self, store: ResultsCache, callstack_str: str, trace_id: str, checksum: str) -> Optional[Dict[str, Any]]:
        """
        Perform trace lookup in a single store with individual timeout.

        Args:
            store: The store to lookup in
            callstack_str: Callstack as string (used as file_name)
            trace_id: Trace ID (used as function_name)
            checksum: Checksum (empty for traces)

        Returns:
            Result if found, None otherwise or on timeout
        """
        try:
            # Each store gets 15 second timeout
            # For traces, we use callstack as "file_name" and trace_id as "function_name"
            if store.has_result(callstack_str, trace_id, checksum or "trace", timeout_seconds=15.0):
                return store.get_existing_result(callstack_str, trace_id, checksum or "trace", timeout_seconds=15.0)
        except Exception as e:
            logger.warning(f"Error in trace store lookup for {type(store).__name__}: {e}")

        return None

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

    def _get_all_results(self, repo_name: str) -> List[Dict[str, Any]]:
        """
        Internal helper method to get all results for a repository

        Args:
            repo_name: Name of the repository

        Returns:
            List of all results for the repository
        """
        return self.get_results(repo_name)

    def get_results_by_trace_id(self, repo_name: str, trace_id: str) -> Optional[Dict[str, Any]]:
        """
        Get result for a specific trace ID

        Args:
            repo_name: Name of the repository
            trace_id: Unique identifier for the trace

        Returns:
            Trace analysis result or None if not found
        """
        with self._lock:
            if repo_name not in self._repo_results:
                return None

            for result_id in self._repo_results[repo_name]:
                if result_id in self._results:
                    result = self._results[result_id]
                    if result.get("trace_id") == trace_id:
                        return result.copy()
            return None

    def get_results_by_function(self, repo_name: str, function_name: str) -> List[Dict[str, Any]]:
        """
        Get all traces that include a specific function

        Args:
            repo_name: Name of the repository
            function_name: Name of the function

        Returns:
            List of trace results containing the function
        """
        results = []
        for result in self._get_all_results(repo_name):
            callstack = result.get("callstack", [])
            if isinstance(callstack, list):
                if any(function_name in str(frame) for frame in callstack):
                    results.append(result)
            elif isinstance(callstack, str):
                if function_name in callstack:
                    results.append(result)
        return results



    def get_traces_with_issues(self, repo_name: str) -> List[Dict[str, Any]]:
        """
        Get all traces that have detected issues or anomalies

        Args:
            repo_name: Name of the repository

        Returns:
            List of trace results with issues
        """
        results = []
        for result in self._get_all_results(repo_name):
            issues = result.get("issues", [])
            if isinstance(issues, list) and len(issues) > 0:
                results.append(result)
        return results


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
            if key not in result or result[key] != value:
                return False
        return True