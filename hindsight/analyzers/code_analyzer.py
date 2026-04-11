#!/usr/bin/env python3
# Created by Sridhar Gurivireddy
"""
Main entry point for Hindsight Analysis
Handles AST call graph generation and processing based on configuration

FILTERING LOGIC:
===============

This tool implements a two-stage filtering approach:

1. AST GENERATION FILTERING:
   - Only honors exclude_directories (from JSON config or --exclude-directories)
   - All AST files (clang_defined_classes.json, merged_defined_classes.json, merged_functions.json,
     swift_call_graph.json, etc.) are generated excluding only these directories
   - Other filters (include_directories, exclude_files) do NOT affect AST generation

2. LLM ANALYSIS FILTERING:
   - Applies to determining which files should be analyzed with LLM
   - Uses the following precedence (higher precedence overrides lower):

     a) --file-filter (HIGHEST PRECEDENCE)
        - If provided, only analyzes functions/classes in specified files
        - Completely ignores all other filtering parameters

     b) include_directories + exclude_directories + exclude_files
        - include_directories: If provided, only analyze files in these directories
        - exclude_directories: Exclude these directories (even if in include_directories)
        - exclude_files: Exclude specific files
        - If no include_directories specified, all files are included by default

COMMAND LINE OVERRIDES:
======================
All filtering parameters can be overridden via command line arguments:
- --exclude-directories: Overrides JSON config exclude_directories
- --include-directories: Overrides JSON config include_directories
- --exclude-files: Overrides JSON config exclude_files
- --file-filter: Provides file-specific filtering (highest precedence)

EXAMPLES:
========
# Use file filter (ignores all directory filters)
python -m hindsight.analyzers.code_analyzer --config config.json --repo /path/to/repo --file-filter src/main.py src/utils.py

# Include only src directory, but exclude test subdirectories
python -m hindsight.analyzers.code_analyzer --config config.json --repo /path/to/repo --include-directories src --exclude-directories src/test

# Exclude specific files and directories
python -m hindsight.analyzers.code_analyzer --config config.json --repo /path/to/repo --exclude-directories build .git --exclude-files debug.py

# Use recently_modified_files strategy with function filter (REQUIRED)
python -m hindsight.analyzers.code_analyzer --config config.json --repo /path/to/repo --analysys_type recently_modified_files --function-filter /path/to/functions_modified.json
"""

import argparse
import json
import os
import sys
import tempfile
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from .analysis_runner import AnalysisRunner
from .analysis_runner_mixins import UnifiedIssueFilterMixin, ReportGeneratorMixin
from .directory_classifier import DirectoryClassifier
from .base_analyzer import BaseAnalyzer, AnalyzerProtocol
from .dummy_analyzer import DummyCodeAnalyzer
from .llm_based_analyzer import LLMBasedAnalyzer
from .token_tracker import TokenTracker
from ..analysys_strategy.diff_strategy import DiffStrategy
from ..core.constants import (DEFAULT_LOGS_DIR, DEFAULT_MAX_TOKENS, DEFAULT_TEMPERATURE,
                              MIN_FUNCTION_BODY_LENGTH, MAX_FUNCTION_BODY_LENGTH,
                              DEFAULT_NUM_FUNCTIONS_TO_ANALYZE,
                              MERGED_DEFINED_CLASSES_FILE,
                              MERGED_SYMBOLS_FILE, NESTED_CALL_GRAPH_FILE,
                              PROCESSED_OUTPUT_DIR, DEFAULT_LLM_MODEL, DEFAULT_LLM_API_END_POINT)
from ..core.lang_util.all_supported_extensions import ALL_SUPPORTED_EXTENSIONS
from ..core.lang_util.filter_by_file_util import FilterByFileUtil
from ..core.ast_index import RepoAstIndex
from hindsight.utils.log_util import LogUtil, get_logger, setup_default_logging
from ..core.llm.code_analysis import AnalysisConfig, CodeAnalysis
from ..core.llm.llm import Claude
from ..report.issue_directory_organizer import DirectoryNode, RepositoryDirHierarchy
from ..report.report_generator import calculate_stats, generate_html_report, generate_dropped_issues_html_report
from ..utils.issue_organizer_util import organize_issues_complete
from ..utils.api_key_util import get_api_key
from ..utils.config_util import ConfigValidationError, load_and_validate_config, get_api_key_from_config, get_llm_provider_type
from ..core.errors import AnalyzerErrorCode, AnalysisResult
from ..core.constants import MAX_SUPPORTED_FILE_COUNT
from ..utils.file_util import extract_function_context, get_artifacts_temp_file_path, read_json_file
from ..utils.filtered_file_finder import FilteredFileFinder
from ..utils.hash_util import HashUtil
from ..utils.output_directory_provider import get_output_directory_provider

# Import publisher-subscriber classes
from results_store.code_analysis_publisher import CodeAnalysisResultsPublisher
from results_store.code_analysys_results_local_fs_subscriber import CodeAnalysysResultsLocalFSSubscriber
from results_store.file_system_results_cache import FileSystemResultsCache

# Import centralized schema
from ..core.schema.code_analysis_result_schema import (
    CodeAnalysisResult,
    CodeAnalysisResultValidator,
    create_result
)

# Import unified issue filter
from ..issue_filter import create_unified_filter

# Add the project root to Python path for imports
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

# Initialize logging at module level (before creating logger)
setup_default_logging()
logger = get_logger(__name__)

# Local constants specific to code analyzer
ANALYSIS_FILE_SUFFIX = "_analysis.json"
DEFAULT_MAX_DEPTH = 100

# Default output directory names
DEFAULT_AST_CALL_GRAPH_DIR = "code_insights"
DEFAULT_LLM_ANALYSIS_OUT_DIR = "code_analysis"


class CodeAnalyzer(LLMBasedAnalyzer):
    """Analyzer that performs LLM-based code analysis."""

    def __init__(self):
        super().__init__()
        # Initialize centralized AST index for lazy loading
        self.ast_index = RepoAstIndex()

    def name(self) -> str:
        return "CodeAnalyzer"

    def initialize(self, config: Mapping[str, Any]) -> None:
        """Setup, load models, and prepare for analysis."""
        super().initialize(config)
        # Initialize AST index (actual loading is lazy)
        self._initialize_ast_index()

    def _initialize_ast_index(self) -> None:
        """
        Initialize AST index for centralized loading.
        The actual loading is now handled lazily by RepoAstIndex.
        """
        try:
            # Validate that AST has been built before analysis
            self.ast_index.validate_ast_built()
            self.logger.debug("AST index initialized and validated")
        except RuntimeError as e:
            self.logger.warning(f"AST validation failed: {e}")
            # Don't fail initialization - let individual property access handle missing files

    def analyze_function(self, func_record: Mapping[str, Any]) -> Optional[Mapping[str, Any]]:
        """Analyze a single function record using LLM."""
        if not self._initialized:
            raise RuntimeError("Analyzer not initialized. Call initialize() first.")

        try:
            # Rate limiting - wait if necessary for HTTP-based LLM providers
            self._wait_for_rate_limit()

            # Create a temporary file for this analysis
            with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as temp_file:
                json.dump(dict(func_record), temp_file, indent=2)
                temp_input_path = temp_file.name

            # Create output file path
            with tempfile.NamedTemporaryFile(mode='w', suffix='_analysis.json', delete=False) as temp_output:
                temp_output_path = temp_output.name

            try:
                # Create AnalysisConfig
                analysis_config = AnalysisConfig(
                    json_file_path=temp_input_path,
                    api_key=self.api_key,
                    api_url=self.config.get('api_end_point', DEFAULT_LLM_API_END_POINT),
                    model=self.config.get('model', DEFAULT_LLM_MODEL),
                    repo_path=self.repo_path,
                    output_file=temp_output_path,
                    max_tokens=DEFAULT_MAX_TOKENS,
                    temperature=DEFAULT_TEMPERATURE,
                    processed_cache_file=None,  # Using new publisher-subscriber caching system
                    config=self.config,
                    file_content_provider=self.file_content_provider,
                    file_filter=self.config.get('file_filter', []),
                    min_function_body_length=self.config.get('min_function_body_length', 7)
                )

                # Create CodeAnalysis instance with pre-loaded data
                code_analysis = CodeAnalysis(analysis_config)

                # Override the AST index with our cached instance to avoid reloading
                code_analysis.ast_index = self.ast_index

                # Store the analysis instance for token tracking
                self._last_analysis = code_analysis

                success = code_analysis.run_analysis()

                if success:
                    # Read the result
                    try:
                        with open(temp_output_path, 'r', encoding='utf-8') as f:
                            result = json.load(f)

                        # Handle new schema format - extract results array
                        if result and isinstance(result, dict) and 'results' in result:
                            # New schema format
                            actual_results = result['results']
                            # Post-process each result item
                            if isinstance(actual_results, list):
                                processed_results = [self._post_process_analysis_result(item) if isinstance(item, dict) else item for item in actual_results]
                            else:
                                processed_results = self._post_process_analysis_result(actual_results) if isinstance(actual_results, dict) else actual_results
                            return processed_results
                        else:
                            # Legacy format - handle as before
                            if result and isinstance(result, dict):
                                result = self._post_process_analysis_result(result)
                            elif result and isinstance(result, list):
                                result = [self._post_process_analysis_result(item) if isinstance(item, dict) else item for item in result]
                            return result
                    except (FileNotFoundError, json.JSONDecodeError):
                        return None
                else:
                    return None

            finally:
                # Clean up temporary files
                try:
                    os.unlink(temp_input_path)
                    os.unlink(temp_output_path)
                except OSError:
                    pass

        except Exception as e:
            # Log error but don't raise to maintain interface contract
            self.logger.error(f"Error during function analysis: {e}")
            self.logger.error(f"Function record keys: {list(func_record.keys()) if isinstance(func_record, dict) else 'Not a dict'}")
            self.logger.error(f"Full traceback: {traceback.format_exc()}")
            return None

    def finalize(self) -> None:
        """Cleanup after analysis."""
        pass

    def set_publisher(self, publisher) -> None:
        """
        Set the publisher for result checking.

        Args:
            publisher: The CodeAnalysisResultsPublisher instance
        """
        # Store publisher for potential future use
        self.publisher = publisher
        # The actual caching logic is handled at the runner level
        self.logger.debug("Publisher set on CodeAnalyzer")

    def _post_process_analysis_result(self, result: Dict[str, Any]) -> Dict[str, Any]:
        """
        Post-process analysis result to ensure required fields are properly set.

        Args:
            result: Analysis result dictionary

        Returns:
            Dict[str, Any]: Post-processed result
        """
        if not isinstance(result, dict):
            return result

        # No longer setting file_name field as it's been removed from the database schema
        # The file_path field contains the full path information needed

        return result

    def pull_results_from_directory(self, artifacts_dir: str) -> Dict[str, Any]:
        """
        Pull code analysis results from the provided artifacts directory.

        Args:
            artifacts_dir: Path to the artifacts directory containing analysis results

        Returns:
            Dictionary containing:
            - 'results': List of code analysis results
            - 'statistics': Dictionary with statistics about the results
            - 'summary': Dictionary with summary information
        """
        # For code analyzer, results are stored in results/code_analysis/ subdirectory
        code_analysis_dir = os.path.join(artifacts_dir, "results", "code_analysis")

        # Use the base implementation with code analysis specific directory
        result = self._read_analysis_results(code_analysis_dir, ANALYSIS_FILE_SUFFIX)

        # Add code analyzer specific information to summary
        result['summary']['analyzer_type'] = 'code_analysis'
        result['summary']['analysis_directory'] = code_analysis_dir

        return result


