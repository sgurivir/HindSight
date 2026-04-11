#!/usr/bin/env python3
# Created by Sridhar Gurivireddy
"""
Base class for analysis runners with common functionality
"""


import json
import orjson
import os
import platform
import shutil
import sys
import time
from pathlib import Path
from threading import Lock
from typing import Optional, List

from clang import cindex

from .analytics_helper import AnalyticsHelper
from ..core.constants import (
    DEFAULT_MAX_TOKENS, DEFAULT_API_RATE_LIMIT,
    DEFAULT_RATE_LIMIT_WINDOW, NESTED_CALL_GRAPH_FILE, MERGED_SYMBOLS_FILE, MERGED_DEFINED_CLASSES_FILE,
    PROCESSED_OUTPUT_DIR, CANCELLATION_CHECK_INTERVAL,
    CALL_TREE_JSON_FILE, CALL_TREE_TEXT_FILE
)


from ..core.lang_util.ast_function_signature_util import ASTFunctionSignatureGenerator
from ..core.lang_util.ast_util import ASTUtil
from ..core.lang_util.call_tree_util import CallTreeGenerator
from ..core.lang_util.cast_util import CASTUtil

from ..core.lang_util.Environment import Environment
from ..core.lang_util.all_supported_extensions import ALL_SUPPORTED_EXTENSIONS
from ..core.llm.llm import Claude, ClaudeConfig
from ..report.issue_directory_organizer import RepositoryDirHierarchy
from ..utils.directory_tree_util import DirectoryTreeUtil
from ..utils.file_content_provider import FileContentProvider
from ..utils.file_util import get_artifacts_temp_file_path
from ..utils.log_util import setup_default_logging, get_logger
from ..utils.output_directory_provider import get_output_directory_provider
from ..utils.sleep_util import setup_signal_handlers, SleepPrevention


# Add the project root to Python path for imports
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))


