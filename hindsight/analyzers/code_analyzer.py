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
import asyncio
import json
import os
import sys
import threading
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

from .analysis_runner import AnalysisRunner
from .analysis_runner_mixins import UnifiedIssueFilterMixin, ReportGeneratorMixin
from .base_analyzer import BaseAnalyzer
from .directory_classifier import DirectoryClassifier
from .dummy_analyzer import DummyCodeAnalyzer
from .token_tracker import TokenTracker
from ..analysys_strategy.diff_strategy import DiffStrategy
from ..core.constants import (DEFAULT_LOGS_DIR, DEFAULT_MAX_TOKENS,
                              MIN_FUNCTION_BODY_LENGTH, MAX_FUNCTION_BODY_LENGTH,
                              DEFAULT_NUM_FUNCTIONS_TO_ANALYZE,
                              MERGED_DEFINED_CLASSES_FILE,
                              MERGED_SYMBOLS_FILE, NESTED_CALL_GRAPH_FILE,
                              PROCESSED_OUTPUT_DIR, DEFAULT_LLM_MODEL, DEFAULT_LLM_API_END_POINT,
                              CODE_ANALYZER_DEFAULT_WORKERS, LLM_PROVIDER_RATE_LIMIT,
                              LLM_PROVIDER_RATE_WINDOW_SECONDS,
                              CALL_TREE_ANALYSIS_MAX_DEPTH,
                              CALL_TREE_ANALYSIS_MAX_CHARS,
                              CALL_TREE_ANALYSIS_MAX_NODES)
from ..core.call_tree import CallTreeBuilder
from ..core.lang_util.all_supported_extensions import ALL_SUPPORTED_EXTENSIONS
from ..core.lang_util.filter_by_file_util import FilterByFileUtil
from ..core.ast_index import RepoAstIndex
from hindsight.utils.log_util import LogUtil, get_logger, setup_default_logging
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