class CodeAnalysisRunner(UnifiedIssueFilterMixin, ReportGeneratorMixin, AnalysisRunner):
    """Main runner class for LLM code analysis.
    
    Uses UnifiedIssueFilterMixin for shared issue filter initialization.
    Uses ReportGeneratorMixin for shared report generation functionality.
    """

    def __init__(self):
        """
        Initialize the runner with logging setup.
        """
        super().__init__()
        self.file_filter = []
        self.filtered_functions = []  # List of function names to analyze
        self.filtered_classes = []    # List of class names to analyze
        self.verified_functions = set()  # Set of function names from function_filter JSON

        # Initialize attributes that may be set later
        self.analyzer_instance = None  # Will be set during analysis
        self.force_in_process_ast = False  # Force AST generation to run in-process

        # Initialize publisher-subscriber system
        self._subscribers = []  # List to hold multiple subscribers
        
        # Unified issue filter (initialized when needed)
        self.unified_issue_filter = None

    def _get_function_line_count(self, json_data: Dict[str, Any]) -> int:
        """
        Extract the line count of a function from JSON data.

        Args:
            json_data: The JSON data for a function to analyze

        Returns:
            int: Number of lines in the function, or 0 if not determinable
        """
        try:
            # Try different possible locations for line count information
            line_count = 0

            # Check for direct line count fields
            if 'line_count' in json_data:
                line_count = json_data['line_count']
            elif 'lines' in json_data:
                line_count = json_data['lines']
            elif 'num_lines' in json_data:
                line_count = json_data['num_lines']

            # Check in context
            elif 'context' in json_data and isinstance(json_data['context'], dict):
                context = json_data['context']
                line_count = (
                    context.get('line_count', 0) or
                    context.get('lines', 0) or
                    context.get('num_lines', 0)
                )

            # Check in function data
            elif 'function' in json_data and isinstance(json_data['function'], dict):
                func = json_data['function']
                line_count = (
                    func.get('line_count', 0) or
                    func.get('lines', 0) or
                    func.get('num_lines', 0)
                )

                # Also check function's context
                if line_count == 0 and 'context' in func and isinstance(func['context'], dict):
                    func_context = func['context']
                    line_count = (
                        func_context.get('line_count', 0) or
                        func_context.get('lines', 0) or
                        func_context.get('num_lines', 0)
                    )

            # Try to calculate from start_line and end_line if available
            if line_count == 0:
                start_line = None
                end_line = None

                # Check different locations for line numbers
                for location in [json_data, json_data.get('context', {}), json_data.get('function', {})]:
                    if isinstance(location, dict):
                        if start_line is None:
                            start_line = location.get('start_line') or location.get('startLine')
                        if end_line is None:
                            end_line = location.get('end_line') or location.get('endLine')

                        if start_line is not None and end_line is not None:
                            break

                if start_line is not None and end_line is not None:
                    line_count = max(0, end_line - start_line + 1)

            return max(0, int(line_count)) if line_count else 0

        except Exception as e:
            self.logger.debug(f"Error extracting line count from JSON data: {e}")
            return 0

    def _should_analyze_function(self, json_data: Dict[str, Any], config: dict) -> bool:
        """
        Check if a function should be analyzed based on LLM analysis filtering logic.
        This implements the filtering precedence: function-filter > file-filter > include_directories > exclude_directories

        Args:
            json_data: The JSON data for a function/file to analyze
            config: Configuration dictionary containing filtering parameters

        Returns:
            bool: True if the function should be analyzed, False otherwise
        """
        # Step 0: Check if --function-filter is provided with a JSON file. If so, use only verified functions
        if self.verified_functions:
            return self._should_analyze_function_by_verified_list(json_data)

        # Step 1: Check if --file-filter is provided. If so, use only file filter logic
        if self.file_filter:
            return self._should_analyze_function_by_file_filter(json_data)

        # Step 2: Check function length requirements (both minimum and maximum)
        min_function_body_length = config.get('min_function_body_length', MIN_FUNCTION_BODY_LENGTH)
        function_line_count = self._get_function_line_count(json_data)

        # Extract function name for logging
        function_name = (
            json_data.get('name') or
            json_data.get('function_name') or
            'Unknown'
        )

        if function_line_count > 0 and function_line_count < min_function_body_length:
            self.logger.debug(f"Skipping function '{function_name}' - only {function_line_count} lines (minimum: {min_function_body_length})")
            return False

        if function_line_count > MAX_FUNCTION_BODY_LENGTH:
            self.logger.debug(f"Skipping function '{function_name}' - {function_line_count} lines exceeds maximum ({MAX_FUNCTION_BODY_LENGTH})")
            return False

        # Step 3: Apply include_directories, exclude_directories, and exclude_files logic
        return self._should_analyze_function_by_directory_filters(json_data, config)

    def _should_analyze_function_by_verified_list(self, json_data: Dict[str, Any]) -> bool:
        """
        Check if a function should be analyzed based on the verified functions list from function_filter JSON.

        Args:
            json_data: The JSON data for a function/file to analyze

        Returns:
            bool: True if the function is in the verified functions list, False otherwise
        """
        # Extract function name from the JSON data
        function_name = (
            json_data.get('name')
            or json_data.get('function_name')
            or json_data.get('function')  # Extract function name from nested function data
        )

        if function_name and function_name in self.verified_functions:
            self.logger.debug(f"✓ Function '{function_name}' found in verified functions list")
            return True
        else:
            if function_name:
                self.logger.debug(f"✗ Function '{function_name}' not in verified functions list")
            else:
                self.logger.debug("✗ Could not extract function name from JSON data")
            return False

    def _should_analyze_function_by_file_filter(self, json_data: Dict[str, Any]) -> bool:
        """
        Check if a function should be analyzed based on the --file-filter argument.
        This uses the existing filtered lists logic.

        Args:
            json_data: The JSON data for a function/file to analyze

        Returns:
            bool: True if the function should be analyzed, False otherwise
        """
        # If we have filtered lists, check if this function/class is in them
        if self.filtered_functions or self.filtered_classes:
            # Extract function/class name from the JSON data
            function_name = None
            class_name = None

            # Check different possible structures in the JSON data
            function_name = (
                json_data.get('name')
                or json_data.get('function_name')
                or json_data.get('function')  # Extract function name from nested function data
                )

            # Check for class names
            class_name = (
                json_data.get('class_name')
                or json_data.get('className')
                or json_data.get('data_type_name')
)

            # Check if function or class is in our filtered lists
            if function_name and function_name in self.filtered_functions:
                self.logger.debug(f"✓ Function '{function_name}' found in filtered functions list")
                return True
            if class_name and class_name in self.filtered_classes:
                self.logger.debug(f"✓ Class '{class_name}' found in filtered classes list")
                return True

            # If we have filtered lists but this item is not in them, fall back to file-based filtering
            # This ensures we don't miss files due to function/class name mismatches
            self.logger.debug(f"Function/class name not found in filtered lists (function: '{function_name}', class: '{class_name}'), falling back to file-based filtering")

        # Always fall back to file-based filtering if function/class name matching didn't succeed
        return self._should_analyze_function_by_file(json_data)

    def _should_analyze_function_by_directory_filters(self, json_data: Dict[str, Any], config: dict) -> bool:
        """
        Check if a function should be analyzed based on include_directories, exclude_directories, and exclude_files.
        Uses the unified filtering method from FilteredFileFinder to ensure consistent behavior.

        Args:
            json_data: The JSON data for a function/file to analyze
            config: Configuration dictionary containing filtering parameters

        Returns:
            bool: True if the function should be analyzed, False otherwise
        """
        # Extract file path from the JSON data
        file_path = self._extract_file_path_from_json(json_data)
        if not file_path:
            self.logger.debug(f"Could not extract file path from JSON data, including by default")
            return True

        # Normalize file path
        normalized_file_path = file_path.lstrip('./')

        # Get filtering parameters from config
        include_directories = config.get('include_directories', [])
        exclude_directories = config.get('exclude_directories', [])
        exclude_files = config.get('exclude_files', [])

        # Use the unified filtering method from FilteredFileFinder
        result = FilteredFileFinder.should_analyze_by_directory_filters(
            normalized_file_path,
            include_directories,
            exclude_directories,
            exclude_files
        )

        if result:
            self.logger.debug(f"✓ File {normalized_file_path} passed all directory filters")
        else:
            self.logger.debug(f"✗ File {normalized_file_path} excluded by directory filters")

        return result

    def _extract_file_path_from_json(self, json_data: Dict[str, Any]) -> str:
        """
        Extract file path from JSON data, checking multiple possible locations.

        Args:
            json_data: The JSON data for a function/file to analyze

        Returns:
            str: File path or None if not found
        """
        # Try direct / top-level contexts
        file_path = (
            json_data.get('file')
            or (json_data.get('context', {}).get('file')
                if isinstance(json_data.get('context'), dict) else None)
            or (json_data.get('fileContext', {}).get('file')
                if isinstance(json_data.get('fileContext'), dict) else None)
        )

        # Nested function data
        if not file_path and isinstance(json_data.get('function'), dict):
            func = json_data['function']
            file_path = (
                (func.get('context', {}).get('file')
                if isinstance(func.get('context'), dict) else None)
                or func.get('file')
            )

        # Invoking list (first item's context)
        if (not file_path and isinstance(json_data.get('invoking'), list)
                and json_data['invoking']):
            first = json_data['invoking'][0]
            if isinstance(first, dict):
                ctx = first.get('context')
                if isinstance(ctx, dict):
                    file_path = ctx.get('file')

        return file_path

    def _should_analyze_function_by_file(self, json_data: Dict[str, Any]) -> bool:
        """
        Fallback method to check if a function should be analyzed based on file path.

        Args:
            json_data: The JSON data for a function/file to analyze

        Returns:
            bool: True if the function should be analyzed, False otherwise
        """
        # Extract file path from the JSON data - check multiple possible locations
        file_path = None

        # Try direct / top-level contexts
        file_path = (
            json_data.get('file')
            or (json_data.get('context', {}).get('file')
                if isinstance(json_data.get('context'), dict) else None)
            or (json_data.get('fileContext', {}).get('file')
                if isinstance(json_data.get('fileContext'), dict) else None)
        )

        # Nested function data
        if not file_path and isinstance(json_data.get('function'), dict):
            func = json_data['function']
            file_path = (
                (func.get('context', {}).get('file')
                if isinstance(func.get('context'), dict) else None)
                or func.get('file')
            )

        # Invoking list (first item’s context)
        if (not file_path and isinstance(json_data.get('invoking'), list)
                and json_data['invoking']):
            first = json_data['invoking'][0]
            if isinstance(first, dict):
                ctx = first.get('context')
                if isinstance(ctx, dict):
                    file_path = ctx.get('file')

        if not file_path:
            self.logger.debug(f"Could not extract file path from JSON data keys: {list(json_data.keys())}")
            if 'context' in json_data:
                self.logger.debug(
                    f"Context keys: {list(json_data['context'].keys()) if isinstance(json_data['context'], dict) else 'Not a dict'}"
                )
            self.logger.debug(f"Full JSON structure: {json.dumps(json_data, indent=2)[:500]}...")
            return True

        # Normalize file paths for comparison (remove leading ./ and handle relative paths)
        normalized_file_path = file_path.lstrip('./')

        # Debug logging to understand the matching process
        self.logger.debug(f"Extracted file path: '{file_path}' -> normalized: '{normalized_file_path}'")
        self.logger.debug(f"File filter contains {len(self.file_filter)} files: {self.file_filter[:3]}..." if len(self.file_filter) > 3 else f"File filter: {self.file_filter}")

        # Check if the file is in our filter list
        for filter_file in self.file_filter:
            normalized_filter_file = filter_file.lstrip('./')
            self.logger.debug(f"Comparing '{normalized_file_path}' with filter '{normalized_filter_file}'")
            if normalized_file_path == normalized_filter_file or normalized_file_path.endswith('/' + normalized_filter_file):
                self.logger.debug(f"✓ File {normalized_file_path} matches filter {normalized_filter_file}")
                return True

        self.logger.debug(f"✗ File {normalized_file_path} does not match any filter in file_filter")
        return False

    def _process_function_entry(self, func_entry: Dict, repo_path: str) -> Dict:
        """
        Process single function entry (extracted from ASTCallGraphParser logic).

        Args:
            func_entry: Function entry from call graph
            repo_path: Repository path

        Returns:
            Dict: Processed function entry ready for analysis
        """
        # Extract primary function information
        function_name = func_entry.get('function', '')
        context = func_entry.get('context', {})
        primary_file = context.get('file', '')


        # Skip if no valid file
        if not primary_file:
            return {}

        # Handle functions_invoked (new format - just a list of function names)
        functions_invoked = func_entry.get('functions_invoked', [])

        # Extract data_types_used from the entry
        data_types_used = func_entry.get('data_types_used', [])

        # Extract constants_used from the entry
        constants_used = func_entry.get('constants_used', {})

        # Extract invoked_by (caller functions) from the entry
        invoked_by = func_entry.get('invoked_by', [])

        # Extract function context for primary function
        function_context = extract_function_context(func_entry, repo_path, True)


        # Build result structure
        result = {
            'function': function_name,
            'code': function_context,  # Add the actual function code here
            'context': {
                'file': primary_file,
                'start': context.get('start'),
                'end': context.get('end'),
                'function_context': function_context
            }
        }


        # Add functions_invoked if it's not empty
        if functions_invoked:
            result['functions_invoked'] = functions_invoked

        # Add data_types_used if it's not empty
        if data_types_used:
            result['data_types_used'] = data_types_used

        # Add constants_used if it's not empty
        if constants_used:
            result['constants_used'] = constants_used

        # Add invoked_by (caller functions) if it's not empty
        if invoked_by:
            result['invoked_by'] = invoked_by

        return result

    def _generate_temp_function_file(self, func_entry: Dict, repo_path: str) -> str:
        """
        Generate temporary function INPUT file only when needed.
        This file is used as input to the LLM analysis and is deleted after analysis.
        The analysis RESULT files are preserved for caching purposes.

        Args:
            func_entry: Function entry from call graph
            repo_path: Repository path

        Returns:
            str: Path to temporary INPUT file (will be deleted after analysis)
        """
        # Process the function entry
        processed_entry = self._process_function_entry(func_entry, repo_path)
        if not processed_entry:
            return ""

        # Create unique filename
        function_name = processed_entry.get('function', 'unknown')
        primary_file = processed_entry.get('context', {}).get('file', 'unknown')

        # Create safe filename using same logic as output filename
        safe_function_name = "".join(c for c in function_name if c.isalnum() or c in ('_', '-'))
        safe_file_name = "".join(c for c in os.path.basename(primary_file) if c.isalnum() or c in ('_', '-', '.'))

        # Truncate function name and file name to prevent filesystem length issues
        # Most filesystems have a 255 character limit, so we'll be conservative
        max_function_name_length = 100
        max_file_name_length = 50
        
        if len(safe_function_name) > max_function_name_length:
            safe_function_name = safe_function_name[:max_function_name_length]
            
        if len(safe_file_name) > max_file_name_length:
            safe_file_name = safe_file_name[:max_file_name_length]

        # Use deterministic hash (same as output filename logic)
        file_hash = HashUtil.hash_for_file_identifier_md5(primary_file, truncate_length=8)
        temp_filename = f"{safe_function_name}_{safe_file_name}_{file_hash}.json"

        temp_path = os.path.join(self.processed_output_dir, temp_filename)

        # Write processed entry to temp file
        try:
            with open(temp_path, 'w', encoding='utf-8') as f:
                json.dump(processed_entry, f, indent=2, ensure_ascii=False)
            return temp_path
        except Exception as e:
            self.logger.error(f"Failed to create temp file {temp_path}: {e}")
            return ""

    def _process_call_graph(self, config: dict, nested_call_graph_path: str, _output_base_dir: str = None) -> tuple:
        """Validate call graph exists and prepare for on-demand processing."""
        self.logger.info("Validating call graph for on-demand processing...")

        # Extract configuration values
        ast_call_graph_dir = config['astCallGraphDir']
        # repo_path = config['path_to_repo']  # Unused variable

        # Ensure analysis_input directory exists
        self.processed_output_dir = os.path.join(os.path.dirname(ast_call_graph_dir), PROCESSED_OUTPUT_DIR)
        os.makedirs(self.processed_output_dir, exist_ok=True)

        # Validate call graph file exists
        if not os.path.exists(nested_call_graph_path):
            self.logger.error(f"Call graph file not found: {nested_call_graph_path}")
            return [], {}

        # Load and validate call graph structure
        call_graph_data = read_json_file(nested_call_graph_path)
        if not call_graph_data or 'call_graph' not in call_graph_data:
            self.logger.error(f"Invalid call graph structure in: {nested_call_graph_path}")
            return [], {}

        # Count total functions for reporting
        total_functions = 0
        for file_entry in call_graph_data['call_graph']:
            functions = file_entry.get('functions', [])
            total_functions += len(functions)

        self.logger.info(f"Call graph validated: {total_functions} functions available for on-demand processing")

        # Store call graph data for on-demand processing
        self.call_graph_data = call_graph_data

        # Return empty results since we're not pre-processing
        return [], {"on_demand_ready": True, "total_functions": total_functions}

    def _build_filtered_lists(self, config: dict) -> None:
        """
        Build filtered lists of functions and classes using FilterByFileUtil.
        This should be called early in the process when file_filter is provided.
        """
        if not self.file_filter:
            self.logger.info("No file filter provided, will analyze all functions and classes")
            return

        self.logger.info(f"Building filtered lists for {len(self.file_filter)} files")

        # Filter by supported extensions
        filtered_files, unsupported_files = [], []
        for fp in self.file_filter:
            ext = os.path.splitext(fp)[1].lower()
            (filtered_files if ext in ALL_SUPPORTED_EXTENSIONS
            else unsupported_files).append(fp if ext in ALL_SUPPORTED_EXTENSIONS else f"{fp} (extension: {ext})")

        if unsupported_files:
            unsupported_list = "\n".join(f"  - {info}" for info in unsupported_files)
            self.logger.warning(
                f"Skipping {len(unsupported_files)} files with unsupported extensions:\n{unsupported_list}"
            )

        self.file_filter = filtered_files
        if not self.file_filter:
            self.logger.warning("No files with supported extensions found after filtering")
            return

        files_list = "\n".join(f"  - {fp}" for fp in self.file_filter)
        self.logger.info(
            f"Files to analyze after extension filtering ({len(self.file_filter)} files):\n{files_list}"
        )

        # AST paths
        ast_call_graph_dir = config['astCallGraphDir']
        merged_functions_path = os.path.join(ast_call_graph_dir, MERGED_SYMBOLS_FILE)
        defined_classes_path = os.path.join(ast_call_graph_dir, MERGED_DEFINED_CLASSES_FILE)

        # Existence checks
        if not os.path.exists(merged_functions_path):
            self.logger.warning(f"Merged functions file not found: {merged_functions_path}")
            self.logger.warning("Will fall back to file-based filtering")
            return

        if not os.path.exists(defined_classes_path):
            self.logger.warning(f"Defined classes file not found: {defined_classes_path}")
            self.logger.warning("Will skip class filtering")

        # Build filtered lists
        try:
            self.filtered_functions = FilterByFileUtil.get_functions_by_files(
                merged_functions_path, self.file_filter
            )
            self.logger.info(f"Found {len(self.filtered_functions)} functions in filtered files")

            if os.path.exists(defined_classes_path):
                self.filtered_classes = FilterByFileUtil.get_classes_by_files(
                    defined_classes_path, self.file_filter
                )
                self.logger.info(f"Found {len(self.filtered_classes)} classes in filtered files")

            if self.filtered_functions:
                self.logger.debug(f"Filtered functions: {self.filtered_functions}")
            if self.filtered_classes:
                self.logger.debug(f"Filtered classes: {self.filtered_classes}")

        except Exception as e:
            self.logger.error(f"Error building filtered lists: {e}")
            self.logger.warning("Will fall back to file-based filtering")

    def _load_verified_functions(self, function_filter_path: str) -> None:
        """
        Load the list of verified functions from function_filter JSON file.

        Args:
            function_filter_path: Path to the function_filter JSON file
        """
        if not function_filter_path or not os.path.exists(function_filter_path):
            self.logger.warning(f"Function filter file not found: {function_filter_path}")
            return

        try:
            with open(function_filter_path, 'r', encoding='utf-8') as f:
                functions_data = json.load(f)

            # Extract function names from the functions_modified structure
            functions_modified = functions_data.get('functions_modified', {})
            self.verified_functions = set(functions_modified.keys())

            self.logger.info(f"Loaded {len(self.verified_functions)} verified functions from {function_filter_path}")
            if self.verified_functions:
                self.logger.debug(f"Verified functions: {list(self.verified_functions)[:10]}..." if len(self.verified_functions) > 10 else f"Verified functions: {list(self.verified_functions)}")

        except Exception as e:
            self.logger.error(f"Error loading verified functions from {function_filter_path}: {e}")
            self.verified_functions = set()

    def add_results_subscriber(self, subscriber) -> None:
        """
        Add a subscriber to receive code analysis results.
        This should be called before running analysis.

        Args:
            subscriber: A subscriber implementing CodeAnalysisSubscriber interface
        """
        self._subscribers.append(subscriber)
        self.logger.info(f"Added subscriber: {type(subscriber).__name__}")


    def set_user_provided_prompts(self, user_prompts: list) -> None:
        """
        Set multiple user-provided prompts to be included in the system prompt for code analysis.
        
        Args:
            user_prompts: List of user-specific instructions for analysis
        """
        self.user_provided_prompts = []
        if user_prompts and isinstance(user_prompts, list):
            for prompt in user_prompts:
                if prompt and prompt.strip():
                    self.user_provided_prompts.append(prompt.strip())
            self.logger.info(f"Set {len(self.user_provided_prompts)} user-provided prompts")
        else:
            self.logger.info("User-provided prompts cleared")

    def clear_user_provided_prompts(self) -> None:
        """Clear all user-provided prompts."""
        self.user_provided_prompts = []
        self.logger.info("User-provided prompts cleared")

    # _initialize_unified_issue_filter is now provided by UnifiedIssueFilterMixin

    def _initialize_publisher_subscriber(self, config: dict, output_base_dir: str) -> None:
        """
        Initialize the publisher-subscriber system for code analysis results.

        Args:
            config: Configuration dictionary
            output_base_dir: Base output directory
        """
        # Extract repository name from path
        repo_path = config['path_to_repo']
        repo_name = os.path.basename(repo_path.rstrip('/'))

        # Initialize publisher only if not already initialized, or preserve existing stores
        if not self.results_publisher:
            self.results_publisher = CodeAnalysisResultsPublisher()
        else:
            # Publisher already exists with registered stores - preserve them
            self.logger.info(f"Publisher already initialized with {len(self.results_publisher._prior_result_stores)} prior result stores - preserving existing stores")

        self.results_publisher.initialize(output_base_dir)

        # Subscribe all registered subscribers to the publisher
        for subscriber in self._subscribers:
            self.results_publisher.subscribe(subscriber)
            self.logger.info(f"Subscribed {type(subscriber).__name__} to publisher")

        # If we have a file system subscriber, load existing results for caching
        # Note: Category filtering is applied later during the analysis loop when results are republished
        # This ensures consistent filtering behavior for both cached and newly analyzed results
        for subscriber in self._subscribers:
            if hasattr(subscriber, 'load_existing_results'):
                loaded_count = subscriber.load_existing_results(repo_name, self.results_publisher)
                if loaded_count > 0:
                    self.logger.info(f"Loaded {loaded_count} existing analysis results for checksum-based caching via {type(subscriber).__name__}")

        self.logger.info(f"Initialized publisher-subscriber system for repository: {repo_name}")

    def _initialize_publisher_subscriber_for_report(self, config: dict, output_base_dir: str) -> None:
        """
        Initialize the publisher-subscriber system for report generation from existing issues.
        Unlike _initialize_publisher_subscriber(), this method loads results directly into
        the publisher's results collection so they are available via get_results().

        Args:
            config: Configuration dictionary
            output_base_dir: Base output directory
        """
        # Extract repository name from path
        repo_path = config['path_to_repo']
        repo_name = os.path.basename(repo_path.rstrip('/'))

        # Initialize publisher only if not already initialized
        if not self.results_publisher:
            self.results_publisher = CodeAnalysisResultsPublisher()
        else:
            self.logger.info(f"Publisher already initialized with {len(self.results_publisher._prior_result_stores)} prior result stores - preserving existing stores")

        self.results_publisher.initialize(output_base_dir)

        # Subscribe all registered subscribers to the publisher
        for subscriber in self._subscribers:
            self.results_publisher.subscribe(subscriber)
            self.logger.info(f"Subscribed {type(subscriber).__name__} to publisher")

        # Load existing results directly into the publisher's results collection for report generation
        # This is different from _initialize_publisher_subscriber which only indexes for cache lookups
        for subscriber in self._subscribers:
            if hasattr(subscriber, 'load_existing_results_for_report'):
                loaded_count = subscriber.load_existing_results_for_report(repo_name, self.results_publisher)
                if loaded_count > 0:
                    self.logger.info(f"Loaded {loaded_count} existing analysis results for report generation via {type(subscriber).__name__}")

        self.logger.info(f"Initialized publisher-subscriber system for report generation: {repo_name}")

    def _run_code_analysis(self, config: dict, output_base_dir: str, api_key: str = None) -> tuple:
        """Run code analysis with on-demand file generation from call graph."""
        self.logger.info("Starting on-demand code analysis...")

        # Initialize centralized token tracker if not already set
        if not self.token_tracker:
            llm_provider_type = get_llm_provider_type(config)
            self.token_tracker = TokenTracker(llm_provider_type)
            self.logger.info(f"Auto-initialized centralized token tracker for provider: {llm_provider_type}")

        # Initialize publisher-subscriber system
        self._initialize_publisher_subscriber(config, output_base_dir)
        
        # Initialize unified issue filter
        self._initialize_unified_issue_filter(api_key, config)

        # Check if call graph data is available
        if not hasattr(self, 'call_graph_data') or not self.call_graph_data:
            self.logger.error("No call graph data available for on-demand processing")
            return 0, 0

        # Extract configuration values
        repo_path = config['path_to_repo']
        num_functions_to_analyze = config.get('num_functions_to_analyze', DEFAULT_NUM_FUNCTIONS_TO_ANALYZE)

        # Create LLM analysis output directory under results/
        results_dir = self.get_results_directory()
        code_analysis_dir = f"{results_dir}/code_analysis"
        os.makedirs(code_analysis_dir, exist_ok=True)

        # Set the analysis directory in config
        config['analysis_dir'] = code_analysis_dir

        # Pre-filter and sort all functions before processing
        self.logger.info("Pre-filtering and sorting functions by length...")
        filtered_functions = []

        for file_entry in self.call_graph_data['call_graph']:
            functions = file_entry.get('functions', [])

            for func_entry in functions:
                # Add file information to function entry if missing
                if 'context' not in func_entry:
                    func_entry['context'] = {}
                if 'file' not in func_entry['context']:
                    func_entry['context']['file'] = file_entry.get('file', '')

                # Apply filtering logic
                if self._should_analyze_function(func_entry, config):
                    # Calculate function length for sorting
                    function_length = self._get_function_line_count(func_entry)
                    filtered_functions.append({
                        'func_entry': func_entry,
                        'file_entry': file_entry,
                        'length': function_length
                    })

        # Sort by function length (longest first)
        filtered_functions.sort(key=lambda x: x['length'], reverse=True)

        total_functions = len(filtered_functions)
        self.logger.info(f"After filtering: {total_functions} functions to process (sorted by length, longest first)")

        if total_functions == 0:
            self.logger.warning("No functions passed filtering criteria")
            return 0, 0

        # Count existing results to determine how many new functions to analyze
        existing_results_count = 0
        if self.results_publisher:
            repo_name = os.path.basename(repo_path.rstrip('/'))
            existing_results = self.results_publisher.get_results(repo_name)
            existing_results_count = len(existing_results) if existing_results else 0
            self.logger.info(f"Found {existing_results_count} existing analysis results")

        # Calculate how many new functions we can analyze
        remaining_to_analyze = max(0, num_functions_to_analyze - existing_results_count)
        
        if remaining_to_analyze == 0:
            self.logger.info(f"Already have {existing_results_count} results, which meets or exceeds the limit of {num_functions_to_analyze}. No new analysis needed.")
            return existing_results_count, 0
        elif remaining_to_analyze < total_functions:
            self.logger.info(f"Limiting analysis to {remaining_to_analyze} functions (have {existing_results_count} existing, limit is {num_functions_to_analyze})")
            filtered_functions = filtered_functions[:remaining_to_analyze]
            total_functions = len(filtered_functions)
        else:
            self.logger.info(f"Will analyze {total_functions} functions (have {existing_results_count} existing, limit is {num_functions_to_analyze})")

        # Initialize analyzer
        llm_provider_type = get_llm_provider_type(config)
        if llm_provider_type == "dummy":
            analyzer: AnalyzerProtocol = DummyCodeAnalyzer()
            self.logger.info("Using DummyCodeAnalyzer for analysis (llm_provider_type=dummy)")
        else:
            analyzer: AnalyzerProtocol = CodeAnalyzer()
            self.logger.info(f"Using CodeAnalyzer for analysis (llm_provider_type={llm_provider_type})")

        # Initialize analyzer with configuration values
        # Note: file_filter and min_function_body_length are NOT passed because
        # all filtering has already been applied during pre-filtering stage
        analyzer_config = {
            'api_key': api_key,
            'repo_path': repo_path,
            'file_content_provider': self.get_file_content_provider() if hasattr(self, 'get_file_content_provider') else None,
            'api_end_point': config.get('api_end_point', DEFAULT_LLM_API_END_POINT),
            'model': config.get('model', DEFAULT_LLM_MODEL),
            'output_base_dir': output_base_dir,
            'user_provided_prompts': self.user_provided_prompts  # Pass user-provided prompts to analyzer
        }
        analyzer_config.update(config)  # Add all config values

        analyzer.initialize(analyzer_config)
        self.analyzer_instance = analyzer

        # Set publisher on analyzer for result checking if it's a CodeAnalyzer
        if hasattr(analyzer, 'set_publisher') and self.results_publisher:
            analyzer.set_publisher(self.results_publisher)

        if not api_key and llm_provider_type != "dummy":
            self.logger.warning("No API key available from config or other ways")
            self.logger.info("Skipping code analysis due to missing API key")
            return 0, 0

        successful_analyses = 0
        failed_analyses = 0

        # Process each filtered function (already sorted by length, longest first)
        for i, func_data in enumerate(filtered_functions, 1):
            # Check for cancellation every N functions
            if i > 1 and (i - 1) % self._cancellation_check_interval == 0:
                if not self._should_continue():
                    self.logger.info(f"Analysis cancelled at function {i}/{total_functions}")
                    self.logger.info(f"Processed {successful_analyses} successfully, {failed_analyses} failed before cancellation")
                    return successful_analyses, failed_analyses
            
            func_entry = func_data['func_entry']
            function_length = func_data['length']

            # Extract function information for analysis
            function_name = func_entry.get('function', 'unknown')
            primary_file = func_entry.get('context', {}).get('file', 'unknown')

            # Get function checksum directly from the func_entry (from call graph data)
            function_checksum = func_entry.get('checksum', None)
            if function_checksum and function_checksum != "None":
                # Use first 8 characters of content checksum
                checksum_hash = function_checksum[:8]
            else:
                # Fallback to file path hash if checksum not available
                checksum_hash = HashUtil.hash_for_file_identifier_md5(primary_file, truncate_length=8)

            # Check if already processed using publisher with concurrent lookup across all prior result stores
            if self.results_publisher:
                current_checksum = function_checksum if function_checksum and function_checksum != "None" else checksum_hash
                self.logger.info(f"DEBUG: [{i}/{total_functions}] Checking cache for function='{function_name}', file='{primary_file}', checksum='{current_checksum}'")

                existing_result = self.results_publisher.check_existing_result(
                    primary_file,
                    function_name,
                    current_checksum
                )

                if existing_result:
                    self.logger.info(f"[{i}/{total_functions}] ⏭️  ANALYSIS SKIPPED - Same checksum found for {function_name}")
                    self.logger.info(f"[{i}/{total_functions}] 🔍 Function: {function_name} | File: {primary_file} | Checksum: {current_checksum}")
                    if function_checksum and function_checksum != "None":
                        self.logger.info(f"[{i}/{total_functions}] ✅ Content unchanged ({function_length} lines) - reusing existing analysis result")
                    else:
                        self.logger.info(f"[{i}/{total_functions}] ✅ Already processed ({function_length} lines) - reusing existing analysis result")

                    # Republish the existing result to the current analysis session
                    repo_name = os.path.basename(repo_path.rstrip('/'))
                    if self.results_publisher and existing_result:
                        # Use centralized schema to normalize the existing result
                        try:
                            normalized_result = CodeAnalysisResultValidator.normalize_result(
                                existing_result,
                                file_path=primary_file,
                                function=function_name,
                                checksum=current_checksum
                            )

                            # Validate the normalized result
                            validation_errors = normalized_result.validate()
                            if validation_errors:
                                self.logger.warning(f"[{i}/{total_functions}] ⚠️  Existing result validation failed: {validation_errors}")
                                existing_results = []
                            else:
                                existing_results = [issue.to_dict() for issue in normalized_result.results]

                            # Apply Level 1 (Category) filtering to cached results
                            # This ensures cached results from before the filter was implemented are properly filtered
                            if self.unified_issue_filter and existing_results:
                                original_count = len(existing_results)
                                # Only apply Level 1 (Category) filtering to cached results
                                # Skip Level 2 and Level 3 (LLM-based) to avoid extra API calls for cached data
                                existing_results = self.unified_issue_filter.category_filter.filter_issues(existing_results)
                                if len(existing_results) != original_count:
                                    dropped_count = original_count - len(existing_results)
                                    self.logger.info(f"[{i}/{total_functions}] 🔍 Applied Level 1 category filter to cached results: dropped {dropped_count} issues, keeping {len(existing_results)}")

                            # Republish the existing result to the current analysis
                            self.results_publisher.add_result(
                                repo_name=repo_name,
                                file_path=primary_file,
                                function=function_name,
                                function_checksum=current_checksum,
                                results=existing_results
                            )
                            self.logger.info(f"[{i}/{total_functions}] 📤 Republished existing result with {len(existing_results)} issues to current analysis")

                        except Exception as e:
                            self.logger.warning(f"[{i}/{total_functions}] ⚠️  Failed to normalize existing result: {e}, skipping republish")

                    successful_analyses += 1
                    continue
                else:
                    self.logger.info(f"DEBUG: [{i}/{total_functions}] NO existing result found for {function_name} - will analyze")
            else:
                self.logger.info(f"DEBUG: [{i}/{total_functions}] No results publisher available - will analyze {function_name}")

            # Generate temporary function file on-demand
            temp_file_path = self._generate_temp_function_file(func_entry, repo_path)
            if not temp_file_path:
                self.logger.warning(f"[{i}/{total_functions}] Failed to generate temp file for {function_name}")
                failed_analyses += 1
                continue

            try:
                progress_msg = f"[{i}/{total_functions}]"
                self.logger.info(f"{progress_msg} Analyzing: {function_name} ({function_length} lines)")

                # Start function analysis tracking
                self._start_function_analysis_tracking(function_name)

                # Load the processed function data
                with open(temp_file_path, 'r', encoding='utf-8') as f:
                    json_data = json.load(f)

                # Use analyzer to analyze the function
                result = analyzer.analyze_function(json_data)

                if result is not None:
                    # Use centralized schema to create standardized result
                    try:
                        final_checksum = function_checksum if function_checksum and function_checksum != "None" else checksum_hash

                        # Normalize the result to ensure it's in the correct format
                        if isinstance(result, list):
                            # Filter out any invalid items and ensure all items have required fields
                            valid_issues = []
                            for item in result:
                                if isinstance(item, dict):
                                    # Ensure required fields exist with defaults
                                    if 'issue' not in item:
                                        item['issue'] = item.get('description', 'No description provided')
                                    if 'severity' not in item:
                                        item['severity'] = 'medium'  # Default severity
                                    if 'category' not in item:
                                        item['category'] = 'general'  # Default category
                                    valid_issues.append(item)
                                else:
                                    self.logger.warning(f"Skipping invalid result item: {type(item)} - {item}")
                            issues_list = valid_issues
                        elif isinstance(result, dict):
                            # Single result - ensure required fields exist
                            if 'issue' not in result:
                                result['issue'] = result.get('description', 'No description provided')
                            if 'severity' not in result:
                                result['severity'] = 'medium'  # Default severity
                            if 'category' not in result:
                                result['category'] = 'general'  # Default category
                            issues_list = [result]
                        else:
                            # Unexpected format - create a generic issue
                            self.logger.warning(f"Unexpected result format: {type(result)} - {result}")
                            issues_list = [{
                                'issue': f'Analysis completed with unexpected result format: {str(result)}',
                                'severity': 'low',
                                'category': 'general'
                            }]

                        # Create standardized result using the schema
                        standardized_result = create_result(
                            file_path=primary_file,
                            function=function_name,
                            checksum=final_checksum,
                            issues=issues_list
                        )

                        # Validate the result
                        validation_errors = standardized_result.validate()
                        if validation_errors:
                            self.logger.warning(f"[{i}/{total_functions}] ⚠️  Result validation failed: {validation_errors}")
                            success = False
                        else:
                            # Convert to dict format for publisher
                            result_issues = [issue.to_dict() for issue in standardized_result.results]

                            # Apply unified issue filter before publishing (only for new analysis results)
                            if self.unified_issue_filter and result_issues:
                                self.logger.debug(f"Applying unified issue filter to {len(result_issues)} issues")
                                
                                # Get the original function context from the processed entry
                                # The function context was stored in the temp file at line 705 in _process_function_entry
                                function_context = None
                                try:
                                    # Read the function context from the temp file that was just processed
                                    with open(temp_file_path, 'r', encoding='utf-8') as f:
                                        temp_data = json.load(f)
                                    function_context = temp_data.get('code', '')
                                    if function_context:
                                        self.logger.debug(f"Retrieved function context ({len(function_context)} chars) for Level 3 filtering")
                                    else:
                                        self.logger.debug("No function context found in temp file for Level 3 filtering")
                                except Exception as e:
                                    self.logger.warning(f"Failed to retrieve function context for Level 3 filtering: {e}")
                                    function_context = None
                                
                                filtered_issues = self.unified_issue_filter.filter_issues(result_issues, function_context)
                                
                                if len(filtered_issues) != len(result_issues):
                                    dropped_count = len(result_issues) - len(filtered_issues)
                                    self.logger.info(f"Unified issue filter: dropped {dropped_count} issues, keeping {len(filtered_issues)} issues")
                                
                                result_issues = filtered_issues
                            elif not self.unified_issue_filter:
                                self.logger.debug("Unified issue filter not available - publishing all issues")

                            # Publish result using publisher-subscriber system
                            repo_name = os.path.basename(repo_path.rstrip('/'))
                            if self.results_publisher:
                                self.results_publisher.add_result(
                                    repo_name=repo_name,
                                    file_path=primary_file,
                                    function=function_name,
                                    function_checksum=final_checksum,
                                    results=result_issues
                                )
                                success = True
                            else:
                                # Publisher not initialized - this is an error condition
                                self.logger.error("Publisher not available - cannot save analysis results")
                                success = False

                    except Exception as e:
                        self.logger.error(f"[{i}/{total_functions}] ✗ Failed to create standardized result: {e}")
                        self.logger.error(f"Result type: {type(result)}, Result: {result}")
                        success = False

                    # Use centralized token tracking
                    if llm_provider_type == "dummy":
                        input_tokens, output_tokens = self.token_tracker.record_dummy_tokens()
                    else:
                        # Get real token counts from the analyzer's last analysis instance
                        if hasattr(analyzer, '_last_analysis') and analyzer._last_analysis:
                            input_tokens, output_tokens = self.token_tracker.record_tokens_from_analysis(analyzer._last_analysis)
                        else:
                            # Fallback: no tokens recorded for failed analysis
                            input_tokens, output_tokens = 0, 0
                else:
                    # Only consider it a failure if result is None (no response or malformed response)
                    success = False
                    input_tokens, output_tokens = 0, 0
                    # Still record the failure in token tracker for consistency
                    if self.token_tracker and llm_provider_type == "dummy":
                        self.token_tracker.record_dummy_tokens()
                    # For failed real analysis, don't record tokens since no API call was made

                # Record function analysis result
                self._record_function_analysis_result(
                    functions_analyzed=1,
                    success=success,
                    function_data=function_name
                )

                if success:
                    successful_analyses += 1
                    # Check if result is empty array and log accordingly
                    if isinstance(result, list) and len(result) == 0:
                        self.logger.info(f"{progress_msg} ✓ Successfully analyzed: {function_name} ({function_length} lines) - No issues found")
                    else:
                        self.logger.info(f"{progress_msg} ✓ Successfully analyzed: {function_name} ({function_length} lines)")
                else:
                    failed_analyses += 1
                    self.logger.error(f"{progress_msg} ✗ Failed to analyze: {function_name} ({function_length} lines) - No response or malformed response received")

            except Exception as e:
                failed_analyses += 1
                self.logger.error(f"{progress_msg} ✗ Error analyzing {function_name}: {e}")

            finally:
                # Clean up temporary INPUT file only (not the analysis result file)
                if os.path.exists(temp_file_path):
                    try:
                        os.unlink(temp_file_path)
                        self.logger.debug(f"Cleaned up temporary input file: {temp_file_path}")
                    except OSError as e:
                        self.logger.warning(f"Failed to cleanup temp input file {temp_file_path}: {e}")

                # NOTE: Analysis result files in code_analysis_dir are preserved for caching
                # They allow skipping re-analysis when the program runs again on the same repository

        # Finalize analyzer
        try:
            analyzer.finalize()
            self.logger.info(f"Analyzer {analyzer.name()} finalized successfully")
        except Exception as e:
            self.logger.warning(f"Error finalizing analyzer: {e}")

        self.logger.info(f"On-demand code analysis completed. Success: {successful_analyses}, Failed: {failed_analyses}")

        # Log centralized token usage summary
        if self.token_tracker and (successful_analyses > 0 or failed_analyses > 0):
            self.token_tracker.log_summary()

        return successful_analyses, failed_analyses

    def pull_results_from_directory(self, artifacts_dir: str) -> Dict[str, Any]:
        """
        Pull code analysis results from the provided artifacts directory using the analyzer.

        Args:
            artifacts_dir: Path to the artifacts directory containing analysis results

        Returns:
            Dictionary containing:
            - 'results': List of code analysis results
            - 'statistics': Dictionary with statistics about the results
            - 'summary': Dictionary with summary information
        """
        # Create a CodeAnalyzer instance to pull results
        analyzer = CodeAnalyzer()
        return analyzer.pull_results_from_directory(artifacts_dir)

    def print_results_summary(self, results_data: Dict[str, Any]) -> None:
        """
        Print a summary of the analysis results.

        Args:
            results_data: Dictionary containing results, statistics, and summary
        """
        summary = results_data['summary']
        statistics = results_data['statistics']
        # results = results_data['results']  # Unused variable

        print("=" * 80)
        print("CODE ANALYSIS RESULTS SUMMARY")
        print("=" * 80)
        print(f"Analyzer: {summary['analyzer']}")
        print(f"Analysis Type: {summary.get('analyzer_type', 'Unknown')}")
        print(f"Directory: {summary['analysis_directory']}")
        print(f"Total Files: {summary['total_files']}")
        print(f"Files Processed: {summary['files_processed']}")
        print(f"Files with Errors: {summary['files_with_errors']}")
        print(f"Total Issues Found: {summary['total_issues']}")
        print()

        if statistics['total'] > 0:
            print("STATISTICS BY SEVERITY:")
            print("-" * 40)
            for severity, count in statistics['by_severity'].items():
                print(f"  {severity.capitalize()}: {count}")
            print()

            print("STATISTICS BY CATEGORY:")
            print("-" * 40)
            for category, count in statistics['by_category'].items():
                print(f"  {category}: {count}")
            print()

            print("TOP FILES BY ISSUE COUNT:")
            print("-" * 40)
            # Sort files by issue count and show top 10
            sorted_files = sorted(statistics['by_file'].items(), key=lambda x: x[1], reverse=True)
            for file_name, count in sorted_files[:10]:
                print(f"  {file_name}: {count} issues")
            print()

            print("TOP FUNCTIONS BY ISSUE COUNT:")
            print("-" * 40)
            # Sort functions by issue count and show top 10
            sorted_functions = sorted(statistics['by_function'].items(), key=lambda x: x[1], reverse=True)
            for function_name, count in sorted_functions[:10]:
                print(f"  {function_name}: {count} issues")
        else:
            print("No issues found in the analysis results.")

        print("=" * 80)

    def _writeback_final_issues_to_json(
        self,
        final_issues: list,
        checksum_lookup: dict,
        code_analysis_dir: str,
    ) -> None:
        """Overwrite per-function JSON files so they contain only the issues
        that survived all filtering stages (dedup + FP CSV filter + category
        filter).

        This ensures that a subsequent ``--generate-report-from-existing-issues``
        run loads exactly the same baseline as the full analysis run produced,
        rather than the pre-Level-2/3-filter snapshot that was originally
        written to disk.

        Removed issues are archived to ``dropped_issues/final_filter/`` so
        they appear in the dropped-issues HTML report and are preserved for
        audit, but will never be loaded again by the analysis cache.

        Only called after a *full* analysis run (not from
        ``generate_report_from_existing_issues``).
        """
        if not final_issues or not code_analysis_dir:
            return

        code_analysis_path = Path(code_analysis_dir)
        if not code_analysis_path.exists():
            return

        # Resolve the dropped_issues/final_filter directory (best-effort).
        drop_dir: Optional[Path] = None
        try:
            from ..utils.output_directory_provider import get_output_directory_provider
            artifacts_dir = get_output_directory_provider().get_repo_artifacts_dir()
            drop_dir = Path(artifacts_dir) / "dropped_issues" / "final_filter"
            drop_dir.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            self.logger.debug(
                "Writeback: could not create drop dir, archival skipped: %s", exc
            )
            drop_dir = None

        # Build the set of (8-char-checksum, issue_title) pairs that survived.
        surviving: set = set()
        for issue in final_issues:
            fn = issue.get("function_name", "")
            fp = issue.get("file_path", "")
            title = issue.get("issue", "")
            checksum = checksum_lookup.get((fn, fp, title), "")
            if checksum:
                surviving.add((checksum, title.strip()))

        json_files = list(code_analysis_path.glob("*_analysis.json"))
        if not json_files:
            return

        files_updated = 0
        issues_removed = 0
        for json_path in json_files:
            # Filename format: {func}_{file}_{checksum8}_analysis.json
            # stem  example : myFunc_myFile_abc12345_analysis
            stem = json_path.stem
            # Strip trailing "_analysis"
            parts = stem.rsplit("_analysis", 1)
            if len(parts) != 2 or parts[1] != "":
                continue
            before_analysis = parts[0]
            last_sep = before_analysis.rfind("_")
            if last_sep == -1:
                continue
            checksum = before_analysis[last_sep + 1:]
            if len(checksum) != 8:
                continue

            try:
                with open(json_path, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
            except Exception as exc:
                self.logger.debug(
                    "Writeback: failed to read '%s': %s", json_path.name, exc
                )
                continue

            original_results = data.get("results", [])
            if not original_results:
                continue

            kept = [
                r
                for r in original_results
                if (checksum, (r.get("issue") or "").strip()) in surviving
            ]
            dropped_in_file = [
                r
                for r in original_results
                if (checksum, (r.get("issue") or "").strip()) not in surviving
            ]
            if not dropped_in_file:
                continue

            # Archive each removed issue before altering the JSON.
            if drop_dir is not None:
                ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
                for idx, issue in enumerate(dropped_in_file):
                    try:
                        safe_title = "".join(
                            c for c in (issue.get("issue") or "")[:40]
                            if c.isalnum() or c in ("_", "-", " ")
                        ).replace(" ", "_")
                        archive_name = (
                            f"final_filter_{ts}_{checksum}_{idx}_{safe_title}.json"
                        )
                        record = {
                            "timestamp": datetime.now().isoformat(),
                            "filter_level": "Final Filter - Post-Analysis Writeback",
                            "reason": (
                                "Issue was present in per-function JSON but did not "
                                "survive the full analysis pipeline "
                                "(Level 2/3 filtering, deduplication, or FP CSV filter). "
                                "Removed during post-run writeback to keep the cache "
                                "consistent with the final report."
                            ),
                            "original_issue": issue,
                        }
                        with open(drop_dir / archive_name, "w", encoding="utf-8") as fh:
                            json.dump(record, fh, indent=2, ensure_ascii=False)
                    except Exception as exc:
                        self.logger.debug(
                            "Writeback: failed to archive dropped issue: %s", exc
                        )

            data["results"] = kept
            try:
                with open(json_path, "w", encoding="utf-8") as fh:
                    json.dump(data, fh, indent=2, ensure_ascii=False)
                files_updated += 1
                issues_removed += len(dropped_in_file)
                self.logger.debug(
                    "Writeback: '%s' removed %d issue(s), kept %d",
                    json_path.name,
                    len(dropped_in_file),
                    len(kept),
                )
            except Exception as exc:
                self.logger.warning(
                    "Writeback: failed to write '%s': %s", json_path.name, exc
                )

        if files_updated:
            self.logger.info(
                "Final-issues writeback: updated %d JSON file(s), "
                "removed %d issue(s) to reflect post-filter baseline "
                "(archived to dropped_issues/final_filter/)",
                files_updated,
                issues_removed,
            )
        else:
            self.logger.debug(
                "Final-issues writeback: no JSON files needed updating"
            )

    def _generate_report(self, config: dict, writeback_final_issues: bool = False) -> tuple:
        """Generate HTML report from analysis results."""
        self.logger.info("Starting report generation...")

        # Extract configuration values
        llm_analysis_out_dir = config['analysis_dir']
        project_name = config.get('project_name', '')
        repo_path = config['path_to_repo']

        # Check if analysis output directory exists
        if not os.path.exists(llm_analysis_out_dir):
            self.logger.warning(f"Analysis output directory not found: {llm_analysis_out_dir}")
            return False, None

        try:
            # Use publisher to get all results instead of reading files directly
            if not self.results_publisher:
                self.logger.error("Publisher not available for report generation")
                return False, None

            repo_name = os.path.basename(repo_path.rstrip('/'))
            all_results = self.results_publisher.get_results(repo_name)

            if not all_results:
                self.logger.warning("No results found in publisher")
                return False, None

            # Convert results to issues format for report generation
            all_issues = []
            # Build checksum_lookup *before* dedup so the mapping survives.
            # Key: (function_name, file_path, issue_title) -> 8-char checksum.
            checksum_lookup = {}
            for result in all_results:
                if 'results' in result and isinstance(result['results'], list):
                    checksum_8 = (result.get('checksum') or '')[:8]
                    fn = result.get('function', '')
                    for issue in result['results']:
                        k = (
                            issue.get('function_name', fn),
                            issue.get('file_path', result.get('file_path', '')),
                            issue.get('issue', ''),
                        )
                        checksum_lookup[k] = checksum_8
                    all_issues.extend(result['results'])
                else:
                    # Single result format
                    all_issues.append(result)

            self.logger.info(f"Found {len(all_issues)} total issues from publisher")

            # Deduplicate issues before report generation
            if config.get('enable_issue_deduplication', True) and all_issues:
                try:
                    from hindsight.dedupers.issue_deduper import IssueDeduper
                    from hindsight.utils.output_directory_provider import get_output_directory_provider
                    
                    # Get the repository artifacts directory
                    output_provider = get_output_directory_provider()
                    artifacts_dir = output_provider.get_repo_artifacts_dir()
                    
                    # Initialize deduper with artifacts directory
                    deduper = IssueDeduper(
                        artifacts_dir=artifacts_dir,
                        threshold=config.get('dedupe_threshold', 0.85)
                    )
                    
                    original_count = len(all_issues)
                    all_issues = deduper.dedupe(all_issues)
                    
                    dedupe_stats = deduper.get_stats()
                    self.logger.info(
                        f"Deduplication: {dedupe_stats['total_input']} issues -> "
                        f"{dedupe_stats['unique_output']} unique "
                        f"({dedupe_stats['duplicates_removed']} duplicates removed: "
                        f"{dedupe_stats['exact_matches']} exact, "
                        f"{dedupe_stats['semantic_matches']} semantic)"
                    )
                    self.logger.info(f"Vector DB stored at: {dedupe_stats['db_path']}")
                    
                    # Cleanup deduper resources
                    deduper.cleanup()
                    
                except Exception as e:
                    self.logger.warning(f"Issue deduplication failed, continuing with all issues: {e}")

            # ── FP CSV Filter (Pass 1 + Pass 2) ───────────────────────────────
            # Runs after dedup so the semantic search operates on a smaller,
            # already-deduplicated set.  Only active when
            # config['false_positives_csv'] is set (i.e. the caller passed
            # --false-positives-csv).  Completely fault-tolerant: any failure
            # leaves all_issues unchanged.
            false_positives_csv = config.get('false_positives_csv')
            if false_positives_csv and all_issues:
                try:
                    from ..issue_filter.fp_csv_filter import FpCsvFilter
                    from hindsight.utils.output_directory_provider import get_output_directory_provider

                    output_provider = get_output_directory_provider()
                    artifacts_dir = output_provider.get_repo_artifacts_dir()

                    fp_filter = FpCsvFilter(
                        csv_path=false_positives_csv,
                        code_analysis_dir=llm_analysis_out_dir,
                        artifacts_dir=artifacts_dir,
                    )
                    before_count = len(all_issues)
                    all_issues = fp_filter.filter_issues(all_issues, checksum_lookup)
                    fp_stats = fp_filter.get_stats()
                    self.logger.info(
                        "FP CSV Filter: %d -> %d issues "
                        "(%d explicit, %d semantic removed)",
                        before_count,
                        len(all_issues),
                        fp_stats["pass1_removed"],
                        fp_stats["pass2_removed"],
                    )
                except Exception as exc:
                    self.logger.warning(
                        "FP CSV Filter raised an unexpected error, "
                        "continuing with all issues: %s", exc
                    )

            # ── Write final issue set back to per-function JSONs ───────────
            # Only done after a full analysis run (writeback_final_issues=True)
            # so that future --generate-report-from-existing-issues runs start
            # from this already-filtered baseline instead of the pre-L2/L3
            # snapshot that was originally written to disk.
            if writeback_final_issues:
                self._writeback_final_issues_to_json(
                    final_issues=all_issues,
                    checksum_lookup=checksum_lookup,
                    code_analysis_dir=llm_analysis_out_dir,
                )

            file_mapping_index, _ = self._get_file_mapping_paths()
            assignment_stats, repo_hierarchy, issue_organizer, unknown_node = organize_issues_complete(
                repo_path=repo_path,
                all_issues=all_issues,
                file_content_provider=self.get_file_content_provider(),
                pickled_index_path=file_mapping_index,
                update_file_paths=True,
                create_unknown_directory=True,
                exclude_directories=config.get('exclude_directories', [])
            )

            # Print organized issues tree to file
            # Use the output directory from the singleton instead of JSON config
            results_dir = self.get_results_directory()
            organized_issues_file = f"{results_dir}/code_analysis/repo_analysis_organized_issues.txt"
            os.makedirs(os.path.dirname(organized_issues_file), exist_ok=True)

            with open(organized_issues_file, 'w', encoding='utf-8') as f:
                f.write("REPOSITORY ANALYSIS - ORGANIZED ISSUES BY DIRECTORY\n")
                f.write("=" * 60 + "\n\n")
                f.write(f"Repository: {repo_path}\n")
                f.write(f"Total Issues: {len(all_issues)}\n")
                f.write(f"Assigned to Directories: {assignment_stats['assigned']}\n")
                f.write(f"Unassigned: {assignment_stats['unassigned']}\n\n")

                # Write directory tree with issues
                self._write_directory_tree_with_issues(f, repo_hierarchy.get_root_node(), 0)

                # Write unassigned issues
                unassigned_issues = issue_organizer.get_unassigned_issues()
                if unassigned_issues:
                    f.write("\n" + "=" * 60 + "\n")
                    f.write("UNASSIGNED ISSUES\n")
                    f.write("=" * 60 + "\n")
                    for i, issue in enumerate(unassigned_issues, 1):
                        # Ensure issue is a dictionary before accessing its properties
                        if isinstance(issue, dict):
                            f.write(f"\n{i}. {issue.get('file', 'Unknown file')} - {issue.get('function_name', 'Unknown function')}\n")
                            f.write(f"   Issue: {issue.get('issue', 'No description')}\n")
                            f.write(f"   Severity: {issue.get('severity', 'unknown')}\n")
                        else:
                            f.write(f"\n{i}. Invalid issue format: {issue}\n")

            self.logger.info(f"Organized issues tree saved to: {organized_issues_file}")

            # Generate HTML report with project information (use repo analysis specific filename)
            if project_name:
                report_filename = f"repo_analysis_{project_name.replace(' ', '_')}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
            else:
                report_filename = f"repo_analysis_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"

            # Get the reports directory and create full path
            # Use the output directory from the singleton instead of JSON config
            reports_dir = self.get_reports_directory()
            report_file_path = os.path.join(reports_dir, report_filename)

            # Ensure the full directory path exists (including any subdirectories in the filename)
            report_dir = os.path.dirname(report_file_path)
            os.makedirs(report_dir, exist_ok=True)

            report_file = generate_html_report(all_issues, output_file=report_file_path, project_name=project_name)

            # Calculate and log statistics
            stats = calculate_stats(all_issues)
            self.logger.info(f"Report generated successfully: {report_file}")
            self.logger.info(f"Report statistics:")
            self.logger.info(f"  Total Issues: {stats['total']}")
            
            # Get filtering statistics if unified filter is available
            filter_stats_msg = ""
            if self.unified_issue_filter:
                try:
                    filter_stats = self.unified_issue_filter.get_filtering_stats()
                    # Add dropped issues count to the log message - show all levels that have dropped issues
                    dropped_category = filter_stats.get('level1_dropped_count', 0)
                    dropped_trivial = filter_stats.get('level2_dropped_count', 0)
                    dropped_challenge = filter_stats.get('level3_dropped_count', 0)
                    
                    # Build filter stats message showing all levels with dropped issues
                    dropped_parts = []
                    if dropped_category > 0:
                        dropped_parts.append(f"Category: {dropped_category}")
                    if dropped_trivial > 0:
                        dropped_parts.append(f"Trivial: {dropped_trivial}")
                    if dropped_challenge > 0:
                        dropped_parts.append(f"Challenge: {dropped_challenge}")
                    
                    if dropped_parts:
                        filter_stats_msg = f" (Dropped - {', '.join(dropped_parts)})"
                except Exception as e:
                    self.logger.debug(f"Failed to get filter statistics: {e}")
            
            self.logger.info(f"  Critical: {stats['critical']}, High: {stats['high']}, Medium: {stats['medium']}, Low: {stats['low']}{filter_stats_msg}")

            # Generate dropped issues report alongside the main report
            dropped_report_file = self._generate_dropped_issues_report(config, project_name)
            if dropped_report_file:
                self.logger.info(f"Dropped issues report generated: {dropped_report_file}")

            return True, report_file

        except Exception as e:
            self.logger.error(f"Error generating report: {e}")
            self.logger.error(f"Full traceback: {traceback.format_exc()}")
            return False, None

    def _collect_dropped_issues(self) -> List[Dict[str, Any]]:
        """
        Collect all dropped issues from the dropped_issues directory.
        
        Returns:
            List of dropped issue dictionaries with metadata
        """
        dropped_issues = []
        
        try:
            # Get the artifacts directory from the output provider
            output_provider = get_output_directory_provider()
            artifacts_dir = output_provider.get_repo_artifacts_dir()
            dropped_issues_base_dir = os.path.join(artifacts_dir, "dropped_issues")
            
            if not os.path.exists(dropped_issues_base_dir):
                self.logger.debug(f"No dropped issues directory found at: {dropped_issues_base_dir}")
                return []
            
            # Iterate through all level directories (level1_*, level2_*, level3_*)
            for level_dir_name in sorted(os.listdir(dropped_issues_base_dir)):
                level_dir_path = os.path.join(dropped_issues_base_dir, level_dir_name)
                
                if not os.path.isdir(level_dir_path):
                    continue
                
                # Read all JSON files in this level directory
                for filename in os.listdir(level_dir_path):
                    if not filename.endswith('.json'):
                        continue
                    
                    file_path = os.path.join(level_dir_path, filename)
                    try:
                        with open(file_path, 'r', encoding='utf-8') as f:
                            dropped_issue = json.load(f)
                            dropped_issues.append(dropped_issue)
                    except (json.JSONDecodeError, IOError) as e:
                        self.logger.warning(f"Failed to read dropped issue file {file_path}: {e}")
                        continue
            
            self.logger.info(f"Collected {len(dropped_issues)} dropped issues from {dropped_issues_base_dir}")
            return dropped_issues
            
        except Exception as e:
            self.logger.error(f"Error collecting dropped issues: {e}")
            return []

    def _generate_dropped_issues_report(self, config: dict, project_name: str = '') -> Optional[str]:
        """
        Generate HTML report for dropped issues.
        
        Args:
            config: Configuration dictionary
            project_name: Optional project name for the report title
            
        Returns:
            Path to the generated report file, or None if no dropped issues
        """
        try:
            # Collect all dropped issues
            dropped_issues = self._collect_dropped_issues()
            
            if not dropped_issues:
                self.logger.info("No dropped issues found - skipping dropped issues report generation")
                return None
            
            # Generate report filename
            if project_name:
                report_filename = f"dropped_issues_{project_name.replace(' ', '_')}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
            else:
                report_filename = f"dropped_issues_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
            
            # Get the reports directory
            reports_dir = self.get_reports_directory()
            report_file_path = os.path.join(reports_dir, report_filename)
            
            # Ensure directory exists
            os.makedirs(reports_dir, exist_ok=True)
            
            # Generate the HTML report
            report_file = generate_dropped_issues_html_report(
                dropped_issues=dropped_issues,
                output_file=report_file_path,
                project_name=project_name
            )
            
            self.logger.info(f"Dropped issues report generated successfully: {report_file}")
            self.logger.info(f"  Total dropped issues: {len(dropped_issues)}")
            
            return report_file
            
        except Exception as e:
            self.logger.error(f"Error generating dropped issues report: {e}")
            self.logger.error(f"Full traceback: {traceback.format_exc()}")
            return None

    def print_analysis_summary(self, issues_dir: str, report_file: str, issue_count: int) -> None:
        """
        Print summary of analysis results with directory locations.
        
        This provides users with easy access to:
        - The directory containing all issue JSON files
        - The HTML report path
        - Summary statistics
        
        Args:
            issues_dir: Path to the code_analysis directory containing issue JSON files
            report_file: Path to the generated HTML report
            issue_count: Total number of issues found
        """
        print()
        print("=" * 80)
        print("ANALYSIS COMPLETE")
        print("=" * 80)
        print(f"Issues Directory: {issues_dir}")
        print(f"HTML Report: {report_file}")
        print(f"Total Issues Found: {issue_count}")
        print("=" * 80)
        print()

    def generate_report_from_existing_issues(self, config_dict: Dict[str, Any], repo_path: str, out_dir: str):
        """Generate report from existing analysis files without running analysis."""
        try:
            self.logger.info("Starting report generation from existing issues...")

            # Load and validate configuration
            # self.logger.info(f"Loading configuration from: {config_file}")
            config = config_dict

            # Set repo_path in config for compatibility with existing code
            config['path_to_repo'] = repo_path

            # Determine the output base directory
            # Use out_dir parameter instead of reading from JSON config
            output_base_dir = out_dir

            # Ensure the output directory is absolute
            if output_base_dir:
                output_base_dir = os.path.abspath(output_base_dir)
                self.logger.info(f"Using output directory: {output_base_dir}")

            # Initialize OutputDirectoryProvider singleton before using it
            output_provider = get_output_directory_provider()
            output_provider.configure(repo_path, output_base_dir)
            self.logger.info(f"Configured OutputDirectoryProvider with repo_path: {repo_path}, output_base_dir: {output_base_dir}")

            # Set default output directories using base class path methods
            ast_paths = self.get_default_ast_output_paths()
            config['astCallGraphDir'] = ast_paths['code_insights_dir']
            # Create code_analysis directory under results/
            results_dir = self.get_results_directory()
            config['analysis_dir'] = f"{results_dir}/code_analysis"

            self.logger.info(f"Repository path: {config['path_to_repo']}")
            self.logger.info(f"LLM analysis output directory: {config['analysis_dir']}")

            # Create FileContentProvider instance for the repository (needed for directory assignment)
            repo_path_obj = Path(config["path_to_repo"])
            self.create_file_content_provider(repo_path_obj)

            # Initialize publisher-subscriber system to load existing results
            self.logger.info("Initializing publisher-subscriber system for existing results...")
            self._initialize_publisher_subscriber_for_report(config, output_base_dir)

            # For report regeneration:
            # 1. Apply Level 1 (Category) filter only - no LLM calls
            # 2. Apply deduplication (uses embeddings, not LLM)
            # 3. Generate HTML report
            # Skip Level 2 (LLM) and Level 3 (Response Challenger) to avoid expensive API calls
            from ..issue_filter.category_filter import CategoryBasedFilter
            
            # Get dropped issues directory for category filter
            output_provider = get_output_directory_provider()
            artifacts_dir = output_provider.get_repo_artifacts_dir()
            dropped_issues_dir = os.path.join(artifacts_dir, "dropped_issues")
            
            # Initialize category filter only (Level 1)
            category_filter = CategoryBasedFilter(dropped_issues_dir=dropped_issues_dir)
            self.logger.info("Report regeneration mode: applying Level 1 (Category) filter only")
            self.logger.info("Skipping Level 2 (LLM) and Level 3 (Response Challenger) - no LLM API calls")
            self.logger.info(f"Allowed categories: {list(category_filter.get_allowed_categories())}")

            # Apply category filter to loaded results before report generation
            # IMPORTANT: This MOVES filtered issues from code_analysis/ to dropped_issues/
            # The original JSON files are updated to remove dropped issues
            if self.results_publisher:
                repo_name = os.path.basename(repo_path.rstrip('/'))
                
                # Get the result IDs for this repo
                result_ids = self.results_publisher._repo_results.get(repo_name, [])
                
                # Get the code_analysis directory for updating files
                code_analysis_dir = config.get('analysis_dir', '')
                
                if result_ids:
                    self.logger.info(f"Applying Level 1 (Category) filter to {len(result_ids)} loaded results...")
                    self.logger.info(f"Filtered issues will be MOVED from code_analysis/ to dropped_issues/")
                    total_original_issues = 0
                    total_filtered_issues = 0
                    files_updated = 0
                    
                    for result_id in result_ids:
                        if result_id in self.results_publisher._results:
                            result = self.results_publisher._results[result_id]
                            
                            if 'results' in result and isinstance(result['results'], list):
                                original_issues = result['results']
                                original_count = len(original_issues)
                                total_original_issues += original_count
                                
                                # Apply only Level 1 (Category) filtering - no LLM calls
                                # This also saves dropped issues to dropped_issues/level1_category_filter/
                                filtered_issues = category_filter.filter_issues(original_issues)
                                filtered_count = len(filtered_issues)
                                total_filtered_issues += filtered_count
                                
                                # Update the result in place with filtered issues
                                self.results_publisher._results[result_id]['results'] = filtered_issues
                                
                                # If issues were dropped, update the original JSON file in code_analysis/
                                if filtered_count < original_count and code_analysis_dir:
                                    try:
                                        # Generate the filename for this result
                                        function_name = result.get('function', 'unknown')
                                        file_path = result.get('file_path', 'unknown')
                                        checksum = result.get('checksum', 'unknown')
                                        
                                        # Create safe filename components (same logic as subscriber)
                                        safe_function_name = "".join(c for c in function_name if c.isalnum() or c in ('_', '-'))
                                        safe_file_name = "".join(c for c in os.path.basename(file_path) if c.isalnum() or c in ('_', '-', '.'))
                                        
                                        # Truncate to prevent filesystem length issues
                                        if len(safe_function_name) > 100:
                                            safe_function_name = safe_function_name[:100]
                                        if len(safe_file_name) > 50:
                                            safe_file_name = safe_file_name[:50]
                                        
                                        # Use checksum or generate hash
                                        if checksum and checksum != "None" and checksum != "unknown":
                                            checksum_hash = checksum[:8] if len(checksum) > 8 else checksum
                                        else:
                                            checksum_hash = str(abs(hash(file_path)))[:8]
                                        
                                        # Generate filename matching the format: function_file_checksum_analysis.json
                                        filename = f"{safe_function_name}_{safe_file_name}_{checksum_hash}_analysis.json"
                                        json_file_path = os.path.join(code_analysis_dir, filename)
                                        
                                        if os.path.exists(json_file_path):
                                            # Update the JSON file with filtered issues
                                            updated_result = result.copy()
                                            updated_result['results'] = filtered_issues
                                            
                                            with open(json_file_path, 'w', encoding='utf-8') as f:
                                                json.dump(updated_result, f, indent=2, ensure_ascii=False)
                                            
                                            files_updated += 1
                                            dropped_from_file = original_count - filtered_count
                                            self.logger.debug(f"Updated {filename}: removed {dropped_from_file} dropped issues, {filtered_count} remaining")
                                    except Exception as e:
                                        self.logger.warning(f"Failed to update JSON file for {function_name}: {e}")
                    
                    dropped_count = total_original_issues - total_filtered_issues
                    self.logger.info(f"Level 1 (Category) filter applied: {total_original_issues} -> {total_filtered_issues} issues (dropped {dropped_count})")
                    if files_updated > 0:
                        self.logger.info(f"Updated {files_updated} JSON files in code_analysis/ to remove dropped issues")
            
            # Deduplication will be applied in _generate_report() - it uses embeddings, not LLM
            self.logger.info("Deduplication will be applied during report generation (uses embeddings, not LLM)")

            # Generate report directly
            self.logger.info("=== REPORT GENERATION FROM EXISTING ISSUES ===")
            report_results = self._generate_report(config)
            if report_results:
                report_success, report_file = report_results
                if report_success:
                    self.logger.info(f"Report generation completed successfully!")
                    self.logger.info(f"HTML report saved to: {report_file}")
                    return True
                else:
                    self.logger.warning("Report generation completed but no report was generated")
                    return False
            else:
                self.logger.error("Report generation failed")
                return False

        except ConfigValidationError as e:
            self.logger.error(f"Configuration validation failed: {e}")
            return False
        except Exception as e:
            self.logger.error(f"Unexpected error during report generation: {e}")
            traceback.print_exc()
            return False

    def _run_directory_classification_and_file_count_check(self, config: dict) -> Optional[AnalysisResult]:
        """
        Run DirectoryClassifier to get enhanced exclusions and check file count limit.
        
        This method:
        1. Runs DirectoryClassifier (static + LLM-based) to get enhanced exclude directories
        2. Updates config with the enhanced exclusions
        3. Counts files with supported extensions after filtering
        4. Returns error if count exceeds MAX_SUPPORTED_FILE_COUNT
        
        Args:
            config: Configuration dictionary
            
        Returns:
            AnalysisResult with error if limit exceeded, None if within limit
        """
        repo_path = config['path_to_repo']
        include_directories = config.get('include_directories', [])
        user_exclude_directories = config.get('exclude_directories', [])
        
        self.logger.info("Running DirectoryClassifier to get enhanced exclusions...")
        self.logger.info(f"Repository: {repo_path}")
        self.logger.info(f"User-provided include directories: {include_directories}")
        self.logger.info(f"User-provided exclude directories: {user_exclude_directories}")
        
        try:
            # Run enhanced directory exclusion (static + LLM-based)
            enhanced_exclude_dirs = self.get_enhanced_exclude_directories(
                repo_path=repo_path,
                config=config,
                user_provided_include_list=include_directories,
                user_provided_exclude_list=user_exclude_directories
            )
            
            self.logger.info(f"DirectoryClassifier complete:")
            self.logger.info(f"  User-provided exclusions: {len(user_exclude_directories)}")
            self.logger.info(f"  Enhanced exclusions (static + LLM): {len(enhanced_exclude_dirs)}")
            
            if enhanced_exclude_dirs:
                self.logger.info(f"  Directories to exclude: {sorted(enhanced_exclude_dirs)[:10]}{'...' if len(enhanced_exclude_dirs) > 10 else ''}")
            
            # Update config with enhanced exclusions for use in AST generation and analysis
            config['exclude_directories'] = enhanced_exclude_dirs
            self.logger.info("Updated config with enhanced exclude directories")
            
        except Exception as e:
            self.logger.warning(f"DirectoryClassifier failed, using user-provided exclusions: {e}")
            # Continue with user-provided exclusions
            enhanced_exclude_dirs = user_exclude_directories
        
        # Now check file count with the enhanced exclusions
        self.logger.info("\nChecking file count limit...")
        
        try:
            file_count = FilteredFileFinder.count_files_with_supported_extensions(
                repo_dir=repo_path,
                include_directories=include_directories,
                exclude_directories=enhanced_exclude_dirs
            )
            
            self.logger.info(f"Found {file_count} files with supported extensions")
            self.logger.info(f"Limit: {MAX_SUPPORTED_FILE_COUNT} files")
            
            if file_count > MAX_SUPPORTED_FILE_COUNT:
                error_msg = (
                    f"Repository has too many files ({file_count} files with supported extensions). "
                    f"Maximum allowed: {MAX_SUPPORTED_FILE_COUNT}. "
                    f"Please use include_directories or exclude_directories to reduce the scope."
                )
                self.logger.error(error_msg)
                
                return AnalysisResult.error(
                    code=AnalyzerErrorCode.ERROR_REPOSITORY_TOO_MANY_FILES,
                    message=error_msg,
                    details={
                        'file_count': file_count,
                        'max_allowed': MAX_SUPPORTED_FILE_COUNT,
                        'include_directories': include_directories,
                        'exclude_directories': enhanced_exclude_dirs
                    },
                    recoverable=True,
                    user_action="Reduce repository scope using include_directories or exclude_directories configuration"
                )
            
            self.logger.info(f"✓ File count check passed ({file_count}/{MAX_SUPPORTED_FILE_COUNT})")
            return None
            
        except Exception as e:
            self.logger.error(f"Error during file count check: {e}")
            # Don't fail analysis on count error, just log warning
            self.logger.warning("Proceeding with analysis despite file count check error")
            return None

    def merge_include_exclude_directories_from_config_and_params(self,
                                                                 config_dict: Dict[str, Any], 
                                                                 include_directories: List[str] = None, 
                                                                 exclude_directories: List[str] = None):
        """
        User can provide these include and exclude directories either through JSON or through arguments
        Merge them and return the list

        Args:
            config_dict: Configuration dictionary
            exclude_directories: List of additional directories to exclude
            include_directories: List of additional directories to include

        """
        # Compute union of directories from config and command-line arguments
        config_exclude_directories = config_dict.get('exclude_directories', []) or []
        config_include_directories = config_dict.get('include_directories', []) or []
        
        # Convert to sets for union operation, handling None values
        exclude_dirs_from_config = set(config_exclude_directories) if config_exclude_directories else set()
        exclude_dirs_from_args = set(exclude_directories) if exclude_directories else set()
        computed_exclude_directories = list(exclude_dirs_from_config.union(exclude_dirs_from_args))
        
        include_dirs_from_config = set(config_include_directories) if config_include_directories else set()
        include_dirs_from_args = set(include_directories) if include_directories else set()
        computed_include_directories = list(include_dirs_from_config.union(include_dirs_from_args))

        return computed_include_directories, computed_exclude_directories



    def run(self,
            config_dict: Dict[str, Any],
            repo_path: str, out_dir: str,
            force_recreate_ast: bool = False,
            force_llm_analysis: bool = False,
            file_filter: List[str] = None,
            exclude_directories: List[str] = None,
            include_directories: List[str] = None,
            exclude_files: List[str] = None,
            min_function_body_length: int = 7,
            analysys_type: str = "entire_repo",
            function_filter: str = None,
            num_functions_to_analyze: int = DEFAULT_NUM_FUNCTIONS_TO_ANALYZE,
            force_in_process_ast: bool = False,
            use_parallel: bool = True,
            max_workers: int = None):

        """
        Main entry point for the Hindsight Analysis tool.

        Args:
            config_dict: Configuration dictionary
            repo_path: Path to repository directory
            out_dir: Output directory
            force_recreate_ast: Force recreation of AST call graphs
            force_llm_analysis: Force re-analysis by clearing cache
            file_filter: Optional list of files to limit analysis to
            exclude_directories: List of additional directories to exclude
            include_directories: List of additional directories to include
            exclude_files: List of additional files to exclude
            min_function_body_length: Minimum number of lines for a function to be analyzed
            analysys_type: Analysis strategy type (recently_modified, branch_based, entire_repo)
            function_filter: Path to JSON file containing functions to filter. When provided, only functions listed in this file will be analyzed. Required for recently_modified_files strategy.
            num_functions_to_analyze: Maximum number of functions to analyze (default: 300)
            force_in_process_ast: Force AST generation to run in-process instead of using subprocess (default: False)
            use_parallel: Whether to use parallel processing for AST generation (default: True)
            max_workers: Maximum number of worker processes for parallel processing (default: None, uses system default)
        """

        computed_include_directories, computed_exclude_directories = self.merge_include_exclude_directories_from_config_and_params(config_dict, include_directories, exclude_directories)

        # Run the full analysis pipeline
        self.logger.info(f"Arguments passed to runner.run:")
        self.logger.info(f"  config_dict: {config_dict}")
        self.logger.info(f"  repo_path: {repo_path}")
        self.logger.info(f"  out_dir: {out_dir}")
        self.logger.info(f"  force_recreate_ast: {force_recreate_ast}")
        self.logger.info(f"  force_llm_analysis: {force_llm_analysis}")
        self.logger.info(f"  file_filter: {file_filter}")
        self.logger.info(f"  exclude_directories : {computed_exclude_directories}")
        self.logger.info(f"  include_directories : {computed_include_directories}")
        self.logger.info(f"  exclude_files: {exclude_files}")
        self.logger.info(f"  min_function_body_length: {min_function_body_length}")
        self.logger.info(f"  analysys_type: {analysys_type}")
        self.logger.info(f"  function_filter: {function_filter}")
        self.logger.info(f"  num_functions_to_analyze: {num_functions_to_analyze}")
        self.logger.info(f"  force_in_process_ast: {force_in_process_ast}")
        self.logger.info(f"  use_parallel: {use_parallel}")
        self.logger.info(f"  max_workers: {max_workers}")

        # Store the force_in_process_ast parameter
        self.force_in_process_ast = force_in_process_ast

        # Get API key early for summary generation using consolidated utility
        api_key = get_api_key_from_config(config_dict)
        llm_provider_type = get_llm_provider_type(config_dict)
        
        # Log API key status for LLM filtering
        self.logger.info(f"LLM Provider Type: {llm_provider_type}")
        if api_key:
            self.logger.info(f"API Key retrieved successfully - LLM filtering will be enabled")
        else:
            self.logger.warning(f"No API key retrieved - LLM filtering will be disabled")


        # Load verified functions from function_filter if provided (for any analysis type)
        # This allows filtering to specific functions even outside of recently_modified_files strategy
        if function_filter and os.path.exists(function_filter):
            self.logger.info(f"Loading verified functions from function_filter: {function_filter}")
            self._load_verified_functions(function_filter)

        # Handle recently_modified analysis type
        if analysys_type == DiffStrategy.RECENTLY_MODIFIED_FILES.value:
            self.logger.info(f"Using analysis type: {analysys_type}")

            # Validate that function_filter is provided for RECENTLY_MODIFIED_FILES strategy
            if not function_filter:
                self.logger.error("ERROR: --function-filter is required when using --analysys_type recently_modified_files")
                self.logger.error("The function filter JSON should have the same syntax as generated by git_recent_function_changes.py")
                self.logger.error("Example: --function-filter /path/to/functions_modified.json")
                sys.exit(1)

            # Validate that the function filter file exists
            if not os.path.exists(function_filter):
                self.logger.error(f"ERROR: Function filter file does not exist: {function_filter}")
                sys.exit(1)

            # Load and validate the function filter JSON
            try:
                with open(function_filter, 'r', encoding='utf-8') as f:
                    function_filter_data = json.load(f)

                # Validate JSON structure (should have 'functions_modified' key)
                if 'functions_modified' not in function_filter_data:
                    self.logger.error(f"ERROR: Invalid function filter JSON structure. Expected 'functions_modified' key in {function_filter}")
                    self.logger.error("The JSON should have the same syntax as generated by git_recent_function_changes.py")
                    sys.exit(1)

                functions_modified = function_filter_data['functions_modified']
                if not isinstance(functions_modified, dict):
                    self.logger.error(f"ERROR: 'functions_modified' should be a dictionary in {function_filter}")
                    sys.exit(1)

                self.logger.info(f"Loaded function filter with {len(functions_modified)} functions from: {function_filter}")

                # Load the verified functions from the JSON structure
                self.verified_functions = set(functions_modified.keys())
                self.logger.info(f"Will analyze only these {len(self.verified_functions)} functions: {list(self.verified_functions)[:10]}..." if len(self.verified_functions) > 10 else f"Will analyze these functions: {list(self.verified_functions)}")

            except json.JSONDecodeError as e:
                self.logger.error(f"ERROR: Invalid JSON in function filter file {function_filter}: {e}")
                sys.exit(1)
            except Exception as e:
                self.logger.error(f"ERROR: Failed to load function filter file {function_filter}: {e}")
                sys.exit(1)

        # Update the instance file filter if provided or generated
        if file_filter is not None:
            self.file_filter = file_filter

        try:
            # Start sleep prevention early to keep Mac awake during entire analysis
            self._start_sleep_prevention()

            # Load and validate configuration
            # self.logger.info(f"Loading configuration from: {config_file}")
            config = config_dict

            # Override JSON config values with command line arguments if provided
            # For AST generation, we should NOT use DirectoryClassifier expansion
            # Instead, preserve the simple directory names from config and command line
            if exclude_directories is not None:
                config['exclude_directories'] = exclude_directories
                self.logger.info(f"Overriding exclude_directories from command line: {exclude_directories}")
            # If no command line override, keep the original config exclude_directories as-is
            # This ensures AST generation uses simple directory names like "Tools", not expanded paths

            if include_directories is not None:
                config['include_directories'] = include_directories
                self.logger.info(f"Overriding include_directories from command line: {include_directories}")

            if exclude_files is not None:
                config['exclude_files'] = exclude_files
                self.logger.info(f"Overriding exclude_files from command line: {exclude_files}")

            # Set min_function_body_length in config (from command line or default)
            config['min_function_body_length'] = min_function_body_length
            self.logger.info(f"Using min_function_body_length: {min_function_body_length}")

            # Set num_functions_to_analyze in config
            config['num_functions_to_analyze'] = num_functions_to_analyze
            self.logger.info(f"Using num_functions_to_analyze: {num_functions_to_analyze}")

            # Set repo_path in config for compatibility with existing code
            config['path_to_repo'] = repo_path

            # Determine the output base directory
            # Use out_dir parameter instead of reading from JSON config
            output_base_dir = out_dir

            # Ensure the output directory is absolute and create it
            if output_base_dir:
                output_base_dir = os.path.abspath(output_base_dir)
                self.logger.info(f"Using output directory: {output_base_dir}")
                # Create the output directory if it doesn't exist
                os.makedirs(output_base_dir, exist_ok=True)

            # Initialize OutputDirectoryProvider singleton early
            output_provider = get_output_directory_provider()
            output_provider.configure(repo_path, output_base_dir)
            self.logger.info(f"Configured OutputDirectoryProvider with repo_path: {repo_path}, output_base_dir: {output_base_dir}")

            # Step 0: Directory Structure Index (before any analysis)
            self.logger.info("\n\n=== DIRECTORY STRUCTURE INDEX ===")
            self._ensure_directory_structure_index(repo_path)

            # Set default output directories using base class path methods
            ast_paths = self.get_default_ast_output_paths()
            config['astCallGraphDir'] = ast_paths['code_insights_dir']

            # Create code_analysis directory under results/
            results_dir = self.get_results_directory()
            config['analysis_dir'] = f"{results_dir}/code_analysis"

            # Update logging to use the output directory if custom output is specified
            if out_dir:
                logs_dir = os.path.join(out_dir, DEFAULT_LOGS_DIR)
                os.makedirs(logs_dir, exist_ok=True)

                # Reconfigure logging to use the custom logs directory
                custom_log_file = os.path.join(logs_dir, f"hindsight_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")

                # Reset logging configuration flag to allow reconfiguration
                LogUtil._configured = False

                # Setup logging with custom log file
                LogUtil.setup_logging(
                    log_file=custom_log_file,
                    log_level="INFO",
                    console_level="INFO",
                    file_level="DEBUG"
                )

                # Get a fresh logger instance
                self.logger = get_logger(__name__)

                self.logger.info(f"AST call graphs will be saved to: {config['astCallGraphDir']}")
                self.logger.info(f"LLM analysis will be saved to: {config['analysis_dir']}")
                self.logger.info(f"Logs will be saved to: {logs_dir}")

            # Setup prompt logging - use the output base directory
            Claude.setup_prompts_logging()
            
            # Clear older prompts at the beginning of analysis
            Claude.clear_older_prompts()

            # Get the actual directory used for logging from the singleton
            output_provider = get_output_directory_provider()
            actual_prompts_dir = f"{output_provider.get_repo_artifacts_dir()}/prompts_sent"
            self.logger.info(f"Prompt logging setup completed and older prompts cleared in: {actual_prompts_dir}")

            # Create FileContentProvider instance for the repository
            repo_path_obj = Path(config["path_to_repo"])
            self.create_file_content_provider(repo_path_obj)

            # TTL functionality has been removed

            self.logger.info("Configuration loaded successfully")
            self.logger.info(f"Repository path: {config['path_to_repo']}")
            self.logger.info(f"AST call graph directory: {config['astCallGraphDir']}")
            self.logger.info(f"LLM analysis output directory: {config['analysis_dir']}")
            self.logger.info(f"Generate AST call graph: Always enabled")
            self.logger.info(f"Force recreate AST: {force_recreate_ast}")
            self.logger.info(f"Force LLM analysis: {force_llm_analysis}")

            # Step 1.5: Run DirectoryClassifier and check file count limit BEFORE AST generation
            self.logger.info("\n\n=== DIRECTORY CLASSIFICATION & FILE COUNT CHECK ===")
            file_count_result = self._run_directory_classification_and_file_count_check(config)
            if file_count_result and file_count_result.is_error():
                error_code = file_count_result.code
                self.logger.error(f"[{error_code.value}] {file_count_result.message}")
                print(f"\n❌ Analysis failed with error code: {error_code.value}")
                print(f"Error: {file_count_result.message}")
                if file_count_result.user_action:
                    print(f"Action: {file_count_result.user_action}")
                sys.exit(1)

            # Step 2: AST Call Graph Generation (now uses enhanced exclusions from Step 1.5)
            self.logger.info("\n\n=== AST CALL GRAPH GENERATION ===")

            # Check if AST files already exist
            ast_files_exist = self._check_existing_ast_files(config)

            if force_recreate_ast:
                self.logger.info("Force recreate AST flag is set - will regenerate AST call graphs")
                should_generate = True
            elif ast_files_exist:
                self.logger.info("Existing AST call graph files detected - reusing existing artifacts")
                should_generate = False
            else:
                self.logger.info("No existing AST files found - will generate new ones")
                should_generate = True

            if should_generate:
                # Generate AST call graph with parallel processing parameters
                nested_call_graph_path = self._generate_ast_call_graph(
                    config,
                    use_parallel=use_parallel,
                    max_workers=max_workers
                )

                # Process the generated call graph
                results, summary = self._process_call_graph(config, nested_call_graph_path, output_base_dir)

                self.logger.info("AST call graph processing completed successfully!")
                self.logger.info(f"Results: {len(results)} processed entries")
                self.logger.info(f"Summary: {len(summary)} files processed")
            else:
                self.logger.info("Skipping AST call graph generation - using existing files")
                # Still need to get the merged call graph path for potential processing
                ast_call_graph_dir = config['astCallGraphDir']
                nested_call_graph_path = os.path.join(ast_call_graph_dir, NESTED_CALL_GRAPH_FILE)

                # Always load call graph data for on-demand processing, even if we skip generation
                self.logger.info("Loading existing call graph data for on-demand processing...")
                results, summary = self._process_call_graph(config, nested_call_graph_path, output_base_dir)
                self.logger.info("Call graph data loaded successfully!")
                self.logger.info(f"Results: {len(results)} processed entries")
                self.logger.info(f"Summary: {len(summary)} files processed")

            # Enhanced prompts functionality removed
    
            # Step 3.5: Build filtered lists if file filter is provided
            if self.file_filter:
                self.logger.info("\n\n=== BUILDING FILTERED LISTS ===")
                self._build_filtered_lists(config)

            # Step 4: Code Analysis (if processed files exist)
            self.logger.info("\n\n=== CODE ANALYSIS ===")

            # Clear LLM analysis cache if force flag is set
            if force_llm_analysis:
                self.logger.info("Force LLM analysis flag is set - clearing analysis cache and output directory")
                self._clear_llm_analysis_cache(config, output_base_dir)

            analysis_results = self._run_code_analysis(config, output_base_dir, api_key)
            if analysis_results:
                successful, failed = analysis_results
                self.logger.info(f"Code analysis completed. Successful: {successful}, Failed: {failed}")


            # Report generation is now handled separately after run() completes
            # This allows API calls to skip report generation while standalone usage can still generate reports

            # Print dropped issues statistics at the end of analysis
            self._print_dropped_issues_statistics()

            self.logger.info("Hindsight analysis pipeline completed successfully!")

        except ConfigValidationError as e:
            error_code = AnalyzerErrorCode.ERROR_ANALYSIS_INVALID_CONFIG
            self.logger.error(f"[{error_code.value}] Configuration validation failed: {e}")
            print(f"\n❌ Analysis failed with error code: {error_code.value}")
            print(f"Error: {e}")
            sys.exit(1)
        except Exception as e:
            error_code = AnalyzerErrorCode.ERROR_INTERNAL_UNKNOWN
            self.logger.error(f"[{error_code.value}] Unexpected error: {e}")
            print(f"\n❌ Analysis failed with error code: {error_code.value}")
            print(f"Error: {e}")
            traceback.print_exc()
            sys.exit(1)
        finally:
            # Skip analytics session end - using centralized TokenTracker instead
            # self._end_analytics_session()

            # Always stop sleep prevention when done
            self._stop_sleep_prevention()

    def _print_dropped_issues_statistics(self) -> None:
        """
        Print statistics about dropped issues at each filtering level.
        This replicates the functionality that was in analyze_filter_stats.py.
        """
        if not self.unified_issue_filter:
            self.logger.info("No unified issue filter available - skipping dropped issues statistics")
            return
        
        try:
            # Get filtering statistics from the unified filter
            filter_stats = self.unified_issue_filter.get_filtering_stats()
            
            level1_dropped = filter_stats.get('level1_dropped_count', 0)
            level2_dropped = filter_stats.get('level2_dropped_count', 0)
            level3_dropped = filter_stats.get('level3_dropped_count', 0)
            total_dropped = level1_dropped + level2_dropped + level3_dropped
            
            # Only print statistics if there were dropped issues
            if total_dropped == 0:
                self.logger.info("=== ISSUE FILTERING ANALYSIS ===")
                self.logger.info("No issues were dropped during filtering")
                return
            
            # Print detailed statistics similar to the original script
            self.logger.info("=== ISSUE FILTERING ANALYSIS ===")
            self.logger.info("")
            
            if level1_dropped > 0:
                self.logger.info(f"Level 1 (Category): Dropped {level1_dropped} issues")
            if level2_dropped > 0:
                self.logger.info(f"Level 2 (LLM): Dropped {level2_dropped} issues")
            if level3_dropped > 0:
                self.logger.info(f"Level 3 (Response Challenger): Dropped {level3_dropped} issues")
            
            self.logger.info("")
            self.logger.info("=== SUMMARY ===")
            self.logger.info(f"Level 1 (Category Filter):     {level1_dropped:3d} issues dropped")
            self.logger.info(f"Level 2 (LLM Filter):          {level2_dropped:3d} issues dropped")
            self.logger.info(f"Level 3 (Response Challenger): {level3_dropped:3d} issues dropped")
            self.logger.info(f"                               ----")
            self.logger.info(f"Total issues dropped:          {total_dropped:3d} issues")
            self.logger.info("")
            
            # Show filtering levels explanation
            self.logger.info("=== FILTERING LEVELS EXPLANATION ===")
            self.logger.info("Level 1 (Category Filter): Filters out issues in predefined categories")
            filtered_categories = filter_stats.get('level1_filtered_categories', [])
            if filtered_categories:
                categories_str = ", ".join(sorted(filtered_categories))
                self.logger.info(f"  - Categories: {categories_str}")
            self.logger.info("")
            self.logger.info("Level 2 (LLM Filter): Uses LLM to identify trivial/obvious issues")
            self.logger.info("  - Removes issues that are too simple or obvious to be valuable")
            self.logger.info("")
            self.logger.info("Level 3 (Response Challenger): Uses LLM to challenge issue validity")
            self.logger.info("  - Final validation to ensure issues are worth pursuing")
            self.logger.info("  - Most aggressive filter, removes issues not deemed valuable")
            self.logger.info("")
            
            # Show percentage breakdown if there were dropped issues
            if total_dropped > 0:
                self.logger.info("=== PERCENTAGE BREAKDOWN ===")
                if level1_dropped > 0:
                    self.logger.info(f"Level 1: {level1_dropped/total_dropped*100:.1f}% of total dropped issues")
                if level2_dropped > 0:
                    self.logger.info(f"Level 2: {level2_dropped/total_dropped*100:.1f}% of total dropped issues")
                if level3_dropped > 0:
                    self.logger.info(f"Level 3: {level3_dropped/total_dropped*100:.1f}% of total dropped issues")
                self.logger.info("")
            
        except Exception as e:
            self.logger.error(f"Error printing dropped issues statistics: {e}")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Hindsight Analysis Tool - Analyzes code repositories using AST call graphs and LLM analysis",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
FILTERING LOGIC:
===============

This tool uses a two-stage filtering approach:

1. AST GENERATION FILTERING:
   - Only honors --exclude-directories (and exclude_directories from JSON config)
   - All AST files are generated excluding only these directories
   - Other filters do NOT affect AST generation

2. LLM ANALYSIS FILTERING (determines which files are analyzed with LLM):
   - Uses the following precedence (higher precedence overrides lower):

   a) --file-filter (HIGHEST PRECEDENCE)
      - If provided, only analyzes functions/classes in specified files
      - Completely ignores all other filtering parameters

   b) Directory and file filters:
      - --include-directories: Only analyze files in these directories
      - --exclude-directories: Exclude these directories (even if in include_directories)
      - --exclude-files: Exclude specific files

