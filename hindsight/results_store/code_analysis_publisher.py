#!/usr/bin/env python3
"""
Code Analysis Results Publisher
Concrete implementation of publisher for code analysis results
"""

import os
import json
import uuid
import threading
import concurrent.futures
from typing import Any, Dict, List, Optional
from .interface.code_analysis_result_store_interface import CodeAnalysisResultCacheInterface
from .interface.prior_results_store_interface import ResultsCache
from hindsight.utils.log_util import get_logger

# Import centralized schema
from hindsight.core.schema.code_analysis_result_schema import (
    CodeAnalysisResult,
    CodeAnalysisResultValidator,
    create_result
)

logger = get_logger(__name__)


class CodeAnalysisResultsPublisher(CodeAnalysisResultCacheInterface):
    """
    Concrete publisher for code analysis results.
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
        # Issues should already be filtered by analyzers before reaching the publisher
        # No filtering should be done at the publisher level
        results_list = results if isinstance(results, list) else [results]

        # Ensure all issues have required fields before creating the result
        validated_issues = []
        for issue_data in results_list:
            if isinstance(issue_data, dict):
                # Ensure required fields are present with defaults if missing
                if 'issue' not in issue_data or not issue_data['issue']:
                    issue_data['issue'] = 'Code analysis issue'
                if 'severity' not in issue_data or not issue_data['severity']:
                    issue_data['severity'] = 'medium'
                if 'category' not in issue_data or not issue_data['category']:
                    issue_data['category'] = 'general'
                validated_issues.append(issue_data)
            else:
                # Handle non-dict issues by converting to dict with required fields
                validated_issues.append({
                    'issue': str(issue_data) if issue_data else 'Code analysis issue',
                    'severity': 'medium',
                    'category': 'general'
                })

        # Use centralized schema to create standardized result
        try:
            standardized_result = create_result(
                file_path=file_path,
                function=function,
                checksum=function_checksum,
                issues=validated_issues
            )

            # Validate the result
            validation_errors = standardized_result.validate()
            if validation_errors:
                logger.warning(f"Result validation failed for {function}, skipping publication: {validation_errors}")
                return None

            # Convert to dictionary format for storage and notification
            enhanced_result = standardized_result.to_dict()

            # Always publish to cache, but only publish to report subscribers if there are issues
            if not validated_issues:
                logger.info(f"No issues found for {function} in {file_path} - caching result but not publishing to report")
                # Still cache the result for future runs, but don't notify report subscribers
                with self._lock:
                    result_id = str(uuid.uuid4())
                    self._results[result_id] = enhanced_result.copy()

                    # Track by repository
                    if repo_name not in self._repo_results:
                        self._repo_results[repo_name] = []
                    self._repo_results[repo_name].append(result_id)

                    # Only notify cache subscribers (like FileSystemResultsCache), not report subscribers
                    self._notify_result_added(result_id, enhanced_result)

                    return result_id
            else:
                # Publish normally for functions with issues
                return self.publish_result(repo_name, enhanced_result)

        except Exception as e:
            logger.error(f"Failed to create standardized result for {function}: {e}")
            return None

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

    def index_existing_result(self, file_path: str, function: str, function_checksum: str, result_data: Dict[str, Any]) -> None:
        """
        Index an existing result for cache lookups WITHOUT adding it to the results collection.
        This is used during initialization to build the cache index from existing files.
        
        The result will be available for cache lookups via check_existing_result(), but will NOT
        appear in get_results() or be included in reports. Results are only added to the collection
        when they are "republished" during the analysis loop, which prevents duplicate issues.

        Args:
            file_path: Path to the file containing the function
            function: Name of the analyzed function
            function_checksum: Checksum of the function content
            result_data: The full result data (stored for retrieval during cache hits)
        """
        # Store in a separate index for cache lookups only
        # This does NOT add to _results or _repo_results, so it won't appear in reports
        if not hasattr(self, '_cache_index'):
            self._cache_index: Dict[str, Dict[str, Any]] = {}
        
        # Create a lookup key
        key = f"{file_path}:{function}:{function_checksum}"
        self._cache_index[key] = result_data
        
        logger.debug(f"Indexed existing result for cache lookup: {function} in {file_path}")

    def load_existing_result_for_report(self, repo_name: str, file_path: str, function: str, function_checksum: str, result_data: Dict[str, Any]) -> str:
        """
        Load an existing result directly into the publisher's results collection for report generation.
        Unlike index_existing_result(), this method DOES add the result to _results and _repo_results,
        making it available via get_results() for report generation.
        
        This is used by --generate-report-from-existing-issues to load existing analysis files
        directly into the publisher without running the analysis loop.

        Args:
            repo_name: Name of the repository
            file_path: Path to the file containing the function
            function: Name of the analyzed function
            function_checksum: Checksum of the function content
            result_data: The full result data

        Returns:
            Unique identifier for the stored result
        """
        with self._lock:
            # Use centralized schema to normalize the result
            try:
                # Extract issues from result_data
                issues = result_data.get('results', [])
                if not issues and 'issue' in result_data:
                    # Single issue format
                    issues = [result_data]
                
                # Create standardized result
                standardized_result = create_result(
                    file_path=file_path,
                    function=function,
                    checksum=function_checksum,
                    issues=issues
                )
                
                # Validate the result
                validation_errors = standardized_result.validate()
                if validation_errors:
                    logger.warning(f"Result validation failed for {function}, skipping: {validation_errors}")
                    return None
                
                # Convert to dictionary format for storage
                enhanced_result = standardized_result.to_dict()
                
                # Store the result in the results collection
                result_id = str(uuid.uuid4())
                self._results[result_id] = enhanced_result.copy()
                
                # Track by repository
                if repo_name not in self._repo_results:
                    self._repo_results[repo_name] = []
                self._repo_results[repo_name].append(result_id)
                
                logger.debug(f"Loaded existing result for report: {function} in {file_path}")
                return result_id
                
            except Exception as e:
                logger.error(f"Failed to load existing result for {function}: {e}")
                return None

    def check_existing_result(self, file_name: str, function_name: str, checksum: str) -> Optional[Dict[str, Any]]:
        """
        Check for existing result, first in the in-memory cache index, then in registered stores.
        First successful lookup wins and stops other lookups.
        
        The cache index is populated during initialization by index_existing_result() and provides
        fast O(1) lookups. If not found there, falls back to querying prior result stores.

        Args:
            file_name: Name of the file
            function_name: Name of the function
            checksum: Function checksum

        Returns:
            Existing result if found in cache index or any store within timeout, None otherwise
        """
        logger.info(f"DEBUG: Publisher.check_existing_result called - file_name='{file_name}', function_name='{function_name}', checksum='{checksum[:8]}...'")

        # First, check the in-memory cache index (populated by index_existing_result during initialization)
        # This is the fastest lookup path and prevents duplicate issues by not adding to results during load
        if hasattr(self, '_cache_index') and self._cache_index:
            key = f"{file_name}:{function_name}:{checksum}"
            if key in self._cache_index:
                logger.info(f"Found existing result in cache index for {function_name} in {file_name}")
                return self._cache_index[key]
            
            # Also try with normalized file path (strip leading ./ or /)
            normalized_file = file_name.lstrip('./').lstrip('/')
            normalized_key = f"{normalized_file}:{function_name}:{checksum}"
            if normalized_key in self._cache_index:
                logger.info(f"Found existing result in cache index (normalized path) for {function_name} in {file_name}")
                return self._cache_index[normalized_key]

        # Fall back to checking prior result stores if not found in cache index
        if not self._prior_result_stores:
            logger.info(f"DEBUG: No prior result stores registered and not found in cache index")
            return None

        logger.info(f"DEBUG: Checking {len(self._prior_result_stores)} prior result stores for {function_name} in {file_name}")

        # Use ThreadPoolExecutor to query all stores concurrently
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(self._prior_result_stores)) as executor:
            # Submit all lookup tasks
            future_to_store = {
                executor.submit(self._lookup_with_timeout, store, file_name, function_name, checksum): store
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
                            logger.info(f"Found existing result in store {type(store).__name__} for {function_name} in {file_name}")
                            return result
                    except Exception as e:
                        store = future_to_store[future]
                        logger.warning(f"Error checking result in store {type(store).__name__}: {e}")
                        continue

            except concurrent.futures.TimeoutError:
                logger.warning(f"Timeout (15s) checking for existing result: {function_name} in {file_name}")
                # Cancel all remaining tasks
                for future in future_to_store:
                    future.cancel()

        return None

    def _lookup_with_timeout(self, store: ResultsCache, file_name: str, function_name: str, checksum: str) -> Optional[Dict[str, Any]]:
        """
        Perform lookup in a single store with individual timeout.

        Args:
            store: The store to lookup in
            file_name: Name of the file
            function_name: Name of the function
            checksum: Function checksum

        Returns:
            Result if found, None otherwise or on timeout
        """
        try:
            # Each store gets 15 second timeout
            if store.has_result(file_name, function_name, checksum, timeout_seconds=15.0):
                return store.get_existing_result(file_name, function_name, checksum, timeout_seconds=15.0)
        except Exception as e:
            logger.warning(f"Error in store lookup for {type(store).__name__}: {e}")

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

    def get_all_results(self, repo_name: str) -> List[Dict[str, Any]]:
        """
        Get all results for a specific repository

        Args:
            repo_name: Name of the repository

        Returns:
            List of all results for the repository
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


    def remove_result(self, repo_name: str, file_path: str, function: str, function_checksum: str) -> bool:
        """
        Remove a specific code analysis result

        Args:
            repo_name: Name of the repository
            file_path: Path to the file containing the function
            function: Name of the function
            function_checksum: Checksum of the function content

        Returns:
            True if result was found and removed, False otherwise
        """
        if repo_name not in self._repo_results:
            return False

        # Find the result by matching criteria
        for result_id in self._repo_results[repo_name]:
            if result_id in self._results:
                result = self._results[result_id]
                if (result.get("file_path") == file_path and
                    result.get("function") == function and
                    result.get("checksum") == function_checksum):
                    # Delete the result directly
                    del self._results[result_id]
                    self._repo_results[repo_name].remove(result_id)
                    return True

        return False

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
        filters = {"function": function_name}
        if checksum:
            filters["checksum"] = checksum

        return self.get_results(repo_name, filters)


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