class AnalysisRunner:
    """Base class for analysis runners with common functionality."""

    def __init__(self):
        """Initialize the runner with logging setup."""
        # Initial logging setup - will be reconfigured if custom output dir is specified
        # Use current working directory as initial repo path
        setup_default_logging(repo_path=os.getcwd())
        self.logger = get_logger(__name__)

        # Rate limiting
        self.api_requests = []  # List of request timestamps
        self.rate_limit_lock = Lock()

        # Store config file path for use in methods
        self.config_file = None

        # Token tracking is now handled by centralized TokenTracker in code_analyzer.py
        self.token_tracker = None

        # Sleep prevention
        self.sleep_prevention = None

        # Analytics helper (will be initialized when needed)
        self.analytics_helper = None

        # Project summary generator removed - no longer used

        # Directory structure cache
        self._directory_structure_cache = {}

        # Initialize DirectoryTreeUtil
        self.directory_tree_util = DirectoryTreeUtil()

        # Publisher-subscriber system (will be initialized by subclasses)
        self.results_publisher = None

        # User-provided prompts for analysis (optional list)
        self.user_provided_prompts = []
        
        # Cancellation support for cooperative cancellation
        self._cancellation_checker = None
        self._cancellation_check_interval = CANCELLATION_CHECK_INTERVAL

    def _get_file_mapping_paths(self):
        """
        Get the file mapping paths for pickle and JSON files.

        Returns:
            tuple: (file_mapping_index_path, file_mapping_json_path)
        """
        output_provider = get_output_directory_provider()
        artifacts_dir = output_provider.get_repo_artifacts_dir()
        file_mapping_index = f"{artifacts_dir}/code_insights/file_mapping.pkl"
        file_mapping_json = f"{artifacts_dir}/code_insights/file_mapping.json"
        return file_mapping_index, file_mapping_json

    def get_results_directory(self) -> str:
        """
        Get the results directory path under REPO_IQ_ARTIFACTS_DIR or custom base directory.
        Creates the directory if it doesn't exist.

        Returns:
            str: Path to the results directory
        """
        output_provider = get_output_directory_provider()
        artifacts_dir = output_provider.get_repo_artifacts_dir()
        results_dir = f"{artifacts_dir}/results"

        # Create the directory if it doesn't exist
        os.makedirs(results_dir, exist_ok=True)

        return results_dir

    def get_reports_directory(self) -> str:
        """
        Get the html_reports directory path for HTML reports.
        Creates the directory if it doesn't exist.

        Returns:
            str: Path to the html_reports directory
        """
        results_dir = self.get_results_directory()
        reports_dir = f"{results_dir}/html_reports"

        # Create the directory if it doesn't exist
        os.makedirs(reports_dir, exist_ok=True)

        return reports_dir

    def create_file_content_provider(self, repo_path_obj):
        """
        Create or load FileContentProvider singleton instance for the repository.
        This is the ONLY method that should create FileContentProvider instances.

        Args:
            repo_path_obj: Path object for the repository

        Returns:
            FileContentProvider: Configured FileContentProvider singleton instance
        """
        # Check if FileContentProvider is already initialized
        try:
            existing_instance = FileContentProvider.get()
            self.logger.info("FileContentProvider singleton already initialized, reusing existing instance")
            return existing_instance
        except RuntimeError:
            # FileContentProvider not initialized yet, proceed with creation
            pass

        file_mapping_index, _ = self._get_file_mapping_paths()

        # Check if existing file mapping can be reused
        if os.path.exists(file_mapping_index):
            self.logger.info("Reusing existing FileContentProvider mapping")
            try:
                file_content_provider = FileContentProvider.from_index(file_mapping_index)
                self.logger.info("Successfully loaded existing FileContentProvider index")
                return file_content_provider
            except Exception as e:
                self.logger.warning(f"Failed to load existing FileContentProvider index: {e}")
                self.logger.info("Rebuilding FileContentProvider mapping...")

        # Create new FileContentProvider singleton using simplified API
        self.logger.info("Building FileContentProvider mapping...")
        file_content_provider = FileContentProvider.from_repo(str(repo_path_obj))

        self.logger.info("FileContentProvider singleton created and initialized successfully")
        return file_content_provider

    @classmethod
    def get_file_content_provider(cls):
        """
        Get the FileContentProvider singleton instance.
        This method should be used by other classes to access FileContentProvider.

        Returns:
            FileContentProvider: The singleton instance

        Raises:
            RuntimeError: If FileContentProvider has not been initialized
        """
        try:
            return FileContentProvider.get()
        except RuntimeError as e:
            raise RuntimeError(
                "FileContentProvider has not been initialized. "
                "It should be created in AnalysisRunner.create_file_content_provider() first."
            ) from e

    def get_default_ast_output_paths(self) -> dict:
        """
        Get default output paths for AST-related files.

        Returns:
            Dictionary containing default paths for AST files
        """
        output_provider = get_output_directory_provider()
        artifacts_dir = output_provider.get_repo_artifacts_dir()
        code_insights_dir = os.path.join(artifacts_dir, "code_insights")

        return {
            'merged_functions_file': os.path.join(code_insights_dir, MERGED_SYMBOLS_FILE),
            'merged_graph_file': os.path.join(code_insights_dir, NESTED_CALL_GRAPH_FILE),
            'merged_data_types_file': os.path.join(code_insights_dir, MERGED_DEFINED_CLASSES_FILE),
            'code_insights_dir': code_insights_dir
        }

    def get_complete_ast_files_config(self, repo_path: str, output_base_dir: str = None) -> dict:
        """
        Get complete AST files configuration combining AST output paths and trace analysis paths.
        Note: This method now requires the calling class to have get_default_trace_analysis_paths method
        (typically TraceAnalysisRunner) or it will fall back to AST paths only.

        Args:
            repo_path: Path to the repository
            output_base_dir: Optional output base directory

        Returns:
            Dictionary containing complete AST files configuration
        """
        ast_paths = self.get_default_ast_output_paths()

        # Try to get trace paths if the method exists (for TraceAnalysisRunner)
        if hasattr(self, 'get_default_trace_analysis_paths'):
            trace_paths = self.get_default_trace_analysis_paths(repo_path, output_base_dir)
            output_dir = trace_paths['trace_analysis_dir']
        else:
            # Fallback for classes that don't have trace analysis paths (like CodeAnalysisRunner)
            output_dir = ast_paths['code_insights_dir']

        return {
            'merged_functions_file': ast_paths['merged_functions_file'],
            'merged_graph_file': ast_paths['merged_graph_file'],
            'merged_data_types_file': ast_paths['merged_data_types_file'],
            'output_dir': output_dir,
            'code_insights_dir': ast_paths['code_insights_dir']
        }


    def _start_sleep_prevention(self) -> bool:
        """
        Start sleep prevention to keep Mac awake during long-running operations.

        Returns:
            bool: True if successfully started, False otherwise
        """
        try:
            self.sleep_prevention = SleepPrevention(
                prevent_display_sleep=True,
                prevent_system_sleep=True,
                prevent_idle_sleep=True
            )

            success = self.sleep_prevention.start()
            if success:
                self.logger.info("Sleep prevention activated - Mac will stay awake during analysis")
                # Set up signal handlers for graceful shutdown
                setup_signal_handlers(self.sleep_prevention)
            else:
                self.logger.warning("Failed to activate sleep prevention - continuing without it")
                self.sleep_prevention = None

            return success

        except Exception as e:
            self.logger.warning(f"Could not start sleep prevention: {e}")
            self.sleep_prevention = None
            return False

    def _stop_sleep_prevention(self) -> None:
        """Stop sleep prevention and allow Mac to sleep normally."""
        if self.sleep_prevention:
            try:
                self.sleep_prevention.stop()
                self.logger.info("Sleep prevention deactivated - Mac can now sleep normally")
            except Exception as e:
                self.logger.warning(f"Error stopping sleep prevention: {e}")
            finally:
                self.sleep_prevention = None

    def _wait_for_rate_limit(self) -> float:
        """
        Implement rate limiting - wait if necessary to stay within API limits

        Returns:
            float: Time waited in seconds
        """
        with self.rate_limit_lock:
            current_time = time.time()

            # Remove requests older than the rate limit window
            self.api_requests = [req_time for req_time in self.api_requests
                               if current_time - req_time < DEFAULT_RATE_LIMIT_WINDOW]

            # Check if we need to wait
            if len(self.api_requests) >= DEFAULT_API_RATE_LIMIT:
                # Find the oldest request in the current window
                oldest_request = min(self.api_requests)
                # Wait for exactly the rate limit window from the first request
                wait_time = DEFAULT_RATE_LIMIT_WINDOW - (current_time - oldest_request)

                if wait_time > 0:
                    self.logger.info(f"Rate limit reached ({len(self.api_requests)}/{DEFAULT_API_RATE_LIMIT}), "
                                   f"waiting {wait_time:.1f} seconds...")
                    time.sleep(wait_time)

                    # After waiting, clean up old requests again
                    current_time = time.time()
                    self.api_requests = [req_time for req_time in self.api_requests
                                       if current_time - req_time < DEFAULT_RATE_LIMIT_WINDOW]

                    return wait_time

            # Record this request
            self.api_requests.append(current_time)
            return 0.0

    # _log_grand_total_token_summary method removed - now handled by centralized TokenTracker

    def _run_summary_generation(self, config: dict, api_key: str = None) -> bool:
        """
        Initialize summary service for on-demand operation.
        No longer generates summaries on startup - summaries are created when requested by LLM.

        Args:
            config: Configuration dictionary
            api_key: API key for LLM calls

        Returns:
            bool: True (always successful since no startup generation)
        """
        self.logger.info("Initializing summary service for on-demand operation...")

        if not api_key:
            self.logger.info("No API key available - summary service will be available but summaries won't be generated")
            return True

        try:
            # Create case variants for directories to ignore (same as AST generation)
            base_dirs_to_ignore = config.get('exclude_directories', [])
            ignore_dirs = set()
            for dir_name in base_dirs_to_ignore:
                # Add original case (given case)
                ignore_dirs.add(dir_name)
                # Add uppercase variant
                ignore_dirs.add(dir_name.upper())
                # Add lowercase variant
                ignore_dirs.add(dir_name.lower())

            self.logger.info("Summaries will be generated when requested by LLM during analysis")

            return True

        except Exception as e:
            self.logger.error(f"Error initializing summary service: {e}")
            self.logger.warning("Continuing with analysis despite summary service initialization failure")
            return True  # Don't block analysis due to summary service errors

    def _run_early_summary_generation(self) -> bool:
        """
        Initialize early summary service for on-demand operation.
        This is called early in the pipeline, before AST generation, so it cannot initialize
        the ProjectSummaryGenerator singleton yet (merged_functions.json doesn't exist).

        Returns:
            bool: True (always successful since no startup generation)
        """
        self.logger.info("Early summary generation step - ProjectSummaryGenerator singleton will be initialized later after AST generation")
        return True

    # ProjectSummaryGenerator methods removed - no longer used

    def _ensure_directory_structure_index(self, repo_path: str) -> str:
        """
        Ensure directory structure index exists and return the cached structure.

        Args:
            repo_path: Path to the repository

        Returns:
            str: Directory structure as formatted tree string
        """
        # Check if we already have it cached in memory
        if repo_path in self._directory_structure_cache:
            return self._directory_structure_cache[repo_path]

        self.logger.info("Building directory structure index...")

        try:
            # Use the static method to get directory structure (handles caching automatically)
            # The method will use the OutputDirectoryProvider singleton internally
            structure = RepositoryDirHierarchy.get_directory_structure_for_repo(repo_path)

            # Cache in memory for this session
            self._directory_structure_cache[repo_path] = structure

            self.logger.info("Directory structure index built successfully")
            return structure

        except Exception as e:
            self.logger.error(f"Failed to build directory structure index: {e}")
            # Return a basic fallback structure
            repo_name = os.path.basename(repo_path.rstrip('/'))
            return f"{repo_name}/\n|- (Error building directory structure)"

    def _start_analytics_session(self, repo_path: str) -> None:
        """Start analytics tracking session."""
        try:
            # Get or create analytics helper singleton
            self.analytics_helper = AnalyticsHelper.get_instance(repo_path=repo_path)

            # Start session for this analysis run
            session_id = self.analytics_helper.start_session(repo_path)
            self.logger.info(f"Analytics session started: {session_id}")

        except Exception as e:
            self.logger.warning(f"Failed to start analytics session: {e}")
            self.analytics_helper = None

    def _record_token_usage(self, tokens_used: int, retry_errors: int = 0,
                           cost_usd: float = 0.0, duration_seconds: float = 0.0) -> None:
        """Record token usage for analytics."""
        if self.analytics_helper:
            try:
                self.analytics_helper.record_token_usage(
                    tokens_used=tokens_used,
                    retry_errors=retry_errors,
                    cost_usd=cost_usd,
                    duration_seconds=duration_seconds
                )
            except Exception as e:
                self.logger.warning(f"Failed to record token usage: {e}")

    def _start_function_analysis_tracking(self, function_data: str = None) -> None:
        """Start tracking a function analysis operation."""
        if self.analytics_helper:
            try:
                self.analytics_helper.start_function_analysis(function_data)
            except Exception as e:
                self.logger.error(f"Failed to start function analysis tracking: {e}")

    def _record_function_analysis_result(self, functions_analyzed: int, success: bool,
                                       function_data: str = None) -> None:
        """Record the result of function analysis."""
        if self.analytics_helper:
            try:
                self.analytics_helper.record_function_analysis_result(
                    functions_analyzed=functions_analyzed,
                    success=success,
                    function_data=function_data
                )
            except Exception as e:
                self.logger.warning(f"Failed to record function analysis result: {e}")

    def _end_analytics_session(self) -> None:
        """End analytics tracking session and display summary."""
        if self.analytics_helper:
            try:
                self.analytics_helper.end_session()
            except Exception as e:
                self.logger.warning(f"Failed to end analytics session: {e}")

    def get_directory_structure(self, repo_path: str, directory_path: str = None) -> str:
        """
        Get directory structure for the repository or a specific directory.

        Args:
            repo_path: Path to the repository
            directory_path: Optional specific directory path (relative to repo root)

        Returns:
            str: Directory structure as formatted tree string
        """
        # Ensure the index exists
        self._ensure_directory_structure_index(repo_path)

        if directory_path:
            # Get structure for specific directory
            try:
                hierarchy = RepositoryDirHierarchy(repo_path)
                return hierarchy.get_directory_hierarchy_by_path(directory_path) or "Directory not found"
            except Exception as e:
                self.logger.error(f"Error getting directory structure for {directory_path}: {e}")
                return f"Error: Could not retrieve directory structure for {directory_path}"
        else:
            # Return cached full structure
            return self._directory_structure_cache.get(repo_path, "Directory structure not available")


    def _check_existing_ast_files(self, config: dict) -> bool:
        """Check if AST call graph files already exist."""
        ast_call_graph_dir = config['astCallGraphDir']

        # Check for key AST files that are generated during AST creation
        nested_call_graph_path = os.path.join(ast_call_graph_dir, NESTED_CALL_GRAPH_FILE)
        merged_symbols_path = os.path.join(ast_call_graph_dir, MERGED_SYMBOLS_FILE)
        merged_classes_path = os.path.join(ast_call_graph_dir, MERGED_DEFINED_CLASSES_FILE)

        # Check if core AST files exist (don't check processed_output_dir as it's created during analysis, not AST generation)
        try:
            files_exist = (
                os.path.exists(nested_call_graph_path) and
                os.path.exists(merged_symbols_path) and
                os.path.exists(merged_classes_path) and
                os.path.getsize(nested_call_graph_path) > 0 and
                os.path.getsize(merged_symbols_path) > 0 and
                os.path.getsize(merged_classes_path) > 0
            )
            
            if files_exist:
                self.logger.info(f"Found existing AST files:")
                self.logger.info(f"  - {nested_call_graph_path} ({os.path.getsize(nested_call_graph_path)} bytes)")
                self.logger.info(f"  - {merged_symbols_path} ({os.path.getsize(merged_symbols_path)} bytes)")
                self.logger.info(f"  - {merged_classes_path} ({os.path.getsize(merged_classes_path)} bytes)")
            else:
                self.logger.info("AST files missing or empty - will need to generate")
                
        except (OSError, FileNotFoundError) as e:
            self.logger.debug(f"Error checking AST files: {e}")
            files_exist = False

        return files_exist

    def get_enhanced_exclude_directories(self, repo_path: str,
                                         config: dict,
                                         user_provided_include_list: Optional[List[str]] = None,
                                         user_provided_exclude_list: Optional[List[str]] = None) -> List[str]:
        """
        Get enhanced directory exclusions using both static analysis and LLM-based recommendations.
        
        This method combines:
        1. User-provided exclude directories (from config and parameters)
        2. LLM-based directory analysis (fault-tolerant)
        3. Static directory classification as fallback
        
        Args:
            repo_path: Path to the repository root
            config: Configuration dictionary containing LLM settings
            user_provided_include_list: Optional list of directory names or relative paths to include
            user_provided_exclude_list: Optional list of directory names to exclude in addition to defaults
            
        Returns:
            List of relative paths that should be excluded from analysis
        """
        from ..utils.log_util import get_logger
        from ..utils.config_util import get_llm_provider_type
        
        logger = get_logger(__name__)
        
        # Start with user-provided exclusions
        base_exclusions = set(user_provided_exclude_list or [])
        
        # Get static directory classification as baseline
        try:
            from .directory_classifier import DirectoryClassifier
            static_exclusions = DirectoryClassifier.get_recommended_exclude_directories_safe(
                repo_path, user_provided_include_list, user_provided_exclude_list
            )
            base_exclusions.update(static_exclusions)
            logger.info(f"Static directory analysis found {len(static_exclusions)} directories to exclude")
        except Exception as e:
            logger.warning(f"Static directory analysis failed: {e}")
        
        # Try LLM-based enhancement if provider is not dummy
        llm_provider_type = get_llm_provider_type(config)
        if llm_provider_type != "dummy":
            try:
                logger.info("Attempting LLM-based directory analysis for enhanced exclusions...")
                
                # Lazy import to avoid circular dependency
                from .directory_classifier import LLMBasedDirectoryClassifier
                from ..utils.config_util import get_api_key_from_config
                
                # Get API key
                api_key = get_api_key_from_config(config)
                if not api_key:
                    logger.warning("No API key available for LLM-based directory analysis, using static analysis only")
                    return list(base_exclusions)
                
                # Create LLM classifier
                llm_classifier = LLMBasedDirectoryClassifier.from_config(config)
                
                # Use base exclusions as already excluded directories
                already_excluded = list(base_exclusions)
                
                # Get LLM recommendations
                # Pass user_provided_include_list so LLM knows not to exclude those directories
                llm_exclusions = llm_classifier.analyze_directories(
                    repo_path=repo_path,
                    subdirectories=None,  # Let it discover all directories
                    already_excluded_directories=already_excluded,
                    user_provided_include_list=user_provided_include_list
                )
                
                if llm_exclusions:
                    logger.info(f"LLM analysis recommended {len(llm_exclusions)} additional directories for exclusion")
                    base_exclusions.update(llm_exclusions)
                else:
                    logger.info("LLM analysis completed but found no additional directories to exclude")
                    
            except Exception as e:
                logger.warning(f"LLM-based directory analysis failed (using static analysis only): {e}")
                # Continue with static analysis results
        else:
            logger.info("Using dummy LLM provider - skipping LLM-based directory analysis")
        
        final_exclusions = list(base_exclusions)
        logger.info(f"Enhanced directory analysis complete: {len(final_exclusions)} total directories to exclude")
        
        return final_exclusions

    def _generate_ast_call_graph(self, config: dict, use_parallel: bool = True, max_workers: int = None) -> str:
        """Generate AST call graph using ASTUtil for both Objective-C and Swift.
        
        Args:
            config: Configuration dictionary
            use_parallel: Whether to use parallel processing for AST generation (default: True)
            max_workers: Maximum number of worker processes for parallel processing (default: None, uses system default)
            
        Returns:
            str: Path to the merged call graph file
        """
        self.logger.info("Starting AST call graph generation...")

        # Extract configuration values
        repo_path = Path(config['path_to_repo']).resolve()

        # Where to store AST artifacts
        ast_call_graph_dir = config['astCallGraphDir']
        clang_args = config.get('clangArgs')

        # Use pre-computed exclusions from config (set by DirectoryClassifier in run())
        self.logger.info("=== USING PRE-COMPUTED DIRECTORY EXCLUSIONS ===")
        
        include_dirs = config.get('include_directories') or []
        all_dirs_to_ignore = config.get('exclude_directories', [])
        
        # Get preprocessor macros from config (optional)
        enable_preprocessor_macros = config.get('enable_preprocessor_macros', [])
        if enable_preprocessor_macros:
            self.logger.info(f"Preprocessor macros to enable: {enable_preprocessor_macros}")
        
        self.logger.info(f"Include directories: {include_dirs}")
        self.logger.info(f"Exclude directories ({len(all_dirs_to_ignore)}): {sorted(all_dirs_to_ignore)[:10]}{'...' if len(all_dirs_to_ignore) > 10 else ''}")

        # Create output directory if it doesn't exist
        os.makedirs(ast_call_graph_dir, exist_ok=True)

        # Define output file paths
        merged_symbols_out = Path(ast_call_graph_dir) / MERGED_SYMBOLS_FILE
        merged_graph_out = Path(ast_call_graph_dir) / NESTED_CALL_GRAPH_FILE

        # Define class output file paths (automatically generate class definitions)
        merged_classes_out = Path(ast_call_graph_dir) / MERGED_DEFINED_CLASSES_FILE

        # Run full analysis using ASTUtil for call graphs and classes
        self.logger.info("Building a nested AST call graph for all languages. Please wait...\n")

        # Create language artifacts directory
        language_artifacts_dir = Path(ast_call_graph_dir) / "language_artifacts"
        language_artifacts_dir.mkdir(parents=True, exist_ok=True)

        # Default to in-process AST generation
        use_subprocess = False
        self.logger.info("Using in-process AST generation (default behavior)")
        
        # Log parallel processing settings
        if use_parallel:
            self.logger.info(f"Parallel AST generation ENABLED (max_workers={max_workers or 'auto'})")
        else:
            self.logger.info("Parallel AST generation DISABLED")
        
        ASTUtil.run_full_analysis(
            repo=repo_path,
            include_dirs=include_dirs,
            ignore_dirs=all_dirs_to_ignore,
            clang_args=clang_args,
            out_dir=language_artifacts_dir,
            merged_symbols_out=merged_symbols_out,
            merged_graph_out=merged_graph_out,
            merged_data_types_out=merged_classes_out,
            use_subprocess=use_subprocess,
            enable_preprocessor_macros=enable_preprocessor_macros,
            use_parallel=use_parallel,
            max_workers=max_workers
        )

        self.logger.info(f"AST call graph generation completed. Output saved to: {ast_call_graph_dir}")

        # Generate call tree from the call graph
        self._generate_call_tree_from_call_graph(str(merged_graph_out), ast_call_graph_dir)

        # Return the merged nested call graph path for processing
        return str(merged_graph_out)

    def _generate_call_tree_from_call_graph(self, call_graph_path: str, output_dir: str) -> bool:
        """
        Generate call tree JSON and text files from the call graph.
        
        This method is automatically called after AST generation to create
        call tree artifacts in the same directory as AST artifacts.
        
        Args:
            call_graph_path: Path to the merged_call_graph.json file
            output_dir: Directory where call tree files will be saved
            
        Returns:
            bool: True if call tree generation succeeded, False otherwise
        """
        self.logger.info("Generating call tree from call graph...")
        
        try:
            # Check if call graph file exists
            if not os.path.exists(call_graph_path):
                self.logger.warning(f"Call graph file not found: {call_graph_path}")
                return False
            
            # Create CallTreeGenerator with default settings
            # max_depth=20 for cycle breaking, sort_by_depth=True for longest branches first
            call_tree_generator = CallTreeGenerator(max_depth=20, sort_by_depth=True)
            
            # Load call graph from JSON
            call_tree_generator.load_from_json(call_graph_path)
            
            # Generate the call tree
            call_tree = call_tree_generator.generate_call_tree()
            
            if not call_tree:
                self.logger.warning("Call tree generation returned empty result")
                return False
            
            # Define output file paths in the same directory as AST artifacts
            call_tree_json_path = os.path.join(output_dir, CALL_TREE_JSON_FILE)
            call_tree_text_path = os.path.join(output_dir, CALL_TREE_TEXT_FILE)
            
            # Write call tree JSON (with pretty formatting)
            call_tree_generator.write_json(call_tree_json_path, pretty=True)
            self.logger.info(f"Call tree JSON written to: {call_tree_json_path}")
            
            # Write call tree text format (with location information)
            call_tree_generator.write_text(call_tree_text_path, show_location=True)
            self.logger.info(f"Call tree text written to: {call_tree_text_path}")
            
            # Log statistics
            metadata = call_tree.get('metadata', {})
            self.logger.info(f"Call tree statistics:")
            self.logger.info(f"  - Total functions: {metadata.get('total_functions', 0)}")
            self.logger.info(f"  - Root nodes: {metadata.get('total_root_nodes', 0)}")
            self.logger.info(f"  - DAG edges: {metadata.get('dag_edges_count', 0)}")
            self.logger.info(f"  - Max depth: {metadata.get('max_depth', 0)}")
            
            return True
            
        except Exception as e:
            self.logger.error(f"Error generating call tree: {e}")
            import traceback
            self.logger.debug(f"Call tree generation traceback: {traceback.format_exc()}")
            # Don't fail the entire AST generation if call tree fails
            return False

    def _clear_llm_analysis_cache(self, config: dict, output_base_dir: str = None) -> None:
        """Clear LLM analysis cache and output directory."""
        llm_analysis_out_dir = config['analysis_dir']

        # Remove the entire LLM analysis output directory
        if os.path.exists(llm_analysis_out_dir):
            self.logger.info(f"Clearing LLM analysis output directory: {llm_analysis_out_dir}")
            shutil.rmtree(llm_analysis_out_dir)
            self.logger.info("LLM analysis output directory cleared")

    def _write_directory_tree_with_issues(self, file_handle, node, indent_level):
        """Write directory tree with issues to file handle."""
        if not node:
            return

        # Write directory name
        indent = "  " * indent_level
        file_handle.write(f"{indent}📁 {node.name}/\n")

        # Write issues for this directory
        issues = node.get_issues()
        if issues:
            severity_counts = node.get_severity_counts()
            file_handle.write(f"{indent}  Issues: {len(issues)} ")
            severity_parts = []
            for severity, count in severity_counts.items():
                if count > 0:
                    severity_parts.append(f"{severity}:{count}")
            if severity_parts:
                file_handle.write(f"({', '.join(severity_parts)})")
            file_handle.write("\n")

            # Write individual issues
            for issue in issues:
                file_handle.write(f"{indent}    • {issue.get('file', 'Unknown')} - {issue.get('function_name', 'Unknown')} [{issue.get('severity', 'unknown')}]\n")
                file_handle.write(f"{indent}      {issue.get('issue', 'No description')[:100]}...\n")

        # Recursively write subdirectories
        for subdir in sorted(node.directories, key=lambda d: d.name):
            self._write_directory_tree_with_issues(file_handle, subdir, indent_level + 1)


    def _extract_original_context_from_analysis(self, analysis_result: str) -> str:
        """
        Extract original function context from analysis result for validation.
        This is a fallback method that tries to extract context from the analysis result itself.

        Args:
            analysis_result: The analysis result JSON string

        Returns:
            str: Original context or a placeholder if not found
        """
        try:
            # Try to parse the analysis result to extract function context
            result_data = orjson.loads(analysis_result)

            # Look for common fields that might contain the original function context
            context_fields = ['function', 'code', 'context', 'original_code', 'function_code']

            for field in context_fields:
                if field in result_data:
                    return orjson.dumps({field: result_data[field]}).decode('utf-8')

            # If no specific context field found, return a minimal context
            return orjson.dumps({"context": "Original function context not available"}).decode('utf-8')

        except (orjson.JSONDecodeError, Exception) as e:
            self.logger.warning(f"Could not extract original context from analysis result: {e}")
            return orjson.dumps({"context": "Original function context not available"}).decode('utf-8')

    def register_prior_result_store(self, store) -> None:
        """
        Register a prior result store for duplicate checking.
        This should be called before running analysis.

        Args:
            store: A store implementing ResultsCache interface
        """
        if not self.results_publisher:
            # Initialize publisher if not already done - determine publisher type based on class
            if hasattr(self, '_subscribers') and 'CodeAnalysis' in self.__class__.__name__:
                # Import here to avoid circular imports
                from results_store.code_analysis_publisher import CodeAnalysisResultsPublisher
                self.results_publisher = CodeAnalysisResultsPublisher()
            elif hasattr(self, '_subscribers') and 'TraceAnalysis' in self.__class__.__name__:
                # Import here to avoid circular imports
                from results_store.trace_analysis_publisher import TraceAnalysisResultsPublisher
                self.results_publisher = TraceAnalysisResultsPublisher()
            else:
                self.logger.warning("Cannot determine publisher type, cannot register prior result store")
                return

        self.results_publisher.register_prior_result_store(store)
        self.logger.info(f"Registered prior result store: {type(store).__name__}")

    def set_token_tracker(self, token_tracker) -> None:
        """
        Set the token tracker for this analysis runner.

        Args:
            token_tracker: TokenTracker instance to use for tracking token usage
        """
        self.token_tracker = token_tracker
        self.logger.info(f"Token tracker set: {type(token_tracker).__name__}")

    def get_token_tracker(self):
        """
        Get the current token tracker.

        Returns:
            TokenTracker instance or None if not set
        """
        return self.token_tracker

    def add_user_provided_prompt_to_analysys(self, user_prompt: str) -> None:
        """
        Add a user-provided prompt to be included in the system prompt for code analysis.
        This prompt will be appended to the system prompt when sending to the LLM.

        Args:
            user_prompt: A paragraph of text containing user-specific instructions for analysis
        """
        if user_prompt and user_prompt.strip():
            self.user_provided_prompts.append(user_prompt.strip())
            self.logger.info(f"Added user-provided prompt: {len(user_prompt.strip())} characters (total: {len(self.user_provided_prompts)} prompts)")
        else:
            self.logger.warning("Empty user prompt provided, skipping")
    
    def set_cancellation_checker(self, checker_func):
        """
        Set a function that returns True if analysis should continue.
        
        This enables cooperative cancellation where the analysis periodically
        checks if it should stop. The checker function is called at strategic
        points during analysis (e.g., every N function analyses).
        
        Args:
            checker_func: Callable that returns True if analysis should continue,
                         False if cancellation was requested
        """
        self._cancellation_checker = checker_func
        self.logger.info("Cancellation checker set for cooperative cancellation")
    
    def _should_continue(self) -> bool:
        """
        Check if analysis should continue (not cancelled).
        
        Returns:
            True if analysis should continue, False if cancellation was requested
        """
        if self._cancellation_checker:
            try:
                self.logger.debug("Checking for cancellation request...")
                should_continue = self._cancellation_checker()
                
                if should_continue:
                    self.logger.debug("Cancellation check: continuing analysis")
                else:
                    self.logger.info("Cancellation check: CANCELLATION DETECTED - stopping analysis")
                
                return should_continue
            except Exception as e:
                self.logger.warning(f"Error checking cancellation status: {e}")
                # On error, continue (fail open)
                return True
        # No checker set, always continue
        return True