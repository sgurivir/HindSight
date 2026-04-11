#!/usr/bin/env python3
# Created by Sridhar Gurivireddy
"""
Trace Result Repository Module
Handles analysis and statistics of trace analysis result files
"""

import os
import json
import argparse
import glob
import logging
import sys
from typing import Optional, Dict, Any
from collections import defaultdict
from pathlib import Path


from ...utils.file_content_provider import FileContentProvider
from ...utils.file_util import write_file
from ...utils.log_util import get_logger

# Add project root to path for imports
project_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(project_root))
logger = get_logger(__name__)


class TraceAnalysisResult:
    """
    Utility class for handling trace analysis results.
    """

    @staticmethod
    def _find_file_in_repository(filename: str, repo_path: str, _file_content_provider: FileContentProvider = None) -> Optional[str]:
        """
        Find a file in the repository by name and return its correct relative path.
        Uses FileContentProvider.resolve_file_path() if available, otherwise falls back to directory walking.

        Args:
            filename: Name of the file to find
            repo_path: Path to the repository root
            file_content_provider: Optional FileContentProvider instance for efficient lookup

        Returns:
            str: Relative path from repo root to the file's directory, None if not found
        """
        try:
            # Try using FileContentProvider.resolve_file_path() if available
            try:

                # Use empty string as file_path to trigger index-only search
                resolved_path = FileContentProvider.resolve_file_path(filename, "")
                if resolved_path:
                    # Extract directory from the resolved path
                    resolved_dir = str(Path(resolved_path).parent)
                    return resolved_dir if resolved_dir != '.' else ''
            except RuntimeError:
                pass  # FileContentProvider not available

            # Fallback to original directory walking method
            for root, _dirs, files in os.walk(repo_path):
                if filename in files:
                    # Return relative path from repo root to the file's directory
                    relative_dir = os.path.relpath(root, repo_path)
                    return relative_dir if relative_dir != '.' else ''
            return None
        except Exception as e:
            logger.error(f"Error searching for file {filename}: {e}")
            return None

    @staticmethod
    def _correct_file_paths(result_data, repo_path: str, file_content_provider: FileContentProvider = None) -> bool:
        """
        Correct file paths in the analysis result based on actual file system.
        Python has complete control over folder assignment.

        Args:
            result_data: Parsed JSON result data (dict or list)
            repo_path: Path to the repository root
            file_content_provider: Optional FileContentProvider instance for efficient lookup

        Returns:
            bool: True if any corrections were made
        """
        corrections_made = False

        try:
            # Handle both single issue (dict) and multiple issues (list)
            issues = []
            if isinstance(result_data, dict):
                issues = [result_data]
            elif isinstance(result_data, list):
                issues = result_data
            else:
                return False

            # First pass: Find correct paths for all files
            file_path_map = {}  # filename -> correct_path

            for issue in issues:
                file_name = issue.get('file_name', '')
                if file_name and file_name not in file_path_map:
                    correct_path = TraceAnalysisResult._find_file_in_repository(file_name, repo_path, file_content_provider)
                    if correct_path is not None:
                        file_path_map[file_name] = correct_path
                        logger.info(f"Found {file_name} at: {correct_path}")
                    else:
                        logger.warning(f"Could not find {file_name} in repository")
                        file_path_map[file_name] = 'Unknown'

            # Group issues by callstack to ensure consistent folder assignment
            callstack_groups = {}
            for issue in issues:
                callstack_text = issue.get('Callstack', '')
                if callstack_text:
                    if callstack_text not in callstack_groups:
                        callstack_groups[callstack_text] = []
                    callstack_groups[callstack_text].append(issue)

            # Second pass: For callstack groups, use the most specific valid path
            for callstack_text, issues_in_group in callstack_groups.items():
                if len(issues_in_group) > 1:
                    logger.info(f"Processing {len(issues_in_group)} issues sharing the same callstack")

                    # Find the best path among all files in this callstack group
                    best_path = None
                    best_priority = -1

                    for issue in issues_in_group:
                        file_name = issue.get('file_name', '')
                        if file_name in file_path_map:
                            path = file_path_map[file_name]
                            if path != 'Unknown':
                                # Prefer more specific paths (deeper directory structure)
                                priority = len(path.split('/')) if path else 0
                                if priority > best_priority:
                                    best_path = path
                                    best_priority = priority

                    # If we found a best path, assign it to all issues in this callstack
                    if best_path is not None:
                        logger.info(f"Assigning all {len(issues_in_group)} issues in callstack to: {best_path}")
                        for issue in issues_in_group:
                            file_name = issue.get('file_name', '')
                            file_path_map[file_name] = best_path

            # Third pass: Apply the corrected paths to all issues
            for issue in issues:
                file_name = issue.get('file_name', '')
                if file_name in file_path_map:
                    correct_path = file_path_map[file_name]
                    original_path = issue.get('file_path', '')

                    if original_path != correct_path:
                        #logger.info(f"Correcting file_path for {file_name}: '{original_path}' -> '{correct_path}'")
                        issue['file_path'] = correct_path
                        corrections_made = True

            return corrections_made

        except Exception as e:
            logger.error(f"Error correcting file paths: {e}")
            return False

    @staticmethod
    def save_result(result: str, output_file: str, prompt_file_path: str = None, repo_path: str = None, file_content_provider: FileContentProvider = None, original_callstack: dict = None) -> bool:
        """
        Save the analysis result to output file with Python-controlled folder assignment.

        Args:
            result: Processed analysis result
            output_file: Path to output file
            prompt_file_path: Optional path to the original prompt file
            repo_path: Optional path to repository root for file path correction
            file_content_provider: Optional FileContentProvider instance for efficient lookup
            original_callstack: Optional original callstack data to embed directly

        Returns:
            bool: True if successful
        """
        try:
            # Try to parse the result as JSON to correct paths and add callstack data
            try:
                result_data = json.loads(result)

                # Ensure proper structure: if result_data is a list, wrap it in a dictionary
                if isinstance(result_data, list):
                    logger.info("Wrapping list of issues in proper dictionary structure")
                    result_data = {"issues": result_data}

                # Python takes complete control of folder assignment if repo_path provided
                if repo_path:
                    # Handle the wrapped structure for path correction
                    issues_to_correct = result_data.get("issues", []) if isinstance(result_data, dict) else result_data
                    corrections_made = TraceAnalysisResult._correct_file_paths(issues_to_correct, repo_path, file_content_provider)
                    if corrections_made:
                        logger.info("Python corrected file paths based on actual file system")

                # Add original callstack data if provided directly
                if original_callstack:
                    if isinstance(result_data, dict):
                        # Handle wrapped structure
                        if "issues" in result_data and isinstance(result_data["issues"], list):
                            # Embed callstack data in ALL issues
                            embedded_count = 0
                            for issue in result_data["issues"]:
                                if isinstance(issue, dict):
                                    issue['original_callstack'] = original_callstack
                                    embedded_count += 1
                            if embedded_count > 0:
                                logger.info(f"Added original callstack data to {embedded_count} issues (provided directly)")
                        else:
                            # Single issue in dictionary format
                            result_data['original_callstack'] = original_callstack
                            logger.info("Added original callstack data (provided directly)")

                # Fallback: Find the corresponding callstack file if prompt_file_path provided and no direct callstack
                elif prompt_file_path:
                    prompt_filename = os.path.basename(prompt_file_path)
                    if prompt_filename.startswith('prompt_') and prompt_filename.endswith('.txt'):
                        # Extract the number from prompt_0001.txt -> 0001
                        prompt_number = prompt_filename.replace('prompt_', '').replace('.txt', '')

                        # Look for corresponding callstack file
                        prompt_dir = os.path.dirname(prompt_file_path)
                        callstack_file = os.path.join(prompt_dir, f"callstack_{prompt_number}.json")

                        if os.path.exists(callstack_file):
                            try:
                                with open(callstack_file, 'r', encoding='utf-8') as f:
                                    callstack_data = json.load(f)

                                # Add the original callstack data to the result
                                if isinstance(result_data, dict):
                                    # Handle wrapped structure
                                    if "issues" in result_data and isinstance(result_data["issues"], list):
                                        # Embed callstack data in ALL issues
                                        embedded_count = 0
                                        for issue in result_data["issues"]:
                                            if isinstance(issue, dict):
                                                issue['original_callstack'] = callstack_data
                                                embedded_count += 1
                                        if embedded_count > 0:
                                            logger.info(f"Added original callstack data to {embedded_count} issues from {callstack_file}")
                                    else:
                                        # Single issue in dictionary format
                                        result_data['original_callstack'] = callstack_data
                                        logger.info(f"Added original callstack data from {callstack_file}")

                            except Exception as e:
                                logger.warning(f"Could not load callstack file {callstack_file}: {e}")
                        else:
                            logger.warning(f"Callstack file not found: {callstack_file}")

                # Convert back to JSON string with corrected paths
                result = json.dumps(result_data, indent=2)

            except json.JSONDecodeError:
                logger.info("Result is not JSON, saving as-is without path correction")

            success = write_file(output_file, result)
            if success:
                logger.info(f"Results saved to {output_file}")
            return success
        except Exception as e:
            logger.error(f"Error saving result: {e}")
            return False