EXAMPLES:
========
# Use file filter (ignores all directory filters)
%(prog)s --config config.json --repo /path/to/repo --file-filter src/main.py src/utils.py

# Include only src directory, but exclude test subdirectories
%(prog)s --config config.json --repo /path/to/repo --include-directories src --exclude-directories src/test

# Exclude specific files and directories
%(prog)s --config config.json --repo /path/to/repo --exclude-directories build .git --exclude-files debug.py

# Override JSON config settings
%(prog)s --config config.json --repo /path/to/repo --exclude-directories custom_exclude --include-directories custom_include

# Use recently_modified_files strategy with function filter
%(prog)s --config config.json --repo /path/to/repo --analysys_type recently_modified_files --function-filter /path/to/functions_modified.json

# Use dummy analyzer via config (set llm_provider_type to "dummy" in config.json)
%(prog)s --config config_with_dummy.json --repo /path/to/repo
        """
    )
    parser.add_argument(
        "--config", "-c",
        required=True,
        help="Path to configuration file"
    )
    parser.add_argument(
        "--repo", "-r",
        required=True,
        help="Path to repository directory"
    )
    parser.add_argument(
        "--out-dir", "-o",
        default=os.path.expanduser("~/llm_artifacts"),
        help="Output directory for AST trees, LLM analysis, and logs (default: ~/llm_artifacts)"
    )
    parser.add_argument(
        "--force-recreate-ast",
        action="store_true",
        help="Force recreation of AST call graphs even if they already exist"
    )
    parser.add_argument(
        "--force-llm-analysis",
        action="store_true",
        help="Force re-analysis by clearing LLM analysis cache and output directory"
    )
    parser.add_argument(
        "--generate-report-from-existing-issues",
        action="store_true",
        help="Generate report from existing analysis files without running analysis. Requires --config to locate artifacts."
    )
    parser.add_argument(
        "--false-positives-csv",
        default=None,
        metavar="CSV_PATH",
        help=(
            "Path to a false-positives CSV produced by an external analytical "
            "system (e.g. Roo).  Only valid with --generate-report-from-existing-issues. "
            "Issues that match entries in the CSV are removed from the report "
            "via two passes: (1) exact checksum+title match, "
            "(2) semantic similarity search using ChromaDB embeddings."
        ),
    )
    parser.add_argument(
        "--file-filter",
        nargs="+",
        help="List of files to limit analysis to. Only functions and classes in these files will be analyzed."
    )
    parser.add_argument(
        "--exclude-directories",
        nargs="+",
        help="List of directories to exclude from analysis (overrides JSON config)"
    )
    parser.add_argument(
        "--include-directories",
        nargs="+",
        help="List of directories to include in analysis (overrides JSON config)"
    )
    parser.add_argument(
        "--exclude-files",
        nargs="+",
        help="List of files to exclude from analysis (overrides JSON config)"
    )
    parser.add_argument(
        "--min-function-body-length",
        type=int,
        default=7,
        help="Minimum number of lines for a function to be analyzed (default: 7)"
    )
    parser.add_argument(
        "--analysys_type",
        choices=[strategy.value for strategy in DiffStrategy],
        default=DiffStrategy.ENTIRE_REPO.value,
        help="Analysis strategy type (default: entire_repo). Note: recently_modified_files requires --function-filter"
    )
    parser.add_argument(
        "--function-filter",
        help="Path to JSON file containing functions to filter. When provided, only functions listed in this file will be analyzed. Required when using --analysys_type recently_modified_files. The JSON should have the same syntax as generated by git_recent_function_changes.py."
    )
    parser.add_argument(
        "--num-functions-to-analyze",
        type=int,
        default=DEFAULT_NUM_FUNCTIONS_TO_ANALYZE,
        help=f"Maximum number of functions to analyze (default: {DEFAULT_NUM_FUNCTIONS_TO_ANALYZE}). If there are existing results, they count towards this limit."
    )
    parser.add_argument(
        "--user-prompt",
        action="append",
        help="Optional user-provided prompt to be included in the system prompt for code analysis. Can be specified multiple times to add multiple prompts. Each will be appended to the standard system prompt."
    )
    parser.add_argument(
        "--force-in-process-ast",
        action="store_true",
        help="Force AST generation to run in-process instead of using subprocess (default: false, uses out-of-process generation)"
    )
    parser.add_argument(
        "--no-parallel",
        action="store_true",
        help="Disable parallel AST generation. By default, AST generation uses multiple processes for better performance."
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        default=None,
        help="Maximum number of worker processes for parallel AST generation (default: 4 or CPU count, whichever is smaller)"
    )

    args = parser.parse_args()

    # Create runner instance
    runner = CodeAnalysisRunner()

    # Load and validate configuration to determine LLM provider type
    logger.info(f"Loading configuration from: {args.config}")
    try:
        config = load_and_validate_config(args.config)
    except ConfigValidationError as e:
        logger.error(f"Configuration validation failed: {e}")
        sys.exit(1)

    # Auto-create and set TokenTracker
    llm_provider_type = get_llm_provider_type(config)
    token_tracker = TokenTracker(llm_provider_type)
    runner.set_token_tracker(token_tracker)
    logger.info(f"Auto-created TokenTracker for provider: {llm_provider_type}")

    # Add default file system subscriber and prior results store when running as standalone script
    repo_name = os.path.basename(args.repo.rstrip('/'))

    # Add default file system subscriber for writing results
    default_subscriber = CodeAnalysysResultsLocalFSSubscriber(args.out_dir)
    default_subscriber.set_repo_name(repo_name)
    runner.add_results_subscriber(default_subscriber)

    # Add default file system prior results store for duplicate checking
    print(f"DEBUG: Creating FileSystemResultsCache with base_dir='{args.out_dir}'")
    default_prior_store = FileSystemResultsCache(args.out_dir)

    # Initialize the store for this repository to build the result index
    print(f"DEBUG: Initializing FileSystemResultsCache for repo='{repo_name}'")
    default_prior_store.initialize_for_repo(repo_name)

    print(f"DEBUG: Registering FileSystemResultsCache with runner")
    runner.register_prior_result_store(default_prior_store)

    # Check if user wants to generate report from existing issues only
    if args.generate_report_from_existing_issues:
        if not args.config:
            logger.error("Error: --config is required when using --generate-report-from-existing-issues")
            sys.exit(1)

        # Inject the optional FP CSV path into the config so _generate_report
        # can apply the FP CSV Filter after deduplication.
        if args.false_positives_csv:
            if not os.path.isfile(args.false_positives_csv):
                logger.error(
                    "Error: --false-positives-csv file not found: %s",
                    args.false_positives_csv,
                )
                sys.exit(1)
            config["false_positives_csv"] = args.false_positives_csv
            logger.info(
                "FP CSV Filter enabled with: %s", args.false_positives_csv
            )

        success = runner.generate_report_from_existing_issues(
            config_dict=config,
            repo_path=args.repo,
            out_dir=args.out_dir
        )
        sys.exit(0 if success else 1)

    if args.false_positives_csv:
        logger.error(
            "Error: --false-positives-csv is only valid with "
            "--generate-report-from-existing-issues"
        )
        sys.exit(1)

    # Configuration already loaded above for TokenTracker creation

    # Set user-provided prompts if provided (MUST be done before runner.run())
    if args.user_prompt:
        runner.set_user_provided_prompts(args.user_prompt)

    runner.run(config_dict=config,
                repo_path=args.repo,
                out_dir=args.out_dir,
                force_recreate_ast=args.force_recreate_ast,
                force_llm_analysis=args.force_llm_analysis,
                file_filter=args.file_filter,
                exclude_directories=args.exclude_directories,
                include_directories=args.include_directories,
                exclude_files=args.exclude_files,
                min_function_body_length=args.min_function_body_length,
                analysys_type=args.analysys_type,
                function_filter=args.function_filter,
                num_functions_to_analyze=args.num_functions_to_analyze,
                force_in_process_ast=args.force_in_process_ast,
                use_parallel=not args.no_parallel,
                max_workers=args.max_workers,
                )

    # Generate report after analysis completes (for standalone usage)
    logger.info("\n\n=== REPORT GENERATION ===")
    
    # Prepare config for report generation
    report_config = config.copy()
    report_config['path_to_repo'] = args.repo
    
    # Set analysis directory for report generation
    from ..utils.output_directory_provider import get_output_directory_provider
    output_provider = get_output_directory_provider()
    results_dir = f"{output_provider.get_repo_artifacts_dir()}/results"
    report_config['analysis_dir'] = f"{results_dir}/code_analysis"
    
    report_results = runner._generate_report(report_config, writeback_final_issues=True)
    if report_results:
        report_success, report_file = report_results
        if report_success:
            logger.info(f"Report generation completed successfully!")
            logger.info(f"HTML report saved to: {report_file}")
            
            # Print analysis summary with directory locations for false positive management
            issues_dir = report_config['analysis_dir']
            # Count total issues from publisher
            repo_name = os.path.basename(args.repo.rstrip('/'))
            all_results = runner.results_publisher.get_results(repo_name) if runner.results_publisher else []
            issue_count = sum(len(r.get('results', [])) for r in all_results) if all_results else 0
            runner.print_analysis_summary(issues_dir, report_file, issue_count)
        else:
            logger.warning("Report generation completed but no report was generated")
    else:
        logger.warning("Report generation failed")

    # Print token usage summary after analysis
    if runner.get_token_tracker():
        input_tokens, output_tokens = runner.get_token_tracker().get_total_token_usage()
        total_tokens = input_tokens + output_tokens
        print(f"\n=== TOKEN USAGE SUMMARY ===")
        print(f"Input Tokens:  {input_tokens:,}")
        print(f"Output Tokens: {output_tokens:,}")
        print(f"Total Tokens:  {total_tokens:,}")
        print(f"Provider:      {runner.get_token_tracker().llm_provider_type}")
        print("=" * 27)


if __name__ == "__main__":
    main()
