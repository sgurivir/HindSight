#!/usr/bin/env python3
"""
Trace Analysis Results Local File System Subscriber
Concrete implementation of subscriber that writes trace analysis results to JSON files on local filesystem
"""

import os
import json
import threading
from typing import Any, Dict, List
from .interface.trace_analysis_result_store_interface import TraceAnalysisSubscriber


class TraceAnalysysResultsLocalFSSubscriber(TraceAnalysisSubscriber):
    """
    Concrete subscriber that writes trace analysis results to JSON files on local filesystem.
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
            # Create the directory structure: base_output_dir/repo_name/results/trace_analysis
            repo_artifacts_dir = os.path.join(self.base_output_dir, repo_name)
            analysis_dir = os.path.join(repo_artifacts_dir, "results", "trace_analysis")

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
        Load existing trace analysis results from files into the publisher.
        This enables trace-based caching and avoids re-analyzing the same traces.

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

            # Find all analysis JSON files
            for filename in os.listdir(analysis_dir):
                if filename.endswith('_analysis.json'):
                    file_path = os.path.join(analysis_dir, filename)

                    try:
                        with open(file_path, 'r', encoding='utf-8') as f:
                            result_data = json.load(f)

                        # Extract the required fields for publisher
                        trace_id = result_data.get('trace_id', '')
                        callstack = result_data.get('callstack', [])

                        if trace_id and callstack:
                            # Add to publisher using the trace-specific method
                            publisher.add_trace_result(
                                repo_name=repo_name,
                                trace_id=trace_id,
                                callstack=callstack,
                                result=result_data
                            )
                            loaded_count += 1
                        else:
                            # Fallback: use generic publish_result method
                            publisher.publish_result(repo_name, result_data)
                            loaded_count += 1

                    except Exception as e:
                        print(f"Error loading existing trace result from {file_path}: {e}")
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
        with self._lock:
            try:
                # Extract repository name from the result or use current repo name
                repo_name = self._extract_repo_name_with_fallback(result)
                analysis_dir = self.get_analysis_dir(repo_name)

                # Generate filename using the same logic as current implementation
                filename = self._generate_filename(result)
                file_path = os.path.join(analysis_dir, filename)

                # Write the result to JSON file with the same format as current implementation
                with open(file_path, 'w', encoding='utf-8') as f:
                    json.dump(result, f, indent=2, ensure_ascii=False)

            except Exception as e:
                # Log error but don't raise to avoid breaking the publisher
                print(f"Error writing trace analysis result to file: {e}")

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
            print(f"Error updating trace analysis result file: {e}")


    def on_trace_analyzed(self, trace_id: str, callstack: List[str], result: Dict[str, Any]) -> None:
        """
        Called when a trace analysis is completed.
        This is handled by on_result_added, so we can use that.

        Args:
            trace_id: Unique identifier for the trace
            callstack: List of function calls in the trace
            result: Analysis result data
        """
        # Generate a unique result ID for this trace analysis
        result_id = f"trace_{trace_id}_{hash(str(callstack))}"

        # Enhance result with trace-specific data
        enhanced_result = result.copy()
        enhanced_result['trace_id'] = trace_id
        enhanced_result['callstack'] = callstack

        self.on_result_added(result_id, enhanced_result)

    def on_callstack_pattern_detected(self, pattern: str, traces: List[Dict[str, Any]]) -> None:
        """
        Called when a recurring callstack pattern is detected.
        This could be used for pattern analysis files in the future.

        Args:
            pattern: Description of the detected pattern
            traces: List of traces that match the pattern
        """
        # For now, we don't write pattern files, but this could be extended
        pass

    def on_trace_batch_completed(self, batch_results: List[Dict[str, Any]]) -> None:
        """
        Called when a batch of trace analyses is completed.
        Writes all results in the batch.

        Args:
            batch_results: List of trace analysis results in the batch
        """
        for i, result in enumerate(batch_results):
            result_id = f"batch_trace_result_{i}"
            self.on_result_added(result_id, result)

    def _extract_repo_name(self, result: Dict[str, Any]) -> str:
        """
        Extract repository name from result data.

        Args:
            result: The result data

        Returns:
            Repository name or default
        """
        # Try to extract repo name from file path or trace data
        file_path = result.get('file_path', '') or result.get('file', '')
        if file_path:
            # If file_path is absolute, extract the repo name from it
            # Otherwise, use a default repo name
            if os.path.isabs(file_path):
                # Extract repo name from absolute path
                path_parts = file_path.split(os.sep)
                if len(path_parts) > 1:
                    return path_parts[-2] if path_parts[-1] else path_parts[-3]

        # Try to extract from trace_id or other fields
        trace_id = result.get('trace_id', '')
        if trace_id and '_' in trace_id:
            # If trace_id has repo info, extract it
            parts = trace_id.split('_')
            if len(parts) > 1:
                return parts[0]

        # Default repo name if we can't extract it
        return "default_repo"

    def _generate_filename(self, result: Dict[str, Any]) -> str:
        """
        Generate filename using the same logic as the current trace analysis implementation.

        Args:
            result: The result data

        Returns:
            Generated filename
        """
        # For trace analysis, the filename is typically based on the prompt file name
        # or trace identifier

        # Try to get original prompt filename if available
        prompt_file = result.get('prompt_file', '')
        if prompt_file:
            # Extract base name and replace .txt with _analysis.json
            base_name = os.path.splitext(os.path.basename(prompt_file))[0]
            return f"{base_name}_analysis.json"

        # Try to use trace_id
        trace_id = result.get('trace_id', '')
        if trace_id:
            safe_trace_id = "".join(c for c in trace_id if c.isalnum() or c in ('_', '-'))
            return f"trace_{safe_trace_id}_analysis.json"

        # Try to use callstack hash
        callstack = result.get('callstack', [])
        if callstack:
            callstack_str = str(callstack)
            callstack_hash = str(abs(hash(callstack_str)))[:8]
            return f"callstack_{callstack_hash}_analysis.json"

        # Fallback to generic filename with timestamp-like hash
        result_hash = str(abs(hash(str(result))))[:8]
        return f"trace_analysis_{result_hash}_analysis.json"

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