class TraceAnalysisResultRepository:
    """
    Singleton repository class for analyzing trace analysis result files and saving new results.
    """

    _instance = None
    _initialized = False

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, results_dir: str = None, file_content_provider: FileContentProvider = None):
        """
        Initialize the repository with a results directory and file content provider.

        Args:
            results_dir: Directory containing *_analysis.json files
            file_content_provider: FileContentProvider instance for file operations
        """
        # Only initialize once (singleton pattern)
        if not self._initialized:
            self.results_dir = results_dir
            self.file_content_provider = file_content_provider
            self._initialized = True

            if results_dir and not os.path.exists(results_dir):
                os.makedirs(results_dir, exist_ok=True)
                logger.info(f"Created results directory: {results_dir}")
        elif results_dir:
            # Update results_dir if provided in subsequent calls
            self.results_dir = results_dir
            if not os.path.exists(results_dir):
                os.makedirs(results_dir, exist_ok=True)
                logger.info(f"Created results directory: {results_dir}")

        if file_content_provider:
            self.file_content_provider = file_content_provider

    @classmethod
    def get_instance(cls, file_content_provider: FileContentProvider = None) -> 'TraceAnalysisResultRepository':
        """
        Get the singleton instance of TraceAnalysisResultRepository.

        Args:
            file_content_provider: FileContentProvider instance (only used on first initialization)

        Returns:
            TraceAnalysisResultRepository: The singleton instance
        """
        if cls._instance is None:
            # Create instance with the provided FileContentProvider
            cls._instance = cls(file_content_provider=file_content_provider)
        return cls._instance

    def save_trace_result(self, output_file: str, results_data, metadata: Dict[str, Any] = None) -> bool:
        """
        Save a trace analysis result using the TraceAnalysisResult utility.

        Args:
            output_file: Path to output file
            results_data: Analysis result data (string or dict/list)
            metadata: Optional metadata dictionary containing additional info
                     - prompt_file_path: Path to original prompt file
                     - repo_path: Path to repository root
                     - original_callstack: Original callstack data to embed

        Returns:
            bool: True if successful
        """
        # Convert results_data to string if it's not already
        if isinstance(results_data, (dict, list)):
            result_str = json.dumps(results_data, indent=2)
        else:
            result_str = str(results_data)

        # Extract metadata
        prompt_file_path = metadata.get('prompt_file_path') if metadata else None
        repo_path = metadata.get('repo_path') if metadata else None
        original_callstack = metadata.get('original_callstack') if metadata else None

        return TraceAnalysisResult.save_result(
            result=result_str,
            output_file=output_file,
            prompt_file_path=prompt_file_path,
            repo_path=repo_path,
            file_content_provider=self.file_content_provider,
            original_callstack=original_callstack
        )

    def analyze_trace_results_directory(self) -> Dict[str, Any]:
        """
        Analyze the trace analysis result JSON files and return statistics.

        Returns:
            Dict containing analysis statistics
        """
        stats = {
            'total_files': 0,
            'valid_json_files': 0,
            'invalid_json_files': 0,
            'total_issues': 0,
            'severity_counts': defaultdict(int),
            'file_path_counts': defaultdict(int),
            'function_counts': defaultdict(int),
            'files_with_callstack_data': 0,
            'error_files': []
        }

        # Find all analysis JSON files
        analysis_files = glob.glob(os.path.join(self.results_dir, "*_analysis.json"))
        stats['total_files'] = len(analysis_files)

        logger.info(f"Found {len(analysis_files)} analysis files in {self.results_dir}")

        for file_path in analysis_files:
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)

                stats['valid_json_files'] += 1

                # Handle both single issue (dict) and multiple issues (list)
                issues = []
                if isinstance(data, dict):
                    issues = [data]
                    # Check if this file has callstack data
                    if 'original_callstack' in data:
                        stats['files_with_callstack_data'] += 1
                elif isinstance(data, list):
                    issues = data
                    # Check if first issue has callstack data
                    if issues and isinstance(issues[0], dict) and 'original_callstack' in issues[0]:
                        stats['files_with_callstack_data'] += 1

                stats['total_issues'] += len(issues)

                # Analyze each issue
                for issue in issues:
                    if not isinstance(issue, dict):
                        continue

                    # Count severity
                    severity = issue.get('severity', 'unknown').lower()
                    stats['severity_counts'][severity] += 1

                    # Count file paths
                    file_path = issue.get('file_path', 'unknown')
                    stats['file_path_counts'][file_path] += 1

                    # Count function names
                    function_name = issue.get('function_name', 'unknown')
                    stats['function_counts'][function_name] += 1

            except json.JSONDecodeError as e:
                stats['invalid_json_files'] += 1
                stats['error_files'].append(f"{os.path.basename(file_path)}: Invalid JSON - {str(e)}")
                logger.warning(f"Invalid JSON in {file_path}: {e}")
            except Exception as e:
                stats['error_files'].append(f"{os.path.basename(file_path)}: Error - {str(e)}")
                logger.error(f"Error processing {file_path}: {e}")

        return stats

    def print_trace_analysis_statistics(self, stats: Dict[str, Any] = None) -> None:
        """
        Print formatted statistics about trace analysis results.

        Args:
            stats: Optional statistics dictionary. If None, will analyze directory first.
        """
        if stats is None:
            stats = self.analyze_trace_results_directory()

        print("=" * 80)
        print("TRACE ANALYSIS RESULTS STATISTICS")
        print("=" * 80)
        print(f"Results Directory: {self.results_dir}")
        print("=" * 80)

        # File statistics
        print(f"Total analysis files: {stats['total_files']}")
        print(f"Valid JSON files: {stats['valid_json_files']}")
        print(f"Invalid JSON files: {stats['invalid_json_files']}")
        print(f"Files with callstack data: {stats['files_with_callstack_data']}")
        print()

        # Issue statistics
        print(f"Total issues found: {stats['total_issues']}")
        if stats['total_issues'] > 0:
            avg_issues_per_file = stats['total_issues'] / max(stats['valid_json_files'], 1)
            print(f"Average issues per file: {avg_issues_per_file:.2f}")
        print()

        # Severity breakdown
        if stats['severity_counts']:
            print("SEVERITY BREAKDOWN:")
            print("-" * 40)
            total_with_severity = sum(stats['severity_counts'].values())
            for severity, count in sorted(stats['severity_counts'].items()):
                percentage = (count / total_with_severity * 100) if total_with_severity > 0 else 0
                print(f"  {severity.capitalize():12}: {count:6} ({percentage:5.1f}%)")
            print()

        # Top file paths
        if stats['file_path_counts']:
            print("TOP 10 FILE PATHS:")
            print("-" * 40)
            sorted_paths = sorted(stats['file_path_counts'].items(), key=lambda x: x[1], reverse=True)
            for path, count in sorted_paths[:10]:
                print(f"  {count:4} issues in: {path}")
            print()

        # Top functions
        if stats['function_counts']:
            print("TOP 10 FUNCTIONS WITH ISSUES:")
            print("-" * 40)
            sorted_functions = sorted(stats['function_counts'].items(), key=lambda x: x[1], reverse=True)
            for function, count in sorted_functions[:10]:
                print(f"  {count:4} issues in: {function}")
            print()

        # Error files
        if stats['error_files']:
            print("ERROR FILES:")
            print("-" * 40)
            for error in stats['error_files']:
                print(f"  {error}")
            print()

        print("=" * 80)


def main():
    """
    Main function to analyze trace analysis results directory and print statistics.
    """
    parser = argparse.ArgumentParser(
        description="Analyze trace analysis results directory and print statistics",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "results_dir",
        help="Directory containing *_analysis.json files"
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose logging"
    )

    args = parser.parse_args()

    # Setup logging
    if args.verbose:
        logging.basicConfig(level=logging.INFO)

    try:
        # Create repository and analyze results
        if not os.path.exists(args.results_dir):
            print(f"Error: Results directory does not exist: {args.results_dir}")
            return 1

        repository = TraceAnalysisResultRepository(args.results_dir)
        repository.print_trace_analysis_statistics()
    except Exception as e:
        print(f"Unexpected error: {e}")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())