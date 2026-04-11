#!/usr/bin/env python3
# Created by Sridhar Gurivireddy
"""
Main entry point for Trace Analysis
Handles trace files and provides LLM analysis results based on configuration
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
from .base_analyzer import BaseAnalyzer
from .directory_classifier import DirectoryClassifier
from .token_tracker import TokenTracker
from ..issue_filter import TraceRelevanceFilter, create_unified_filter
from ..core.constants import DEFAULT_MAX_TOKENS, DEFAULT_TEMPERATURE, PROCESSED_OUTPUT_DIR, DEFAULT_LLM_MODEL, DEFAULT_LLM_API_END_POINT
from ..core.lang_util.ast_call_graph_parser import ASTCallGraphParser
from ..core.llm.llm import Claude
from ..core.llm.tools import Tools
from ..core.trace_util.trace_analysis_prompt_builder import TraceAnalysisPromptBuilder
from ..core.trace_util.trace_code_analysis import TraceAnalysisConfig, TraceCodeAnalysis
from ..core.trace_util.trace_result_repository import TraceAnalysisResultRepository
from ..progress_util.analyzed_records_registry import AnalyzedRecordsRegistry
from ..report.enhanced_report_generator import calculate_stats, generate_html_report_with_callstacks
from ..report.issue_directory_organizer import DirectoryNode, RepositoryDirHierarchy
from ..utils.api_key_util import get_api_key
from ..utils.issue_organizer_util import organize_issues_complete
from ..utils.config_util import ConfigValidationError, load_config_tolerant, get_api_key_from_config, get_llm_provider_type
from ..utils.file_util import get_artifacts_temp_subdir_path, read_json_file
from ..core.errors import AnalyzerErrorCode, AnalysisResult
from ..utils.log_util import setup_default_logging
from ..utils.output_directory_provider import get_output_directory_provider

# Add the project root to Python path for imports
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

# Import publisher-subscriber classes
from results_store.trace_analysis_publisher import TraceAnalysisResultsPublisher
from results_store.trace_analysys_results_local_fs_subscriber import TraceAnalysysResultsLocalFSSubscriber
from results_store.file_system_results_cache import FileSystemResultsCache

# Constants
ANALYSIS_FILE_SUFFIX = "_analysis.json"

# Default output directory names
DEFAULT_TRACE_ANALYSIS_OUT_DIR = "trace_analysis"


class TraceAnalyzer(BaseAnalyzer):
    """Analyzer that performs LLM-based trace analysis."""

    def __init__(self):
        super().__init__()
        self.config = None
        self.api_key = None
        self.repo_path = None

    def name(self) -> str:
        return "TraceAnalyzer"

    def initialize(self, config: Mapping[str, Any]) -> None:
        """Setup and prepare for trace analysis."""
        super().initialize(config)
        self.config = dict(config)  # Convert to dict for compatibility
        self.api_key = config.get('api_key')
        self.repo_path = config.get('repo_path')

    def analyze_function(self, func_record: Mapping[str, Any]) -> Optional[Mapping[str, Any]]:
        """Analyze a single trace/prompt record using LLM."""
        if not self._initialized:
            raise RuntimeError("Analyzer not initialized. Call initialize() first.")

        try:
            # Create a temporary file for this analysis

            # For trace analysis, func_record should contain prompt content
            prompt_content = func_record.get('prompt_content', '')
            if not prompt_content:
                return None

            with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as temp_file:
                temp_file.write(prompt_content)
                temp_input_path = temp_file.name

            # Create output file path
            with tempfile.NamedTemporaryFile(mode='w', suffix='_analysis.json', delete=False) as temp_output:
                temp_output_path = temp_output.name

            try:
                # Create TraceAnalysisConfig
                analysis_config = TraceAnalysisConfig(
                    prompt_file_path=temp_input_path,
                    api_key=self.api_key,
                    api_url=self.config.get('api_end_point', DEFAULT_LLM_API_END_POINT),
                    model=self.config.get('model', DEFAULT_LLM_MODEL),
                    repo_path=self.repo_path,
                    output_file=temp_output_path,
                    max_tokens=DEFAULT_MAX_TOKENS,
                    temperature=DEFAULT_TEMPERATURE,
                    config=self.config
                )

                # Run analysis
                trace_analysis = TraceCodeAnalysis(analysis_config)
                success = trace_analysis.run_analysis()

                if success:
                    # Read the result
                    try:
                        with open(temp_output_path, 'r', encoding='utf-8') as f:
                            result = json.load(f)
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

        except Exception:
            # Log error but don't raise to maintain interface contract
            return None

    def finalize(self) -> None:
        """Cleanup after analysis."""
        pass



class TraceAnalysisRunner(UnifiedIssueFilterMixin, ReportGeneratorMixin, AnalysisRunner):
    """Main runner class for trace analysis with LLM.
    
    Uses UnifiedIssueFilterMixin for shared issue filter initialization.
    Uses ReportGeneratorMixin for shared report generation functionality.
    """

    def __init__(self):
        """Initialize the runner with logging setup."""
        super().__init__()

        # AnalyzedRecordsRegistry for tracking analyzed callstacks
        self.analyzed_records_registry = None

        # Initialize attributes that may be set later
        self.api_key = None
        self.config = None
        self.repo_path = None
        self.num_traces_to_analyze = None

        # Unified issue filter (initialized when needed)
        self.unified_issue_filter = None

        # Publisher-subscriber system for trace analysis results
        self._subscribers = []  # List to hold multiple subscribers

    def get_default_trace_analysis_paths(self, repo_path: str, override_base_dir: str = None, custom_base_dir: str = None) -> dict:
        """
        Get default output paths for trace analysis.

        Args:
            repo_path: Path to the repository
            override_base_dir: Optional override base directory (legacy parameter)
            custom_base_dir: Optional custom base directory from config (deprecated, uses singleton)

        Returns:
            Dictionary containing default paths for trace analysis
        """
        try:
            output_provider = get_output_directory_provider()
            artifacts_dir = output_provider.get_repo_artifacts_dir()
            trace_analysis_dir = os.path.join(artifacts_dir, "trace_analysis")
        except RuntimeError:
            # Fallback to parameter-based approach
            # Use custom_base_dir if provided, otherwise fall back to override_base_dir
            base_dir = custom_base_dir or override_base_dir

            # Get the trace analysis directory path
            if base_dir:
                # Use custom base directory structure
                repo_name = os.path.basename(repo_path.rstrip('/'))
                trace_analysis_dir = os.path.join(os.path.expanduser(base_dir), repo_name, "trace_analysis")
            else:
                # Use default structure
                trace_analysis_dir = get_artifacts_temp_subdir_path(repo_path, "trace_analysis", override_base_dir)

        return {
            'trace_analysis_dir': trace_analysis_dir,
            'prompts_dir': os.path.join(trace_analysis_dir, "trace_analysis_prompts"),
            'results_dir': os.path.join(trace_analysis_dir, "trace_analysis")
        }

    def _analyze_single_callstack(self, callstack_index: int, callstack: list, prompt_content: str, callstack_data: dict, trace_analysis_out_dir: str) -> bool:
        """
        Analyze a single callstack directly without using prompt files.

        Args:
            callstack_index: Index of the callstack
            callstack: Callstack data (list of callstack entries)
            prompt_content: Generated prompt content
            callstack_data: Structured callstack data
            trace_analysis_out_dir: Output directory for results

        Returns:
            bool: True if analysis successful
        """
        try:
            # Convert callstack to text format for registry tracking
            from ..core.trace_util.trace_analysis_prompt_builder import TraceAnalysisPromptBuilder
            temp_builder = TraceAnalysisPromptBuilder()
            callstack_text = temp_builder._convert_callstack_to_text_format(callstack)

            # Check if this callstack has already been analyzed
            if self.analyzed_records_registry and callstack_text and self.analyzed_records_registry.is_analyzed(callstack_text):
                trace_id = f"trace_{callstack_index+1:04d}"
                self.logger.info(f"Skipping {trace_id} as it has already been analyzed")
                return True  # Return True since it's already been processed

            # Generate trace ID
            trace_id = f"trace_{callstack_index+1:04d}"

            if self.results_publisher:
                # Convert callstack to list for lookup
                callstack_list = []
                if callstack_text:
                    callstack_list = [line.strip() for line in callstack_text.split('\n') if line.strip()]

                existing_trace = self.results_publisher.check_existing_trace(trace_id, callstack_list)
                if existing_trace:
                    self.logger.info(f"Skipping {trace_id} - trace already exists in prior result stores")
                    return True

            # Create temporary output file for TraceCodeAnalysis
            output_filename = f"{trace_id}_analysis.json"
            output_file = os.path.join(trace_analysis_out_dir, output_filename)

            # Create temporary prompt file for TraceCodeAnalysis (will be cleaned up)
            import tempfile
            with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as temp_file:
                temp_file.write(prompt_content)
                temp_prompt_file = temp_file.name

            try:
                # Create TraceAnalysisConfig for trace analysis
                analysis_config = TraceAnalysisConfig(
                    prompt_file_path=temp_prompt_file,
                    api_key=self.api_key,
                    api_url=self.config['api_end_point'],
                    model=self.config['model'],
                    repo_path=self.repo_path,
                    output_file=output_file,
                    max_tokens=DEFAULT_MAX_TOKENS,
                    temperature=DEFAULT_TEMPERATURE,
                    config=self.config
                )

                # Start function analysis tracking with callstack
                if hasattr(self, '_start_function_analysis_tracking'):
                    self._start_function_analysis_tracking(callstack_text or trace_id)

                # Create and run TraceCodeAnalysis
                trace_analysis = TraceCodeAnalysis(analysis_config)
                success = trace_analysis.run_analysis()

                # Use centralized token tracking
                if self.token_tracker:
                    # Get real token counts from the TraceCodeAnalysis instance
                    input_tokens, output_tokens = trace_analysis.get_token_totals()
                    self.token_tracker.add_token_usage(input_tokens, output_tokens)

                # Record function analysis result
                if hasattr(self, '_record_function_analysis_result'):
                    self._record_function_analysis_result(
                        functions_analyzed=1,
                        success=success,
                        function_data=callstack_text or trace_id
                    )

                if success:
                    # Use publisher-subscriber system to handle results
                    if self.results_publisher:
                        try:
                            # Read the analysis result from the output file
                            with open(output_file, 'r', encoding='utf-8') as f:
                                result_data = json.load(f)

                            # Extract repository name
                            repo_name = os.path.basename(self.repo_path.rstrip('/'))

                            # Convert callstack text to list if available
                            callstack_list = []
                            if callstack_text:
                                callstack_list = [line.strip() for line in callstack_text.split('\n') if line.strip()]

                            # Apply unified two-level issue filter to issues before publishing
                            result_issues = result_data.get('results', []) or result_data.get('issues', [])
                            if self.unified_issue_filter and result_issues:
                                self.logger.debug(f"Applying unified two-level issue filter to {len(result_issues)} issues for {trace_id}")
                                filtered_issues = self.unified_issue_filter.filter_issues(result_issues)
                                
                                if len(filtered_issues) != len(result_issues):
                                    dropped_count = len(result_issues) - len(filtered_issues)
                                    self.logger.info(f"Unified issue filter dropped {dropped_count} issues for {trace_id}")
                                    
                                    # Update result data with filtered issues
                                    if 'results' in result_data:
                                        result_data['results'] = filtered_issues
                                    elif 'issues' in result_data:
                                        result_data['issues'] = filtered_issues
                            elif not self.unified_issue_filter:
                                self.logger.debug("Unified issue filter not available - publishing all issues")

                            # Enhance result data with trace-specific information
                            enhanced_result = result_data.copy()
                            enhanced_result['trace_id'] = trace_id
                            enhanced_result['callstack'] = callstack_list
                            enhanced_result['repo_name'] = repo_name
                            if callstack_data:
                                enhanced_result['callstack_data'] = callstack_data

                            # Publish the result using the publisher
                            self.results_publisher.add_trace_result(
                                repo_name=repo_name,
                                trace_id=trace_id,
                                callstack=callstack_list,
                                result=enhanced_result
                            )

                            self.logger.info(f"Analysis result published for: {trace_id}")

                            # Remove the temporary output file since publisher-subscriber handles file writing
                            try:
                                os.remove(output_file)
                            except OSError:
                                pass  # Ignore if file removal fails

                        except Exception as e:
                            self.logger.error(f"Failed to publish result via publisher-subscriber: {e}")
                            return False

                    if not self.results_publisher:
                        self.logger.error("Publisher not available - cannot save analysis results")
                        return False

                    # Add the callstack to the registry after successful analysis
                    if self.analyzed_records_registry and callstack_text:
                        self.analyzed_records_registry.add_analyzed(callstack_text)

                    return True
                else:
                    return False

            finally:
                # Clean up temporary prompt file
                try:
                    os.unlink(temp_prompt_file)
                except OSError:
                    pass

        except Exception as e:
            self.logger.error(f"Error analyzing callstack {callstack_index}: {e}")
            return False

    def _extract_callstack_from_prompt(self, prompt_file: str) -> str:
        """
        Extract callstack from prompt file in text format (one line per function).

        Args:
            prompt_file: Path to prompt file

        Returns:
            str: Callstack in text format, one function per line, or None if error
        """
        try:
            with open(prompt_file, 'r', encoding='utf-8') as f:
                content = f.read()

            # Find the callstack section before "====Use this additional context if needed==="
            # Since we removed the "Analyze this callstack" header, the callstack starts at the beginning
            end_marker = "\n====Use this additional context if needed==="

            end_idx = content.find(end_marker)
            if end_idx == -1:
                return None

            # Extract the callstack section from the beginning to the marker
            callstack_section = content[:end_idx].strip()

            # Return the callstack as-is (already in one-line-per-function format)
            return callstack_section

        except Exception as e:
            self.logger.warning(f"Error extracting callstack from {prompt_file}: {e}")
            return None

    def _ensure_ast_files_exist(self, config: dict, ast_files_config: dict):
        """
        Ensure AST files exist by generating them if they're missing.
        Enhanced with directory analysis support similar to code_analyzer.

        Args:
            config: Configuration dictionary
            ast_files_config: AST files configuration with file paths
        """
        # Check if all required AST files exist
        required_files = [
            ast_files_config.get('merged_functions_file'),
            ast_files_config.get('merged_graph_file'),
            ast_files_config.get('merged_data_types_file')
        ]

        missing_files = []
        for file_path in required_files:
            if not file_path or not os.path.exists(file_path):
                missing_files.append(file_path)

        if missing_files:
            # Set up configuration for AST generation (similar to code_analyzer.py)
            # Use the output directory from the singleton instead of JSON config
            ast_paths = self.get_default_ast_output_paths()
            config['astCallGraphDir'] = ast_paths['code_insights_dir']

            # Ensure path_to_repo is in config (same key as code_analyzer)
            config['path_to_repo'] = self.repo_path

            # Add directory analysis support (reused from code_analyzer.py)
            self._enhance_config_with_directory_analysis(config)

            # Always generate AST files if any are missing
            self.logger.info("Generating AST call graphs...")

            # Generate AST call graph (from base class)
            nested_call_graph_path = self._generate_ast_call_graph(config)

            # Process the generated call graph (similar to code_analyzer.py)
            self._process_ast_call_graph(config, nested_call_graph_path)

            self.logger.info("AST files generated successfully!")
        else:
            self.logger.info("All required AST files exist")

    def _enhance_config_with_directory_analysis(self, config: dict):
        """
        Enhance configuration with intelligent directory analysis.
        Reused from code_analyzer.py for consistency.

        Args:
            config: Configuration dictionary to enhance
        """
        try:
            # Get static exclusions using DirectoryClassifier (same as code_analyzer)
            static_exclusions = DirectoryClassifier.get_recommended_exclude_directories_safe(
                self.repo_path,
                user_provided_include_list=config.get('include_directories'),
                user_provided_exclude_list=config.get('exclude_directories')
            )
            
            if static_exclusions:
                # Merge with existing exclude_directories
                existing_excludes = config.get('exclude_directories', [])
                if isinstance(existing_excludes, list):
                    # Combine and deduplicate
                    combined_excludes = list(set(existing_excludes + static_exclusions))
                    config['exclude_directories'] = combined_excludes
                    self.logger.info(f"Enhanced exclude_directories with {len(static_exclusions)} recommended exclusions")
                    self.logger.debug(f"Static exclusions added: {static_exclusions}")
                else:
                    config['exclude_directories'] = static_exclusions
                    self.logger.info(f"Set exclude_directories to {len(static_exclusions)} recommended exclusions")

            # Try LLM-based directory analysis if API key is available
            api_key = config.get('api_key') or self.api_key
            if api_key:
                try:
                    # Lazy import to avoid circular dependency
                    from .directory_classifier import LLMBasedDirectoryClassifier
                    from ..utils.config_util import get_api_key_from_config
                    
                    self.logger.info("Attempting LLM-based directory analysis...")
                    
                    # Create LLM-based classifier with same config as main analyzer
                    llm_classifier = LLMBasedDirectoryClassifier.from_config(config)
                    
                    # Get already excluded directories to avoid re-analyzing them
                    already_excluded = config.get('exclude_directories', [])
                    
                    # Perform LLM analysis
                    llm_exclusions = llm_classifier.analyze_directories(
                        self.repo_path,
                        already_excluded_directories=already_excluded
                    )
                    
                    if llm_exclusions:
                        # Merge LLM exclusions with existing ones
                        existing_excludes = config.get('exclude_directories', [])
                        combined_excludes = list(set(existing_excludes + llm_exclusions))
                        config['exclude_directories'] = combined_excludes
                        self.logger.info(f"LLM analysis added {len(llm_exclusions)} additional exclusions")
                        self.logger.debug(f"LLM exclusions: {llm_exclusions}")
                    else:
                        self.logger.info("LLM analysis completed - no additional exclusions recommended")
                        
                except Exception as e:
                    self.logger.warning(f"LLM-based directory analysis failed: {e}")
                    self.logger.info("Continuing with static directory analysis only")
            else:
                self.logger.info("No API key available - skipping LLM-based directory analysis")
                
        except Exception as e:
            self.logger.warning(f"Directory analysis failed: {e}")
            self.logger.info("Continuing without enhanced directory filtering")

    def _process_ast_call_graph(self, config: dict, nested_call_graph_path: str):
        """
        Process the generated AST call graph to create the required files.

        Args:
            config: Configuration dictionary
            nested_call_graph_path: Path to the nested call graph file
        """
        # Extract configuration values
        ast_call_graph_dir = config['astCallGraphDir']
        repo_path = config['path_to_repo']

        # Set up tracking and output paths
        tracking_file = os.path.join(ast_call_graph_dir, "processed_AST_cache.json")
        # Create analysis_input directory at the same level as code_insights, not inside it
        output_dir = os.path.join(os.path.dirname(ast_call_graph_dir), PROCESSED_OUTPUT_DIR)

        # Ensure output directory exists
        os.makedirs(output_dir, exist_ok=True)

        # Validate call graph file exists
        if not os.path.exists(nested_call_graph_path):
            self.logger.error(f"Call graph file not found: {nested_call_graph_path}")
            return

        # Load and validate call graph structure
        call_graph_data = read_json_file(nested_call_graph_path)
        if not call_graph_data or 'call_graph' not in call_graph_data:
            self.logger.error(f"Invalid call graph structure in: {nested_call_graph_path}")
            return

        # Count total functions for reporting
        total_functions = 0
        for file_entry in call_graph_data['call_graph']:
            functions = file_entry.get('functions', [])
            total_functions += len(functions)

        self.logger.info(f"Processing call graph from: {nested_call_graph_path}")
        self.logger.info(f"Call graph validated: {total_functions} functions available for processing")

        # For trace analysis, we don't need to pre-process individual function files
        # The call graph data will be used on-demand during prompt generation
        # This is similar to how code_analyzer.py handles it with on-demand processing

        self.logger.info("AST call graph processing completed successfully!")
        self.logger.info(f"Ready for on-demand processing of {total_functions} functions")


    def add_results_subscriber(self, subscriber) -> None:
        """
        Add a subscriber to receive trace analysis results.
        This should be called before running analysis.

        Args:
            subscriber: A subscriber implementing TraceAnalysisSubscriber interface
        """
        self._subscribers.append(subscriber)
        self.logger.info(f"Added subscriber: {type(subscriber).__name__}")


    def _initialize_publisher_subscriber(self) -> None:
        """
        Initialize the publisher-subscriber system for trace analysis results.
        This should be called before starting trace analysis.
        """
        try:
            # Initialize the publisher
            self.results_publisher = TraceAnalysisResultsPublisher()
            self.logger.info("Initialized TraceAnalysisResultsPublisher")

            # Subscribe all registered subscribers to the publisher
            for subscriber in self._subscribers:
                self.results_publisher.subscribe(subscriber)
                self.logger.info(f"Subscribed {type(subscriber).__name__} to publisher")

            # If we have a file system subscriber, load existing results for caching
            repo_name = os.path.basename(self.repo_path.rstrip('/'))
            for subscriber in self._subscribers:
                if hasattr(subscriber, 'load_existing_results'):
                    loaded_count = subscriber.load_existing_results(repo_name, self.results_publisher)
                    if loaded_count > 0:
                        self.logger.info(f"Loaded {loaded_count} existing trace analysis results for caching via {type(subscriber).__name__}")

        except Exception as e:
            self.logger.error(f"Failed to initialize publisher-subscriber system: {e}")
            raise RuntimeError(f"Publisher-subscriber system initialization failed: {e}")

    # _initialize_unified_issue_filter is now provided by UnifiedIssueFilterMixin

    def _run_trace_analysis(self, config: dict, hotspot_file: str, api_key: str = None) -> tuple:
        """
        Run trace analysis directly on callstack data without generating prompt files.

        Args:
            config: Configuration dictionary
            hotspot_file: Path to hotspot file containing callstack data
            api_key: API key for LLM calls

        Returns:
            tuple: (successful_analyses, failed_analyses)
        """
        self.logger.info("Starting trace analysis on callstack data...")

        # Initialize centralized token tracker if not already set
        if not self.token_tracker:
            llm_provider_type = get_llm_provider_type(config)
            self.token_tracker = TokenTracker(llm_provider_type)
            self.logger.info(f"Auto-initialized centralized token tracker for provider: {llm_provider_type}")

        if not api_key:
            self.logger.warning("No API key available from config or Apple Connect token")
            self.logger.info("Skipping trace analysis due to missing API key")
            return 0, 0

        # Store API key and config for use in analysis
        self.api_key = api_key
        self.config = config

        # Initialize unified issue filter (disable LLM filtering for trace analysis)
        self._initialize_unified_issue_filter(api_key, config, enable_llm_filtering=False)

        # Initialize publisher-subscriber system before analysis
        self._initialize_publisher_subscriber()

        # Create output directory under results/
        output_provider = get_output_directory_provider()
        results_dir = self.get_results_directory()
        trace_analysis_out_dir = f"{results_dir}/trace_analysis"
        os.makedirs(trace_analysis_out_dir, exist_ok=True)

        # Initialize tools for LLM to use
        ignore_dirs = set()
        if config.get('exclude_directories'):
            base_dirs_to_ignore = config.get('exclude_directories', [])
            for dir_pattern in base_dirs_to_ignore:
                # Support both directory names and relative paths
                ignore_dirs.add(dir_pattern)
                ignore_dirs.add(dir_pattern.upper())
                ignore_dirs.add(dir_pattern.lower())

        # Get FileContentProvider instance from AnalysisRunner
        file_content_provider = None
        try:
            file_content_provider = self.get_file_content_provider()
        except RuntimeError:
            self.logger.warning("FileContentProvider not available, continuing without it")

        # Get the artifacts directory path (code_insights subdirectory for file lookups)
        artifacts_dir = f"{output_provider.get_repo_artifacts_dir()}/code_insights"

        tools = Tools(self.repo_path, None, file_content_provider, artifacts_dir, self.directory_tree_util, ignore_dirs)

        # Create AST files configuration using base class path methods
        ast_files_config = self.get_complete_ast_files_config(
            repo_path=self.repo_path,
            output_base_dir=output_provider.get_custom_base_dir()
        )

        # Ensure AST files exist
        self._ensure_ast_files_exist(config, ast_files_config)

        # Create TraceAnalysisPromptBuilder instance for on-demand prompt generation
        prompt_builder = TraceAnalysisPromptBuilder(file_content_provider, ast_files_config)
        prompt_builder.repo_path = self.repo_path

        # Load and validate configuration
        if not prompt_builder.load_and_validate_configuration(self.config_file, hotspot_file, self.repo_path):
            self.logger.error("Failed to load configuration for prompt builder")
            return 0, 0

        # Setup output directory for prompt builder
        prompt_builder.setup_output_directory()

        # Set batch parameters for callstack processing
        prompt_builder.set_batch_parameters(self.num_traces_to_analyze, getattr(self, 'batch_index', 0))

        # Process hotspot data to get callstack groups
        prompt_builder.process_hotspot_data()

        # Process callstacks to get structured data
        results, files_not_found = prompt_builder.process_callstacks()
        if results is None:
            self.logger.error("Failed to process callstack data")
            return 0, 0

        self.logger.info(f"Loaded {len(results)} callstack groups for analysis")

        # Filter out already analyzed callstacks
        unanalyzed_callstacks = []
        analyzed_count = 0

        for i, callstack in enumerate(results):
            # Convert callstack to text format for registry check
            callstack_text = prompt_builder._convert_callstack_to_text_format(callstack)

            # Check if this callstack has already been analyzed
            if self.analyzed_records_registry and callstack_text and self.analyzed_records_registry.is_analyzed(callstack_text):
                analyzed_count += 1
                continue

            unanalyzed_callstacks.append((i, callstack))

        self.logger.info(f"Found {analyzed_count} already analyzed callstacks, {len(unanalyzed_callstacks)} unanalyzed callstacks")

        # Honor num_traces_to_analyze parameter
        if hasattr(self, 'num_traces_to_analyze') and self.num_traces_to_analyze > 0:
            callstacks_to_analyze = unanalyzed_callstacks[:self.num_traces_to_analyze]
            if len(unanalyzed_callstacks) > self.num_traces_to_analyze:
                self.logger.info(f"Will analyze {len(callstacks_to_analyze)} unanalyzed callstacks (limited by --num-traces-to-analyze parameter)")
            else:
                self.logger.info(f"Will analyze all {len(callstacks_to_analyze)} unanalyzed callstacks")
        else:
            callstacks_to_analyze = unanalyzed_callstacks
            self.logger.info(f"Will analyze all {len(callstacks_to_analyze)} unanalyzed callstacks")

        if not callstacks_to_analyze:
            self.logger.info("No unanalyzed callstacks found - all traces have already been processed")
            return 0, 0

        successful_analyses = 0
        failed_analyses = 0

        # Process each callstack directly (no prompt files)
        for i, (callstack_index, callstack) in enumerate(callstacks_to_analyze, 1):
            try:
                progress_msg = f"[{i}/{len(callstacks_to_analyze)}]"
                trace_id = f"trace_{callstack_index+1:04d}"
                self.logger.info(f"{progress_msg} Analyzing: {trace_id}")

                # Generate prompt content on the fly
                prompt_content, callstack_data = prompt_builder.create_context_for(
                    callstack=callstack,
                    prompt_filename=f"{trace_id}.txt"
                )

                # Analyze the callstack directly
                success = self._analyze_single_callstack(
                    callstack_index=callstack_index,
                    callstack=callstack,
                    prompt_content=prompt_content,
                    callstack_data=callstack_data,
                    trace_analysis_out_dir=trace_analysis_out_dir
                )

                if success:
                    successful_analyses += 1
                    self.logger.info(f"{progress_msg} ✓ Successfully analyzed: {trace_id}")
                else:
                    failed_analyses += 1
                    self.logger.info(f"{progress_msg} ✗ Failed to analyze: {trace_id}")

            except Exception as e:
                failed_analyses += 1
                self.logger.error(f"{progress_msg} ✗ Error analyzing callstack {callstack_index}: {e}")

        self.logger.info(f"Trace analysis completed. Success: {successful_analyses}, Failed: {failed_analyses}")

        # Log tool usage summary
        tools.log_tool_usage_summary()

        # Log centralized token usage summary
        if self.token_tracker and (successful_analyses > 0 or failed_analyses > 0):
            self.token_tracker.log_summary()

        return successful_analyses, failed_analyses


    def _generate_report(self, config: dict) -> tuple:
        """Generate HTML report from trace analysis results."""
        self.logger.info("Starting trace analysis report generation...")

        # Extract configuration values
        repo_path = self.repo_path
        # Use the output directory from the singleton instead of JSON config
        results_dir = self.get_results_directory()
        trace_analysis_out_dir = f"{results_dir}/trace_analysis"
        project_name = config.get('project_name', 'Trace Analysis')

        # Check if analysis output directory exists
        if not os.path.exists(trace_analysis_out_dir):
            self.logger.warning(f"Analysis output directory not found: {trace_analysis_out_dir}")
            return False, None

        try:
            # Use publisher to get all results instead of reading files directly
            if not self.results_publisher:
                self.logger.error("Publisher not available for report generation")
                return False, None

            repo_name = os.path.basename(self.repo_path.rstrip('/'))
            all_results = self.results_publisher.get_results(repo_name)

            if not all_results:
                self.logger.warning("No results found in publisher")
                return False, None

            # Convert results to issues format for report generation
            all_issues = []
            for result in all_results:
                if 'results' in result and isinstance(result['results'], list):
                    all_issues.extend(result['results'])
                elif 'issues' in result and isinstance(result['issues'], list):
                    all_issues.extend(result['issues'])
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


            # Use the utility function to organize issues by directory
            # Pass exclude directories from config to issue organizer
            exclude_directories = config.get('exclude_directories', [])
            assignment_stats, repo_hierarchy, issue_organizer, _ = organize_issues_complete(
                repo_path, all_issues, exclude_directories=exclude_directories
            )

            # Print organized issues tree to file
            # Use the output directory from the singleton instead of JSON config
            results_dir = self.get_results_directory()
            organized_issues_file = f"{results_dir}/trace_analysis/trace_analysis_organized_issues.txt"
            os.makedirs(os.path.dirname(organized_issues_file), exist_ok=True)

            with open(organized_issues_file, 'w', encoding='utf-8') as f:
                f.write("TRACE ANALYSIS - ORGANIZED ISSUES BY DIRECTORY\n")
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

            # Generate HTML report with project information and callstack overlay support (use trace analysis specific filename)
            if project_name and project_name != 'Trace Analysis':
                report_filename = f"trace_analysis_{project_name.replace(' ', '_')}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
            else:
                report_filename = f"trace_analysis_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"

            # Get the reports directory and create full path
            # Use the output directory from the singleton instead of JSON config
            reports_dir = self.get_reports_directory()
            report_file_path = os.path.join(reports_dir, report_filename)

            report_file = generate_html_report_with_callstacks(all_issues, output_file=report_file_path, project_name=f"{project_name} - Trace Analysis")

            # Calculate and log statistics
            stats = calculate_stats(all_issues)
            self.logger.info(f"Report generated successfully: {report_file}")
            self.logger.info(f"Report statistics:")
            self.logger.info(f"  Total Issues: {stats['total']}")
            self.logger.info(f"  Critical: {stats['critical']}, High: {stats['high']}, Medium: {stats['medium']}, Low: {stats['low']}")

            return True, report_file

        except Exception as e:
            self.logger.error(f"Error generating report: {e}")
            self.logger.error(f"Full traceback: {traceback.format_exc()}")
            return False, None


    def generate_report_from_existing_issues(self, config_file: str, repo_path: str, out_dir: str):
        """Generate report from existing trace analysis files without running analysis."""
        try:
            self.logger.info("Starting report generation from existing trace analysis issues...")

            # Load and validate configuration
            self.logger.info(f"Loading configuration from: {config_file}")
            config = load_config_tolerant(config_file)

            # Store repo_path for use in methods and set in config for consistency
            self.repo_path = repo_path
            config['path_to_repo'] = repo_path

            # Extract configuration values
            project_name = config.get('project_name', 'Trace Analysis')

            # Initialize OutputDirectoryProvider singleton early
            custom_base_dir = out_dir
            output_provider = get_output_directory_provider()
            output_provider.configure(repo_path, custom_base_dir)
            self.logger.info(f"Configured OutputDirectoryProvider with repo_path: {repo_path}, custom_base_dir: {custom_base_dir}")

            # Set default trace analysis output directory under results/
            # Use the output directory from the singleton instead of JSON config
            output_provider = get_output_directory_provider()
            custom_base_dir = output_provider.get_custom_base_dir()
            results_dir = self.get_results_directory()
            trace_analysis_out_dir = f"{results_dir}/trace_analysis"

            # Override output directories if --out-dir is specified
            if out_dir:
                out_dir = os.path.abspath(out_dir)
                self.logger.info(f"Using custom output directory: {out_dir}")
                # Still use results structure in custom output directory
                custom_base_dir = out_dir
                results_dir = self.get_results_directory()
                trace_analysis_out_dir = f"{results_dir}/trace_analysis"

            self.logger.info(f"Repository path: {self.repo_path}")
            self.logger.info(f"Trace analysis output directory: {trace_analysis_out_dir}")

            # Check if analysis output directory exists
            if not os.path.exists(trace_analysis_out_dir):
                self.logger.error(f"Trace analysis output directory not found: {trace_analysis_out_dir}")
                self.logger.info("Please run trace analysis first or check the configuration.")
                return False

            # Generate report directly using existing method
            self.logger.info("=== REPORT GENERATION FROM EXISTING TRACE ANALYSIS ISSUES ===")

            # Store repo_path for use in methods
            self.repo_path = repo_path
            config['project_name'] = project_name

            # Initialize publisher-subscriber system to load existing results
            self._initialize_publisher_subscriber()

            # Use the existing _generate_report method
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
            error_code = AnalyzerErrorCode.ERROR_ANALYSIS_INVALID_CONFIG
            self.logger.error(f"[{error_code.value}] Configuration validation failed: {e}")
            print(f"\n❌ Report generation failed with error code: {error_code.value}")
            print(f"Error: {e}")
            return False
        except Exception as e:
            error_code = AnalyzerErrorCode.ERROR_INTERNAL_UNKNOWN
            self.logger.error(f"[{error_code.value}] Unexpected error during report generation: {e}")
            print(f"\n❌ Report generation failed with error code: {error_code.value}")
            print(f"Error: {e}")
            traceback.print_exc()
            return False


    def run(self, config_file: str, repo_path: str, hotspot_file: str, out_dir: str, num_traces_to_analyze: int = 100, batch_index: int = 0):
        """Main entry point for the Trace Analysis tool."""
        try:
            # Store parameters for use in other methods
            self.num_traces_to_analyze = num_traces_to_analyze
            self.batch_index = batch_index

            # Start sleep prevention early to keep Mac awake during entire analysis
            self._start_sleep_prevention()

            # Load and validate configuration
            self.logger.info(f"Loading configuration from: {config_file}")
            config = load_config_tolerant(config_file)

            # Skip analytics session - using centralized TokenTracker instead
            # self._start_analytics_session(repo_path)

            # Store repo_path for use in methods and set in config for consistency with code_analyzer
            self.repo_path = repo_path
            config['path_to_repo'] = repo_path

            # Initialize OutputDirectoryProvider singleton early
            # Use out_dir parameter instead of reading from JSON config
            custom_base_dir = out_dir
            output_provider = get_output_directory_provider()
            output_provider.configure(repo_path, custom_base_dir)
            self.logger.info(f"Configured OutputDirectoryProvider with repo_path: {repo_path}, custom_base_dir: {custom_base_dir}")


            # Get API key early for summary generation
            api_key = get_api_key_from_config(config)

            # Step 0: Directory Structure Index (before any analysis)
            self.logger.info("\n\n=== DIRECTORY STRUCTURE INDEX ===")
            self._ensure_directory_structure_index(repo_path)

            # Step 0.5: Enhanced Directory Analysis (like code_analyzer)
            self.logger.info("\n\n=== ENHANCED DIRECTORY ANALYSIS ===")
            self._enhance_config_with_directory_analysis(config)

            # Create AnalyzedRecordsRegistry instance with project name based on repo directory
            dirname = os.path.basename(repo_path.rstrip('/'))
            project_name = f"trace_analysis_{dirname}"
            self.analyzed_records_registry = AnalyzedRecordsRegistry(project_name)
            self.logger.info(f"Initialized AnalyzedRecordsRegistry with project name: {project_name}")
            stats = self.analyzed_records_registry.get_stats()
            self.logger.info(f"Registry stats: {stats['total_analyzed']} previously analyzed records")

            # Store config file path for use in other methods
            self.config_file = config_file

            # Get API key with fallback to Apple Connect token
            api_key = get_api_key_from_config(config)

            # Setup prompt logging - use the base directory where outputs are stored
            # Use out_dir parameter instead of reading from JSON config
            custom_base_dir = out_dir
            Claude.setup_prompts_logging()
            
            # Clear older prompts at the beginning of analysis
            Claude.clear_older_prompts()

            # Get the actual directory used for logging from the singleton
            output_provider = get_output_directory_provider()
            actual_prompts_dir = f"{output_provider.get_repo_artifacts_dir()}/prompts_sent"
            self.logger.info(f"Prompt logging setup completed in: {actual_prompts_dir}")

            self.logger.info("Configuration loaded successfully")
            self.logger.info(f"Repository path: {self.repo_path}")
            self.logger.info(f"Hotspot file: {hotspot_file}")
            self.logger.info(f"Number of traces to analyze: {num_traces_to_analyze}")

            # Step 2: Check for existing prompts and generate if needed
            output_provider = get_output_directory_provider()
            prompts_directory = f"{output_provider.get_repo_artifacts_dir()}/trace_analysis/trace_analysis_prompts"

            # Check if existing prompts can be reused
            generate_prompts = True  # Default behavior: always generate fresh prompts
            reuse_existing_prompts = False  # Default: don't reuse existing prompts

            if os.path.exists(prompts_directory) and reuse_existing_prompts:
                prompt_files = [f for f in os.listdir(prompts_directory) if f.endswith('.txt')]
                if prompt_files:
                    self.logger.info(f"Found {len(prompt_files)} existing prompts in {prompts_directory}")
                    # Always honor --num-traces-to-analyze parameter, even when reusing existing prompts
                    if len(prompt_files) >= num_traces_to_analyze:
                        self.logger.info(f"Reusing existing prompts from earlier run (will use first {num_traces_to_analyze} prompts)")
                        generate_prompts = False
                    else:
                        self.logger.info(f"Existing prompts ({len(prompt_files)}) are fewer than requested ({num_traces_to_analyze}), generating new prompts")
                        generate_prompts = True
                else:
                    self.logger.info("Prompts directory exists but is empty, generating new prompts")
                    generate_prompts = True
            elif reuse_existing_prompts and not os.path.exists(prompts_directory):
                self.logger.info("--reuse-existing-prompts specified but no existing prompts directory found, generating new prompts")
                generate_prompts = True
            else:
                self.logger.info("Generating fresh prompts (default behavior)")
                generate_prompts = True

            # Create FileContentProvider instance for the repository
            repo_path_obj = Path(self.repo_path)
            # Use out_dir parameter instead of reading from JSON config
            custom_base_dir = out_dir
            self.create_file_content_provider(repo_path_obj)

            # Initialize TraceAnalysisResultRepository singleton with FileContentProvider
            TraceAnalysisResultRepository.get_instance(self.get_file_content_provider())
            self.logger.info("Initialized TraceAnalysisResultRepository singleton")

            # Step 2.5: Ensure AST files exist (like code_analyzer)
            self.logger.info("\n\n=== AST FILES PREPARATION ===")
            ast_files_config = self.get_complete_ast_files_config(
                repo_path=repo_path,
                output_base_dir=custom_base_dir
            )
            self._ensure_ast_files_exist(config, ast_files_config)

            # Step 3: Summary Generation (before trace analysis)
            self.logger.info("\n\n=== SUMMARY GENERATION ===")
            summary_success = self._run_summary_generation(config, api_key)
            if summary_success:
                self.logger.info("Summary generation completed successfully!")
            else:
                self.logger.warning("Summary generation encountered issues but continuing with analysis")
            # Step 4: Project Summarization (fault-tolerant)
            self.logger.info("\n\n=== PROJECT SUMMARIZATION ===")

            # Step 5: Run trace analysis directly on callstack data
            self.logger.info("\n\n=== TRACE ANALYSIS ===")
            analysis_results = self._run_trace_analysis(config, hotspot_file, api_key)
            if analysis_results:
                successful, failed = analysis_results
                self.logger.info(f"Trace analysis completed. Successful: {successful}, Failed: {failed}")

            # Step 6: Report Generation (if analysis results exist)
            self.logger.info("\n\n=== REPORT GENERATION ===")
            report_results = self._generate_report(config)
            if report_results:
                report_success, report_file = report_results
                if report_success:
                    self.logger.info(f"Report generation completed successfully!")
                    self.logger.info(f"HTML report saved to: {report_file}")
                else:
                    self.logger.warning("Report generation completed but no report was generated")
            else:
                self.logger.warning("Report generation failed")

            self.logger.info("Trace analysis pipeline completed successfully!")

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


def main():
    """Main entry point for trace analysis."""
    # Parse command line arguments
    parser = argparse.ArgumentParser(
        description="Trace Analysis Tool - Processes hotspot JSON files and analyzes callstacks with LLM",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--config", "-c",
        required=True,
        help="Path to configuration file (JSON format)"
    )
    parser.add_argument(
        "--repo", "-r",
        required=True,
        help="Path to repository directory"
    )
    parser.add_argument(
        "--hotspot", "-t",
        help="Path to hotspot JSON file (with 'callstack' root)"
    )
    parser.add_argument(
        "--num-traces-to-analyze", "-n",
        type=int,
        default=10,
        help="Number of traces to analyze (default: 10)"
    )
    parser.add_argument(
        "--batch-index",
        type=int,
        default=0,
        help="Batch index for selecting traces by normalized cost (0=top aggressors, 1=next batch, etc.). Uses random selection if not specified or negative."
    )
    parser.add_argument(
        "--out-dir", "-o",
        default=os.path.expanduser("~/llm_artifacts"),
        help="Output directory for analysis results and logs (default: ~/llm_artifacts)"
    )
    parser.add_argument(
        "--generate-report-from-existing-issues",
        action="store_true",
        help="Generate report from existing trace analysis files without running analysis. Requires --config to locate artifacts."
    )

    args = parser.parse_args()

    # Setup logging
    setup_default_logging()

    runner = TraceAnalysisRunner()

    # Load and validate configuration to determine LLM provider type
    try:
        config = load_config_tolerant(args.config)
    except Exception as e:
        print(f"Configuration loading failed: {e}")
        sys.exit(1)

    # Auto-create and set TokenTracker
    llm_provider_type = get_llm_provider_type(config)
    token_tracker = TokenTracker(llm_provider_type)
    runner.set_token_tracker(token_tracker)
    print(f"Auto-created TokenTracker for provider: {llm_provider_type}")

    # Add default file system subscriber when running as standalone script
    if args.out_dir:
        repo_name = os.path.basename(args.repo.rstrip('/'))
        default_subscriber = TraceAnalysysResultsLocalFSSubscriber(args.out_dir)
        default_subscriber.set_repo_name(repo_name)
        runner.add_results_subscriber(default_subscriber)

    # Check if user wants to generate report from existing issues only
    if args.generate_report_from_existing_issues:
        if not args.config:
            print("Error: --config is required when using --generate-report-from-existing-issues")
            sys.exit(1)

        success = runner.generate_report_from_existing_issues(
            config_file=args.config,
            repo_path=args.repo,
            out_dir=args.out_dir,
        )
        sys.exit(0 if success else 1)
    else:
        # Validate required arguments for full analysis
        if not args.hotspot:
            print("Error: --hotspot is required when not using --generate-report-from-existing-issues")
            sys.exit(1)

        # Run the full trace analysis pipeline
        runner.run(
            config_file=args.config,
            repo_path=args.repo,
            hotspot_file=args.hotspot,
            out_dir=args.out_dir,
            num_traces_to_analyze=args.num_traces_to_analyze,
            batch_index=args.batch_index,
        )

    # Print token usage summary after analysis
    if runner.get_token_tracker():
        input_tokens, output_tokens = runner.get_token_tracker().get_total_token_usage()
        total_tokens = input_tokens + output_tokens
        if total_tokens > 0:
            print(f"\n=== TOKEN USAGE SUMMARY ===")
            print(f"Input Tokens:  {input_tokens:,}")
            print(f"Output Tokens: {output_tokens:,}")
            print(f"Total Tokens:  {total_tokens:,}")
            print(f"Provider:      {runner.get_token_tracker().llm_provider_type}")
            print("=" * 27)


if __name__ == "__main__":
    main()