class CodeAnalyzer(BaseAnalyzer):
    """Result-reader for code analysis output.

    All LLM-driven analysis lives in `hindsight.orchestration.CodePipeline`;
    this class survives only because the report-generation path needs a
    `BaseAnalyzer` subclass that knows where on disk to find `*_analysis.json`
    files (one level deeper than `BaseAnalyzer._read_analysis_results`'s
    default).
    """

    def name(self) -> str:
        return "CodeAnalyzer"

    def analyze_function(self, func_record: Mapping[str, Any]) -> Optional[Mapping[str, Any]]:
        # Required by BaseAnalyzer. Unused — analysis goes through CodePipeline.
        raise NotImplementedError(
            "CodeAnalyzer no longer performs analysis directly; use "
            "hindsight.orchestration.CodePipeline instead."
        )

    def pull_results_from_directory(self, artifacts_dir: str) -> Dict[str, Any]:
        """Read all `*_analysis.json` files from `{artifacts_dir}/results/code_analysis/`.

        Returns the same `{'results', 'statistics', 'summary'}` shape the
        legacy implementation produced — report generation depends on it.
        """
        code_analysis_dir = os.path.join(artifacts_dir, "results", "code_analysis")
        result = self._read_analysis_results(code_analysis_dir, ANALYSIS_FILE_SUFFIX)
        result["summary"]["analyzer_type"] = "code_analysis"
        result["summary"]["analysis_directory"] = code_analysis_dir
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

    def _process_call_graph(self, config: dict, nested_call_graph_path: str) -> tuple:
        """Validate call graph exists and prepare for on-demand processing."""
        self.logger.info("Validating call graph for on-demand processing...")

        # Extract configuration values
        ast_call_graph_dir = config['astCallGraphDir']
        # repo_path = config['path_to_repo']  # Unused variable

        # Ensure analysis_input directory exists
        self.processed_output_dir = os.path.join(os.path.dirname(ast_call_graph_dir), PROCESSED_OUTPUT_DIR, "code_analysis")
        os.makedirs(self.processed_output_dir, exist_ok=True)

        # Validate call graph file exists
        if not os.path.exists(nested_call_graph_path):
            self.logger.error(f"Call graph file not found: {nested_call_graph_path}")
            return [], {}

        # Load and validate call graph structure (flat list of file entries)
        call_graph_data = read_json_file(nested_call_graph_path)
        if not call_graph_data or not isinstance(call_graph_data, list):
            self.logger.error(f"Invalid call graph structure in: {nested_call_graph_path}")
            return [], {}

        # Count total functions for reporting
        total_functions = 0
        for file_entry in call_graph_data:
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

    def _build_current_checksum_map(self, repo_path: str = None) -> dict:
        """Build a mapping of (file_path, function_name) -> current checksum by reading source files from disk."""
        checksums = {}
        if not hasattr(self, 'call_graph_data') or not self.call_graph_data:
            return checksums
        if not repo_path:
            repo_path = getattr(self, '_current_repo_path', None)
        if not repo_path:
            return checksums
        for file_entry in self.call_graph_data:
            for func_entry in file_entry.get('functions', []):
                func_name = func_entry.get('function', '')
                context = func_entry.get('context', {})
                file_path = context.get('file', '') or file_entry.get('file', '')
                start_line = context.get('start', 0)
                end_line = context.get('end', 0)
                if func_name and file_path and start_line and end_line:
                    checksums[(file_path, func_name)] = HashUtil.checksum_for_function_source(
                        repo_path, file_path, start_line, end_line
                    )
        return checksums

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
        current_checksums = self._build_current_checksum_map(repo_path)
        for subscriber in self._subscribers:
            if hasattr(subscriber, 'load_existing_results'):
                loaded_count = subscriber.load_existing_results(
                    repo_name, self.results_publisher,
                    current_checksums=current_checksums if current_checksums else None
                )
                if loaded_count > 0:
                    self.logger.info(f"Loaded {loaded_count} existing analysis results for checksum-based caching via {type(subscriber).__name__}")

        self.logger.info(f"Initialized publisher-subscriber system for repository: {repo_name}")

    def _initialize_publisher_subscriber_for_report(self, config: dict, output_base_dir: str,
                                                     current_checksums: dict = None) -> None:
        """
        Initialize the publisher-subscriber system for report generation from existing issues.
        Unlike _initialize_publisher_subscriber(), this method loads results directly into
        the publisher's results collection so they are available via get_results().

        Args:
            config: Configuration dictionary
            output_base_dir: Base output directory
            current_checksums: Optional dict mapping (file_path, function_name) -> current checksum.
                             When provided, stale results are deleted during loading.
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
                loaded_count = subscriber.load_existing_results_for_report(
                    repo_name, self.results_publisher,
                    current_checksums=current_checksums if current_checksums else None
                )
                if loaded_count > 0:
                    self.logger.info(f"Loaded {loaded_count} existing analysis results for report generation via {type(subscriber).__name__}")

        self.logger.info(f"Initialized publisher-subscriber system for report generation: {repo_name}")

    def _run_code_analysis(
        self,
        config: dict,
        output_base_dir: str,
        api_key: Optional[str] = None,
    ) -> tuple:
        """Run code analysis through the async orchestration stack.

        Drop-in replacement for the old `_run_code_analysis_legacy` /
        `_run_call_tree_code_analysis` paths. Returns the same
        `(successful, failed)` tuple so the caller does not change.

        What this method does NOT change:
          - Publisher/subscriber initialization (still
            `_initialize_publisher_subscriber(...)`)
          - Unified issue filter initialization
          - Token tracker creation
          - Conversation logging path (preserves
            `prompts_sent/code_analysis/{N}/stepX_stage.md` layout)
          - Output directories under `~/llm_artifacts/{repo}/...`
          - On-disk results in `results/code_analysis/*_analysis.json`

        What is new:
          - LLM HTTP is async (`httpx.AsyncClient`)
          - Per-iteration tool calls dispatch concurrently
          - Per-function fan-out via `asyncio.Semaphore` (no thread pool)
          - Per-function failures isolated (one bad LLM response can't abort
            the whole run); results published write-through so partial state
            is visible even if the run crashes
        """
        from ..orchestration import (
            AnalysisContext,
            AnalysisSession,
            AsyncResultSink,
            CodePipeline,
            FunctionFilters,
        )

        self.logger.info("Starting code analysis via async orchestration stack...")

        # --- Token tracker (preserve legacy behavior) ---
        if not self.token_tracker:
            llm_provider_type = get_llm_provider_type(config)
            self.token_tracker = TokenTracker(llm_provider_type)
            self.logger.info(
                f"Auto-initialized centralized token tracker for provider: {llm_provider_type}"
            )

        # --- Publisher init (unchanged from legacy path) ---
        self._initialize_publisher_subscriber(config, output_base_dir)

        # --- Retry Apple Connect token fetch if it failed at startup ---
        if not api_key:
            self.logger.info("API key not available from startup — retrying Apple Connect token fetch...")
            api_key = get_api_key_from_config(config)
            if api_key:
                self.logger.info("Apple Connect token retrieved on retry")

        # --- Unified issue filter (unchanged) ---
        self._initialize_unified_issue_filter(api_key, config)

        if not hasattr(self, "call_graph_data") or not self.call_graph_data:
            self.logger.error("No call graph data available for analysis")
            return 0, 0

        repo_path = config["path_to_repo"]
        self._current_repo_path = repo_path

        # Make sure `results/code_analysis/` exists (legacy parity).
        results_dir = self.get_results_directory()
        code_analysis_dir = f"{results_dir}/code_analysis"
        os.makedirs(code_analysis_dir, exist_ok=True)
        config["analysis_dir"] = code_analysis_dir

        if not api_key:
            self.logger.warning("No API key available — skipping code analysis")
            return 0, 0

        # --- Build the typed context for the session ---
        ctx = AnalysisContext.from_config(
            repo_path=repo_path,
            config=config,
            output_base_dir=output_base_dir,
            api_key=api_key,
        )

        # --- Build a CallTreeBuilder (call-tree is the only code-analysis mode) ---
        # Only when init fails do we fall back to the per-function pipeline.
        call_tree_builder = None
        try:
            call_tree_builder = CallTreeBuilder(
                nested_call_graph=self.call_graph_data,
                repo_path=repo_path,
                max_depth=ctx.call_tree_max_depth,
                max_chars=ctx.call_tree_max_chars,
                max_nodes=ctx.call_tree_max_nodes,
            )
            self.logger.info("Code analysis: call-tree mode enabled")
        except Exception as exc:
            self.logger.warning(
                f"CallTreeBuilder init failed; falling back to per-function mode: {exc}"
            )
            call_tree_builder = None

        # --- Hooks into the legacy components ---
        issue_filter = (
            self.unified_issue_filter.filter_issues
            if self.unified_issue_filter is not None
            else None
        )

        def _token_callback(input_tokens: int, output_tokens: int) -> None:
            """Bridge LLM-stack token usage into the legacy TokenTracker.

            TokenTracker.record_tokens_from_analysis() takes an object with
            `total_input_tokens` / `total_output_tokens` attributes — we shape
            a minimal stub for each call.
            """
            try:
                stub = type("_TokenStub", (), {
                    "total_input_tokens": input_tokens,
                    "total_output_tokens": output_tokens,
                })()
                self.token_tracker.record_tokens_from_analysis(stub)
            except Exception as exc:
                self.logger.debug(f"token_callback failed (non-fatal): {exc}")

        # --- File content provider / directory tree util (preserved) ---
        fcp = self.get_file_content_provider() if hasattr(self, "get_file_content_provider") else None
        dtu = getattr(self, "directory_tree_util", None)

        # --- Filters — resolve from the runner's state (mirrors legacy precedence) ---
        filters = FunctionFilters(
            file_filter=tuple(self.file_filter or ()),
            include_directories=tuple(config.get("include_directories", []) or ()),
            exclude_directories=tuple(config.get("exclude_directories", []) or ()),
            exclude_files=tuple(config.get("exclude_files", []) or ()),
            verified_functions=frozenset(self.verified_functions or set()),
            filtered_functions=frozenset(self.filtered_functions or []),
            filtered_classes=frozenset(self.filtered_classes or []),
            min_function_body_length=int(
                config.get("min_function_body_length", MIN_FUNCTION_BODY_LENGTH)
            ),
            max_function_body_length=int(
                config.get("max_function_body_length", MAX_FUNCTION_BODY_LENGTH)
            ),
        )

        num_to_analyze = config.get("num_functions_to_analyze", DEFAULT_NUM_FUNCTIONS_TO_ANALYZE)

        # Account for already-cached results so we honor the limit globally.
        if self.results_publisher:
            repo_name = os.path.basename(repo_path.rstrip("/"))
            existing = self.results_publisher.get_results(repo_name) or []
            existing_count = len(existing)
            if existing_count >= num_to_analyze:
                self.logger.info(
                    f"Already have {existing_count} results (limit {num_to_analyze}); no new analysis"
                )
                return existing_count, 0
            num_to_analyze = max(0, num_to_analyze - existing_count)

        async def _run_async() -> tuple:
            async with AnalysisSession.create(
                ctx,
                file_content_provider=fcp,
                directory_tree_util=dtu,
            ) as session:
                # Preserve the existing prompts-sent layout — clear once per run.
                session.conversation_logger.clear_older_prompts()

                sink = AsyncResultSink(
                    self.results_publisher,
                    repo_name=ctx.repo_name,
                )
                pipeline = CodePipeline(
                    session,
                    sink,
                    ast_index=getattr(self, "ast_index", None),
                    issue_filter=issue_filter,
                    token_callback=_token_callback,
                )

                summary = await pipeline.analyze_repo(
                    self.call_graph_data,
                    filters,
                    num_to_analyze=num_to_analyze,
                    call_tree_builder=call_tree_builder,
                )
                self.logger.info(
                    f"Async pipeline summary: selected={summary.selected} "
                    f"successful={summary.successful} cached={summary.cached} "
                    f"failed={summary.failed} duration={summary.duration_seconds:.1f}s"
                )
                if summary.error:
                    self.logger.error(f"Async pipeline aborted: {summary.error}")
                return summary.successful + summary.cached, summary.failed

        try:
            successful, failed = asyncio.run(_run_async())
        except Exception as exc:
            self.logger.error(f"Async orchestration crashed: {exc}")
            traceback.print_exc()
            return 0, 0

        self.logger.info(f"Code analysis (async): success={successful}, failed={failed}")
        if self.token_tracker and (successful > 0 or failed > 0):
            self.token_tracker.log_summary()
        return successful, failed

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

    def _writeback_final_issues_to_publisher(
        self,
        final_issues: list,
        repo_name: str,
    ) -> None:
        """Reconcile the publisher's per-result issue lists with the
        deduped/filtered ``final_issues`` set so that later calls to
        ``results_publisher.get_results()`` reflect the same view as the
        HTML report and the on-disk JSONs.

        Without this, downstream consumers (e.g. the analysis-complete
        banner) that re-derive counts from the publisher see the
        pre-filter snapshot instead.

        Identity-based reconciliation is safe here: the issue deduper
        returns ``raw_data`` refs (same dicts, not copies), radar dedup
        annotates in place, and the FP CSV filter returns a subset of
        the input list.  So each surviving issue is the exact same dict
        object stored under ``_results[result_id]['results']``.
        """
        if not self.results_publisher or not repo_name:
            return

        surviving_ids = {id(issue) for issue in final_issues}

        result_ids = self.results_publisher._repo_results.get(repo_name, [])
        total_before = 0
        total_after = 0
        for result_id in result_ids:
            result = self.results_publisher._results.get(result_id)
            if not result or not isinstance(result.get('results'), list):
                continue
            original = result['results']
            total_before += len(original)
            kept = [issue for issue in original if id(issue) in surviving_ids]
            total_after += len(kept)
            result['results'] = kept

        if total_before != total_after:
            self.logger.info(
                f"Publisher writeback: {total_before} -> {total_after} issues "
                f"({total_before - total_after} filtered) across "
                f"{len(result_ids)} results for repo '{repo_name}'"
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
                        analyzer_type="code_analysis",
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

            # Radar issue deduplication (optional, triggered by --issue-dedupe)
            issue_dedupe_keyword = config.get('issue_dedupe_keyword')
            if issue_dedupe_keyword and all_issues:
                try:
                    self.logger.info(f"Running radar deduplication with keyword: '{issue_dedupe_keyword}'")
                    all_issues = self._run_radar_deduplication(all_issues, issue_dedupe_keyword)
                except Exception as e:
                    self.logger.warning(f"Radar deduplication failed, continuing without it: {e}")

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

            # ── Reconcile publisher's in-memory results with final set ─────
            # After dedup/radar-dedup/FP-CSV filtering, the publisher still
            # holds the pre-filter results.  Reconcile so any consumer that
            # queries the publisher after this point (e.g. the analysis
            # banner) sees the same view as the report.
            self._writeback_final_issues_to_publisher(all_issues, repo_name)

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

    def _run_radar_deduplication(self, all_issues: list, keyword: str) -> list:
        """
        Run radar issue deduplication: download radars by keyword, ingest, and match.

        Annotates each issue dict with a 'radar_matches' key containing match data.
        Returns the same list of issues (possibly annotated). If anything fails,
        raises an exception that the caller should catch.
        """
        import hashlib
        from pathlib import Path

        output_provider = get_output_directory_provider()
        artifacts_dir = output_provider.get_repo_artifacts_dir()

        safe_keyword = keyword.replace(' ', '_').replace('/', '_')
        radar_dupes_dir = os.path.join(artifacts_dir, 'radar_dupes', safe_keyword)
        issue_download_dir = os.path.join(radar_dupes_dir, 'issues')
        vector_db_dir = os.path.join(radar_dupes_dir, 'vector_db')
        os.makedirs(issue_download_dir, exist_ok=True)
        os.makedirs(vector_db_dir, exist_ok=True)

        self.logger.info(f"Radar dupes directory: {radar_dupes_dir}")

        # Step 1: Download radars
        from hindsight.dedupers.issue_tracking_deduper.issue_helper import IssueDownloader
        downloader = IssueDownloader(
            output_dir=str(issue_download_dir),
            client_name='CodeAnalyzerRadarDedupe'
        )
        downloaded = downloader.download_issues_by_keyword(
            keyword=keyword,
            rate_limit_delay=0.1
        )
        self.logger.info(f"Downloaded {len(downloaded)} new radars (keyword: '{keyword}')")

        # Step 2: Ingest into vector DB
        from hindsight.dedupers.issue_tracking_deduper.issue_tracking_deduper.vector_db.ingestion import IssueIngester
        ingester = IssueIngester(db_path=str(vector_db_dir))
        total, added, skipped = ingester.ingest_directory(
            Path(issue_download_dir),
            recursive=True,
            show_progress=False
        )
        self.logger.info(f"Ingestion: {total} files processed, {added} added, {skipped} skipped")
        ingester.close()

        # Step 3: Match issues
        from hindsight.dedupers.issue_tracking_deduper.issue_tracking_deduper.vector_db.store import VectorStore
        from hindsight.dedupers.issue_tracking_deduper.issue_tracking_deduper.vector_db.embeddings import EmbeddingGenerator
        from hindsight.dedupers.issue_tracking_deduper.issue_tracking_deduper.deduper.hybrid_matcher import HybridMatcher
        from hindsight.dedupers.issue_tracking_deduper.issue_tracking_deduper.deduper.issue import Issue

        vector_store = VectorStore(db_path=str(vector_db_dir))
        if vector_store.count() == 0:
            self.logger.warning("Vector DB is empty after ingestion, skipping radar matching")
            vector_store.close()
            return all_issues

        embedding_generator = EmbeddingGenerator()
        matcher = HybridMatcher(
            vector_store=vector_store,
            embedding_generator=embedding_generator,
        )

        issues_with_matches = 0
        total_matches = 0

        for idx, issue_dict in enumerate(all_issues):
            key_parts = [
                issue_dict.get('issue', ''),
                issue_dict.get('file_path', ''),
                issue_dict.get('function_name', ''),
                str(issue_dict.get('lines', ''))
            ]
            issue_id = f"code_{idx}_{hashlib.md5('|'.join(key_parts).encode()).hexdigest()[:8]}"

            deduper_issue = Issue(
                id=issue_id,
                title=issue_dict.get('issue', ''),
                description=issue_dict.get('description', issue_dict.get('issue', '')),
                file_path=issue_dict.get('file_path'),
                function_name=issue_dict.get('function_name'),
                severity=issue_dict.get('severity'),
                category=issue_dict.get('category'),
            )

            matches = matcher.find_matches(deduper_issue)

            if matches:
                issues_with_matches += 1
                total_matches += len(matches)
                issue_dict['radar_matches'] = [
                    {
                        'issueId': m.issue_id,
                        'issueUrl': m.issue_url,
                        'issueTitle': m.issue_title,
                        'hybridScore': m.hybrid_percentage,
                        'confidenceLevel': m.confidence_level,
                        'filePathScore': m.file_path_percentage,
                        'functionNameScore': m.function_name_percentage,
                        'cosineScore': m.cosine_similarity_percentage,
                        'matchReasons': m.match_reasons,
                    }
                    for m in matches
                ]

        vector_store.close()

        self.logger.info(
            f"Radar deduplication complete: {issues_with_matches}/{len(all_issues)} issues "
            f"matched, {total_matches} total matches"
        )

        return all_issues

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
            
            # Filters may write dropped-issue JSONs either directly into
            # dropped_issues/ or nested in level{1,2,3}_* subdirectories.
            # Walk both layouts so no dropped issues are silently omitted.
            for entry_name in sorted(os.listdir(dropped_issues_base_dir)):
                entry_path = os.path.join(dropped_issues_base_dir, entry_name)

                if os.path.isfile(entry_path) and entry_name.endswith('.json'):
                    try:
                        with open(entry_path, 'r', encoding='utf-8') as f:
                            dropped_issues.append(json.load(f))
                    except (json.JSONDecodeError, IOError) as e:
                        self.logger.warning(f"Failed to read dropped issue file {entry_path}: {e}")
                    continue

                if not os.path.isdir(entry_path):
                    continue

                for filename in os.listdir(entry_path):
                    if not filename.endswith('.json'):
                        continue

                    file_path = os.path.join(entry_path, filename)
                    try:
                        with open(file_path, 'r', encoding='utf-8') as f:
                            dropped_issues.append(json.load(f))
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

            # Load call graph to build current checksum map for stale result detection
            current_checksums = None
            repo_path = config['path_to_repo']
            ast_call_graph_dir = config.get('astCallGraphDir', '')
            if ast_call_graph_dir:
                nested_call_graph_path = os.path.join(ast_call_graph_dir, NESTED_CALL_GRAPH_FILE)
                if os.path.exists(nested_call_graph_path):
                    call_graph_data = read_json_file(nested_call_graph_path)
                    if call_graph_data and isinstance(call_graph_data, list):
                        self.call_graph_data = call_graph_data
                        current_checksums = self._build_current_checksum_map(repo_path)
                        self.logger.info(f"Built checksum map with {len(current_checksums)} entries for stale result detection")
                else:
                    self.logger.warning(f"Call graph not found at {nested_call_graph_path} - cannot detect stale results")

            # Initialize publisher-subscriber system to load existing results
            self.logger.info("Initializing publisher-subscriber system for existing results...")
            self._initialize_publisher_subscriber_for_report(config, output_base_dir,
                                                             current_checksums=current_checksums)

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
            max_workers: int = None,
            max_analysis_workers: int = 1):

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
            max_workers: Maximum number of worker processes for parallel AST generation (default: None, uses system default)
            max_analysis_workers: Number of functions analyzed concurrently — the LLM analysis fan-out. Sets config['max_analysis_workers'] (default: 1)
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
        self.logger.info(f"  max_analysis_workers: {max_analysis_workers}")

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

            # Set the analysis fan-out (concurrent function analyses). This
            # becomes AnalysisContext.max_workers, which bounds the pipeline's
            # bounded_gather concurrency. CLI-authoritative; default is 1.
            config['max_analysis_workers'] = max_analysis_workers
            self.logger.info(f"Using max_analysis_workers: {max_analysis_workers}")

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

            # Prompt logging is handled per-session by `ConversationLogger`
            # (see `hindsight.orchestration.session.AnalysisSession.create`).
            # Each run clears `prompts_sent/code_analysis/` before writing
            # new transcripts.
            output_provider = get_output_directory_provider()
            actual_prompts_dir = f"{output_provider.get_repo_artifacts_dir()}/prompts_sent/code_analysis"
            self.logger.info(f"Prompt logging directory: {actual_prompts_dir}")

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
                results, summary = self._process_call_graph(config, nested_call_graph_path)

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
                results, summary = self._process_call_graph(config, nested_call_graph_path)
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


def _default_text_file_output(project_name: str) -> str:
    """Default HTML output path for the text-file re-rendering flow."""
    safe_name = project_name.replace(" ", "_")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return os.path.expanduser(
        f"~/llm_artifacts/from_text/repo_analysis_{safe_name}_{timestamp}_from_text.html"
    )


def _run_generate_from_text_file(args) -> None:
    """Re-render an HTML report from a Copy-All text dump.

    Pure rendering path — no LLM, no AST, no artifacts cache. Exits the
    process via sys.exit(1) on user-facing errors so the CLI keeps a single
    failure mode regardless of where the error originates.
    """
    if args.generate_report_from_existing_issues:
        logger.error(
            "--generate-from-text-file and --generate-report-from-existing-issues are mutually exclusive"
        )
        sys.exit(1)
    if not os.path.isfile(args.generate_from_text_file):
        logger.error("Input text file not found: %s", args.generate_from_text_file)
        sys.exit(1)

    from ..report.text_file_issue_parser import parse_issues_text_file

    try:
        issues = parse_issues_text_file(args.generate_from_text_file)
    except ValueError as e:
        logger.error("Failed to parse text file: %s", e)
        sys.exit(1)

    if not issues:
        logger.error("No issues found in %s", args.generate_from_text_file)
        sys.exit(1)

    project_name = (
        args.text_file_project_name
        or os.path.basename(args.repo.rstrip("/"))
    )
    out_path = args.text_file_output or _default_text_file_output(project_name)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    report_path = generate_html_report(
        issues,
        output_file=out_path,
        project_name=project_name,
        analysis_type="Code Analysis",
    )
    logger.info("HTML report written to: %s", report_path)


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

%(prog)s --config config.json --repo /path/to/repo
        """
    )
    parser.add_argument(
        "--config", "-c",
        help="Path to configuration file (required unless --generate-from-text-file is used)"
    )
    parser.add_argument(
        "--repo", "-r",
        help="Path to repository directory (required unless --generate-from-text-file is used)"
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
        "--generate-from-text-file",
        metavar="PATH",
        help="Path to a text file produced by the report's 'Copy All' button. "
             "Re-renders an HTML report from the text without running analysis. "
             "Mutually exclusive with --generate-report-from-existing-issues."
    )
    parser.add_argument(
        "--text-file-project-name",
        metavar="NAME",
        help="Optional repository label for the regenerated report header. "
             "If omitted, derived from the --repo directory name."
    )
    parser.add_argument(
        "--text-file-output",
        metavar="PATH",
        help="Optional output HTML path. Default: ~/llm_artifacts/from_text/"
             "repo_analysis_<project>_<timestamp>_from_text.html"
    )
    parser.add_argument(
        "--issue-dedupe",
        type=str,
        default=None,
        metavar="KEYWORD",
        help="Run radar deduplication: download radars matching KEYWORD, then highlight matching issues in the HTML report"
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
        "--max-ast-workers",
        type=int,
        default=None,
        help="Maximum number of worker processes for parallel AST generation (default: 4 or CPU count, whichever is smaller)"
    )
    parser.add_argument(
        "--max-analysis-workers",
        type=int,
        default=1,
        help="Number of functions analyzed concurrently (LLM analysis fan-out). Overrides 'max_analysis_workers' in the config. Default: 1"
    )

    args = parser.parse_args()

    # --generate-from-text-file is a pure re-rendering path. It does not need
    # --config, but it does need --repo so the report header shows the correct
    # repository name. Exits before any analysis infra is set up.
    if args.generate_from_text_file:
        if not args.repo:
            logger.error("Error: --repo is required when using --generate-from-text-file")
            sys.exit(1)
        _run_generate_from_text_file(args)
        sys.exit(0)

    if not args.config:
        logger.error("Error: --config is required (unless using --generate-from-text-file)")
        sys.exit(1)
    if not args.repo:
        logger.error("Error: --repo is required (unless using --generate-from-text-file)")
        sys.exit(1)

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
    logger.debug(f"Creating FileSystemResultsCache with base_dir='{args.out_dir}'")
    default_prior_store = FileSystemResultsCache(args.out_dir)

    # Initialize the store for this repository to build the result index
    logger.debug(f"Initializing FileSystemResultsCache for repo='{repo_name}'")
    default_prior_store.initialize_for_repo(repo_name)

    logger.debug(f"Registering FileSystemResultsCache with runner")
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

        config['issue_dedupe_keyword'] = args.issue_dedupe

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
                max_workers=args.max_ast_workers,
                max_analysis_workers=args.max_analysis_workers,
                )

    # Generate report after analysis completes (for standalone usage)
    logger.info("\n\n=== REPORT GENERATION ===")
    
    # Prepare config for report generation
    report_config = config.copy()
    report_config['path_to_repo'] = args.repo
    report_config['issue_dedupe_keyword'] = args.issue_dedupe
    
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
