import os
import random
import sys
from pathlib import Path
from typing import Dict, Any
import logging

from .base_analyzer import BaseAnalyzer

# Add the project root to Python path for imports
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from results_store.code_analysis_publisher import CodeAnalysisResultsPublisher
from results_store.code_analysys_results_local_fs_subscriber import CodeAnalysysResultsLocalFSSubscriber
from results_store.trace_analysis_publisher import TraceAnalysisResultsPublisher
from results_store.trace_analysys_results_local_fs_subscriber import TraceAnalysysResultsLocalFSSubscriber

class DummyCodeAnalyzer(BaseAnalyzer):
    """Analyzer that simulates analysis without LLM calls."""

    def initialize(self, config):
        self.config = config
        self._initialized = True
        # Set up logging
        self.logger = logging.getLogger(__name__)

    def analyze_function(self, func_record):
        # Extract file path using the same logic as the real analyzer
        file_path = self._extract_file_path_from_func_record(func_record)

        # If no file path found, return None to indicate no analysis result
        if not file_path or file_path.strip() == "":
            return None

        # Additional validation: ensure file_path is a meaningful path
        if file_path in ["unknown", "Unknown", "UNKNOWN", "null", "NULL"]:
            return None

        # Extract function name and validate it
        function_name = func_record.get('function', func_record.get('name', ''))
        if not function_name or function_name.strip() == "":
            return None

        # Extract checksum from func_record if available, otherwise generate a dummy one
        checksum = func_record.get('checksum', 'dummy_checksum_12345678')

        # Randomly select realistic categories and issue types used by the real analyzer
        categories = ["performance", "memoryManagement", "concurrency", "errorHandling", "resourceManagement", "codeQuality", "logicBug", "minorOptimizationConsiderations"]
        issue_types = {
            "performance": ["inefficientAlgorithm", "unnecessaryComputation", "slowLoop", "redundantOperation"],
            "memoryManagement": ["memoryLeak", "bufferOverflow", "uninitializedVariable", "danglingPointer"],
            "concurrency": ["raceCondition", "deadlock", "threadSafety", "atomicityViolation"],
            "errorHandling": ["uncaughtException", "improperErrorHandling", "missingValidation", "silentFailure"],
            "resourceManagement": ["resourceLeak", "improperCleanup", "fileHandleLeak", "connectionLeak"],
            "codeQuality": ["codeSmell", "duplicateCode", "complexFunction", "poorNaming"]
        }

        # Randomly select category and corresponding issue type
        category = random.choice(categories)
        if category in issue_types:
            issue_type = random.choice(issue_types[category])
        else:
            # Fallback for categories not in issue_types dict
            issue_type = "generalIssue"

        # Vary severity occasionally
        severities = ["low", "low", "low", "medium", "medium", "high"]  # Weighted toward low
        severity = random.choice(severities)

        # Extract line information from func_record if available
        line_info = self._extract_line_info_from_func_record(func_record)

        # Return result as a list of issues (matching code analyzer format)
        # The code analyzer pipeline expects either a list or single dict, not a wrapper with 'results' key
        return [{
            "issue": f"Dummy analysis detected potential {category} issue in function {function_name}",
            "severity": severity,
            "category": category,
            "confidence": 0.95,
            "lines": line_info,
            "issueType": issue_type,
            "function_name": function_name,
            "file_path": file_path,
            "function": function_name,  # Ensure function field is also present
            "suggestion": f"Consider reviewing the {category} aspects of function {function_name}. This is a simulated finding from the dummy analyzer.",
            "description": f"Dummy analysis detected potential {issue_type} in {function_name} at lines {line_info}",
            "line_number": line_info,
            "lineNumber": line_info
        }]

    def finalize(self):
        pass

    def _extract_file_path_from_func_record(self, func_record):
        """
        Extract file path from func_record, checking multiple possible locations.
        Uses the same logic as the real analyzer to ensure consistency.

        Args:
            func_record: The function record data

        Returns:
            str: File path or None if not found
        """
        # Try direct / top-level contexts
        file_path = (
            func_record.get('file')
            or (func_record.get('context', {}).get('file')
                if isinstance(func_record.get('context'), dict) else None)
            or (func_record.get('fileContext', {}).get('file')
                if isinstance(func_record.get('fileContext'), dict) else None)
        )

        # Nested function data
        if not file_path and isinstance(func_record.get('function'), dict):
            func = func_record['function']
            file_path = (
                (func.get('context', {}).get('file')
                if isinstance(func.get('context'), dict) else None)
                or func.get('file')
            )

        # Invoking list (first item's context)
        if (not file_path and isinstance(func_record.get('invoking'), list)
                and func_record['invoking']):
            first = func_record['invoking'][0]
            if isinstance(first, dict):
                ctx = first.get('context')
                if isinstance(ctx, dict):
                    file_path = ctx.get('file')

        # Return the file path as-is (could be None)
        return file_path

    def _extract_line_info_from_func_record(self, func_record):
        """
        Extract line information from func_record, checking multiple possible locations.
        Uses the same logic as the real analyzer to ensure consistency.

        Args:
            func_record: The function record data

        Returns:
            str: Line information in format "start-end" or "N/A" if not found
        """
        try:
            # Try to get line numbers from different possible locations
            start_line = None
            end_line = None

            # Check direct fields
            start_line = func_record.get('start_line') or func_record.get('startLine')
            end_line = func_record.get('end_line') or func_record.get('endLine')

            # Check in context
            if not start_line or not end_line:
                context = func_record.get('context', {})
                if isinstance(context, dict):
                    start_line = start_line or context.get('start_line') or context.get('startLine') or context.get('start')
                    end_line = end_line or context.get('end_line') or context.get('endLine') or context.get('end')

            # Check in nested function data
            if not start_line or not end_line:
                function_data = func_record.get('function', {})
                if isinstance(function_data, dict):
                    start_line = start_line or function_data.get('start_line') or function_data.get('startLine')
                    end_line = end_line or function_data.get('end_line') or function_data.get('endLine')

                    # Also check function's context
                    func_context = function_data.get('context', {})
                    if isinstance(func_context, dict):
                        start_line = start_line or func_context.get('start_line') or func_context.get('startLine') or func_context.get('start')
                        end_line = end_line or func_context.get('end_line') or func_context.get('endLine') or func_context.get('end')

            # Format line information
            if start_line is not None and end_line is not None:
                return f"{start_line}-{end_line}"
            elif start_line is not None:
                return str(start_line)
            else:
                # Return a dummy line range for dummy analyzer
                return "1-10"

        except Exception as e:
            # Return a dummy line range if extraction fails
            return "1-10"

    def _extract_filename_from_path(self, file_path):
        """
        Extract just the filename from a file path.

        Args:
            file_path: Full file path (e.g., "selfdrive/ui/installer/installer.cc")

        Returns:
            str: Just the filename (e.g., "installer.cc")
        """
        if not file_path:
            return None

        # Handle both forward and backward slashes
        if '/' in file_path:
            return file_path.split('/')[-1]
        elif '\\' in file_path:
            return file_path.split('\\')[-1]
        else:
            # Already just a filename
            return file_path

    def pull_results_from_directory(self, artifacts_dir: str) -> Dict[str, Any]:
        """
        Pull dummy analysis results from the provided artifacts directory.

        Args:
            artifacts_dir: Path to the artifacts directory containing analysis results

        Returns:
            Dictionary containing:
            - 'results': List of dummy analysis results
            - 'statistics': Dictionary with statistics about the results
            - 'summary': Dictionary with summary information
        """
        # For dummy analyzer, use publisher-subscriber mechanism to read results
        # Try both code analysis and trace analysis publishers

        # Extract repo name from artifacts directory
        repo_name = os.path.basename(artifacts_dir.rstrip('/'))

        # Try code analysis first
        try:
            code_publisher = CodeAnalysisResultsPublisher()
            code_subscriber = CodeAnalysysResultsLocalFSSubscriber(artifacts_dir)
            code_subscriber.set_repo_name(repo_name)

            loaded_count = code_subscriber.load_existing_results(repo_name, code_publisher)
            if loaded_count > 0:
                all_results = code_publisher.get_results(repo_name)
                all_issues = []
                for result in all_results:
                    if 'results' in result and isinstance(result['results'], list):
                        all_issues.extend(result['results'])
                    else:
                        all_issues.append(result)

                return {
                    'results': all_issues,
                    'statistics': self._calculate_statistics(all_issues),
                    'summary': {
                        'analyzer': self.name(),
                        'analyzer_type': 'dummy_code_analysis',
                        'analysis_directory': os.path.join(artifacts_dir, "results", "code_analysis"),
                        'total_files': len(set(issue.get('file_name', issue.get('file', '')) for issue in all_issues)),
                        'files_processed': len(set(issue.get('file_name', issue.get('file', '')) for issue in all_issues)),
                        'files_with_errors': 0,
                        'total_issues': len(all_issues)
                    }
                }
        except Exception:
            pass  # Try trace analysis

        # Try trace analysis
        try:
            trace_publisher = TraceAnalysisResultsPublisher()
            trace_subscriber = TraceAnalysysResultsLocalFSSubscriber(artifacts_dir)
            trace_subscriber.set_repo_name(repo_name)

            loaded_count = trace_subscriber.load_existing_results(repo_name, trace_publisher)
            if loaded_count > 0:
                all_results = trace_publisher.get_results(repo_name)
                all_issues = []
                for result in all_results:
                    if 'results' in result and isinstance(result['results'], list):
                        all_issues.extend(result['results'])
                    elif 'issues' in result and isinstance(result['issues'], list):
                        all_issues.extend(result['issues'])
                    else:
                        all_issues.append(result)

                return {
                    'results': all_issues,
                    'statistics': self._calculate_statistics(all_issues),
                    'summary': {
                        'analyzer': self.name(),
                        'analyzer_type': 'dummy_trace_analysis',
                        'analysis_directory': os.path.join(artifacts_dir, "results", "trace_analysis"),
                        'total_files': len(set(issue.get('file_name', issue.get('file', '')) for issue in all_issues)),
                        'files_processed': len(set(issue.get('file_name', issue.get('file', '')) for issue in all_issues)),
                        'files_with_errors': 0,
                        'total_issues': len(all_issues)
                    }
                }
        except Exception:
            pass  # No results found

        # No results found
        return {
            'results': [],
            'statistics': self._calculate_statistics([]),
            'summary': {
                'total_files': 0,
                'files_processed': 0,
                'files_with_errors': 0,
                'total_issues': 0,
                'directory': artifacts_dir,
                'analyzer': self.name(),
                'analyzer_type': 'dummy_no_results',
                'analysis_directory': 'Not found'
            }
        }