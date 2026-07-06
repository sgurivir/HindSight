#!/usr/bin/env python3
# Created by Sridhar Gurivireddy
"""
Main entry point for Trace Analysis
Handles trace files and provides LLM analysis results based on configuration
"""

import argparse
import asyncio
import json
import os
import sys
import tempfile
import threading
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

from .analysis_runner import AnalysisRunner
from .analysis_runner_mixins import UnifiedIssueFilterMixin, ReportGeneratorMixin
from .base_analyzer import BaseAnalyzer
from .directory_classifier import DirectoryClassifier
from .token_tracker import TokenTracker
from ..issue_filter import TraceRelevanceFilter, create_unified_filter
from ..core.constants import DEFAULT_MAX_TOKENS, PROCESSED_OUTPUT_DIR, DEFAULT_LLM_MODEL, DEFAULT_LLM_API_END_POINT, TRACE_ANALYZER_DEFAULT_WORKERS, LLM_PROVIDER_RATE_LIMIT, LLM_PROVIDER_RATE_WINDOW_SECONDS
from ..core.lang_util.ast_call_graph_parser import ASTCallGraphParser
from ..core.trace_util.trace_analysis_prompt_builder import TraceAnalysisPromptBuilder
from ..core.trace_util.trace_result_repository import TraceAnalysisResultRepository
from ..core.trace_util.file_name_extractor_from_trace import FileNameExtractorFromTrace
from ..orchestration import (
    AnalysisContext,
    AnalysisSession,
    TracePipeline,
    TraceRunSummary,
    TraceWork,
)
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

project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from results_store.trace_analysis_publisher import TraceAnalysisResultsPublisher
from results_store.trace_analysys_results_local_fs_subscriber import TraceAnalysysResultsLocalFSSubscriber
from results_store.file_system_results_cache import FileSystemResultsCache

ANALYSIS_FILE_SUFFIX = "_analysis.json"

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
        """Single-function entry point.

        Retained for `BaseAnalyzer` interface compatibility; trace analysis
        now flows through `TracePipeline` via `TraceAnalysisRunner._run_trace_analysis`,
        which operates on whole callstacks rather than individual functions.
        Direct callers should drive `TraceAnalysisRunner` instead.
        """
        raise NotImplementedError(
            "TraceAnalyzer.analyze_function is no longer supported; use "
            "TraceAnalysisRunner._run_trace_analysis (callstack-oriented pipeline)."
        )

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

        self.analyzed_records_registry = None
        self._registry_lock = threading.Lock()  # Protects analyzed_records_registry access
        self._token_tracker_lock = threading.Lock()  # Protects token_tracker access

        self.api_key = None
        self.config = None
        self.repo_path = None
        self.num_traces_to_analyze = None

        self.unified_issue_filter = None

        self._subscribers = []

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
            ast_paths = self.get_default_ast_output_paths()
            config['astCallGraphDir'] = ast_paths['code_insights_dir']

            config['path_to_repo'] = self.repo_path

            self._enhance_config_with_directory_analysis(config)

            self.logger.info("Generating AST call graphs...")

            nested_call_graph_path = self._generate_ast_call_graph(config)

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
        ast_call_graph_dir = config['astCallGraphDir']
        repo_path = config['path_to_repo']

        tracking_file = os.path.join(ast_call_graph_dir, "processed_AST_cache.json")
        # Create analysis_input directory at the same level as code_insights, not inside it
        output_dir = os.path.join(os.path.dirname(ast_call_graph_dir), PROCESSED_OUTPUT_DIR, "trace_analysis")

        os.makedirs(output_dir, exist_ok=True)

        if not os.path.exists(nested_call_graph_path):
            self.logger.error(f"Call graph file not found: {nested_call_graph_path}")
            return

        call_graph_data = read_json_file(nested_call_graph_path)
        if not call_graph_data or not isinstance(call_graph_data, list):
            self.logger.error(f"Invalid call graph structure in: {nested_call_graph_path}")
            return

        total_functions = 0
        for file_entry in call_graph_data:
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
            self.results_publisher = TraceAnalysisResultsPublisher()
            self.logger.info("Initialized TraceAnalysisResultsPublisher")

            for subscriber in self._subscribers:
                self.results_publisher.subscribe(subscriber)
                self.logger.info(f"Subscribed {type(subscriber).__name__} to publisher")

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
        """Drive trace analysis through the new async `TracePipeline`.

        Sets up the publisher / unified-filter / AST / prompt-builder state
        synchronously, materializes `TraceWork` items, then dispatches to an
        inner `asyncio.run`-driven coroutine that runs the LLM stages.

        Returns: ``(successful, failed)`` — cache hits count as successful so
        the caller's progress accounting stays consistent with legacy behavior.
        """
        self.logger.info("Starting trace analysis on callstack data...")

        if not self.token_tracker:
            llm_provider_type = get_llm_provider_type(config)
            self.token_tracker = TokenTracker(llm_provider_type)
            self.logger.info(f"Auto-initialized centralized token tracker for provider: {llm_provider_type}")

        if not api_key:
            self.logger.warning("No API key available from config or Apple Connect token")
            self.logger.info("Skipping trace analysis due to missing API key")
            return 0, 0

        self.api_key = api_key
        self.config = config

        # Initialize unified issue filter (disable LLM filtering for trace analysis)
        self._initialize_unified_issue_filter(api_key, config, enable_llm_filtering=False)

        self._initialize_publisher_subscriber()

        output_provider = get_output_directory_provider()
        results_dir = self.get_results_directory()
        trace_analysis_out_dir = f"{results_dir}/trace_analysis"
        os.makedirs(trace_analysis_out_dir, exist_ok=True)

        file_content_provider = None
        try:
            file_content_provider = self.get_file_content_provider()
        except RuntimeError:
            self.logger.warning("FileContentProvider not available, continuing without it")

        ast_files_config = self.get_complete_ast_files_config(
            repo_path=self.repo_path,
            output_base_dir=output_provider.get_custom_base_dir()
        )

        self._ensure_ast_files_exist(config, ast_files_config)

        prompt_builder = TraceAnalysisPromptBuilder(file_content_provider, ast_files_config)
        prompt_builder.repo_path = self.repo_path

        if not prompt_builder.load_and_validate_configuration(self.config_file, hotspot_file, self.repo_path):
            self.logger.error("Failed to load configuration for prompt builder")
            return 0, 0

        prompt_builder.setup_output_directory()
        prompt_builder.set_batch_parameters(self.num_traces_to_analyze, getattr(self, 'batch_index', 0))
        prompt_builder.process_hotspot_data()

        results, files_not_found = prompt_builder.process_callstacks()
        if results is None:
            self.logger.error("Failed to process callstack data")
            return 0, 0

        self.logger.info(f"Loaded {len(results)} callstack groups for analysis")

        # Filter out already-analyzed callstacks (registry pre-check).
        unanalyzed_callstacks: List[Tuple[int, list, str]] = []
        analyzed_count = 0
        for i, callstack in enumerate(results):
            callstack_text = prompt_builder._convert_callstack_to_text_format(callstack)
            if self.analyzed_records_registry and callstack_text and self.analyzed_records_registry.is_analyzed(callstack_text):
                analyzed_count += 1
                continue
            unanalyzed_callstacks.append((i, callstack, callstack_text or ""))

        self.logger.info(f"Found {analyzed_count} already analyzed callstacks, {len(unanalyzed_callstacks)} unanalyzed callstacks")

        # Honor num_traces_to_analyze.
        if getattr(self, 'num_traces_to_analyze', 0) and self.num_traces_to_analyze > 0:
            to_analyze = unanalyzed_callstacks[:self.num_traces_to_analyze]
            if len(unanalyzed_callstacks) > self.num_traces_to_analyze:
                self.logger.info(f"Will analyze {len(to_analyze)} unanalyzed callstacks (limited by --num-traces-to-analyze parameter)")
            else:
                self.logger.info(f"Will analyze all {len(to_analyze)} unanalyzed callstacks")
        else:
            to_analyze = unanalyzed_callstacks
            self.logger.info(f"Will analyze all {len(to_analyze)} unanalyzed callstacks")

        if not to_analyze:
            self.logger.info("No unanalyzed callstacks found - all traces have already been processed")
            return 0, 0

        # Pre-generate prompt content sequentially (prompt_builder is not thread-safe).
        work_items: List[TraceWork] = []
        file_name_extractor = None
        try:
            file_name_extractor = FileNameExtractorFromTrace(
                config=self.config, repo_path=self.repo_path
            )
        except Exception as e:  # noqa: BLE001
            self.logger.warning(f"FileNameExtractorFromTrace initialization failed: {e}")

        for callstack_index, callstack, callstack_text in to_analyze:
            trace_id = f"trace_{callstack_index+1:04d}"
            prompt_content, callstack_data = prompt_builder.create_context_for(
                callstack=callstack,
                prompt_filename=f"{trace_id}.txt"
            )
            extracted_paths: Tuple[str, ...] = ()
            if file_name_extractor is not None:
                try:
                    paths = file_name_extractor.get_all_file_paths(prompt_content) or []
                    extracted_paths = tuple(p for p in paths if p)
                except Exception as e:  # noqa: BLE001
                    self.logger.warning(f"File-path extraction failed for {trace_id}: {e}")
            work_items.append(
                TraceWork(
                    callstack_index=callstack_index,
                    callstack=tuple(callstack) if callstack else (),
                    prompt_content=prompt_content,
                    callstack_data=callstack_data,
                    extracted_file_paths=extracted_paths,
                    callstack_text=callstack_text,
                    trace_id=trace_id,
                )
            )

        self.logger.info(
            f"Generated prompts for {len(work_items)} callstacks; "
            f"starting async pipeline with {TRACE_ANALYZER_DEFAULT_WORKERS} workers"
        )

        # Drive the async pipeline.
        summary = asyncio.run(
            self._run_trace_pipeline_async(
                config=config,
                api_key=api_key,
                work_items=work_items,
            )
        )

        # Cache hits + successful analyses both count as "success" for legacy parity.
        successful = summary.successful + summary.cached
        failed = summary.failed
        self.logger.info(
            f"Trace analysis completed. Success: {successful} "
            f"(new={summary.successful}, cached={summary.cached}), Failed: {failed}"
        )

        if self.token_tracker and (successful > 0 or failed > 0):
            self.token_tracker.log_summary()

        return successful, failed

    async def _run_trace_pipeline_async(
        self,
        *,
        config: dict,
        api_key: str,
        work_items: List["TraceWork"],
    ) -> "TraceRunSummary":
        """Build an `AnalysisSession` + `TracePipeline` and analyze every trace.

        Cache check and publish are routed back to the existing sync
        publisher/registry through `to_thread`-wrapped callbacks; this
        preserves the legacy publish-and-cache semantics without dragging
        thread locks into the pipeline itself.
        """
        output_provider = get_output_directory_provider()
        output_base = (
            output_provider.get_custom_base_dir()
            if output_provider.is_configured()
            else os.path.dirname(output_provider.get_repo_artifacts_dir())
        )

        analysis_ctx = AnalysisContext.from_config(
            repo_path=self.repo_path,
            config={
                **config,
                "code_analyzer_workers": config.get(
                    "trace_analyzer_workers", TRACE_ANALYZER_DEFAULT_WORKERS
                ),
            },
            output_base_dir=output_base,
            api_key=api_key,
        )

        def _token_relay(input_tokens: int, output_tokens: int) -> None:
            if self.token_tracker is None:
                return
            with self._token_tracker_lock:
                self.token_tracker.add_token_usage(input_tokens, output_tokens)

        async def _cache_check(work: TraceWork) -> bool:
            return await asyncio.to_thread(self._cache_check_sync, work)

        async def _publish(work: TraceWork, issues: List[Dict[str, Any]]) -> bool:
            return await asyncio.to_thread(self._publish_trace_result_sync, work, issues)

        async with AnalysisSession.create(
            analysis_ctx,
            file_content_provider=self.get_file_content_provider()
                if hasattr(self, "_file_content_provider") or hasattr(self, "get_file_content_provider")
                else None,
            directory_tree_util=getattr(self, "directory_tree_util", None),
            analyzer_name="trace_analysis",
            knowledge_subject="trace",
        ) as session:
            pipeline = TracePipeline(
                session,
                publish_callback=_publish,
                cache_check_callback=_cache_check,
                token_callback=_token_relay,
            )
            return await pipeline.analyze_traces(work_items)

    def _cache_check_sync(self, work: "TraceWork") -> bool:
        """Sync cache lookup invoked from `_cache_check` in a worker thread.

        Mirrors the legacy two-step check: AnalyzedRecordsRegistry first,
        then the publisher's `check_existing_trace`. Either match means
        "already analyzed — skip the LLM call".
        """
        try:
            if (
                self.analyzed_records_registry
                and work.callstack_text
                and self.analyzed_records_registry.is_analyzed(work.callstack_text)
            ):
                self.logger.info(f"Skipping {work.trace_id} as it has already been analyzed")
                return True

            if self.results_publisher:
                callstack_list = (
                    [line.strip() for line in work.callstack_text.split("\n") if line.strip()]
                    if work.callstack_text else []
                )
                if self.results_publisher.check_existing_trace(work.trace_id, callstack_list):
                    self.logger.info(
                        f"Skipping {work.trace_id} - trace already exists in prior result stores"
                    )
                    return True
        except Exception as e:  # noqa: BLE001
            self.logger.warning(f"Cache check failed for {work.trace_id}: {e}")
            return False

        return False

    def _publish_trace_result_sync(
        self,
        work: "TraceWork",
        issues: List[Dict[str, Any]],
    ) -> bool:
        """Apply the unified filter, publish, and update the registry — all sync."""
        if not self.results_publisher:
            self.logger.error("Publisher not available - cannot save analysis results")
            return False

        try:
            callstack_list = (
                [line.strip() for line in work.callstack_text.split("\n") if line.strip()]
                if work.callstack_text else []
            )
            repo_name = os.path.basename(self.repo_path.rstrip("/"))

            filtered_issues = list(issues)
            if self.unified_issue_filter and filtered_issues:
                self.logger.debug(
                    f"Applying unified issue filter to {len(filtered_issues)} issues for {work.trace_id}"
                )
                filtered_issues = self.unified_issue_filter.filter_issues(filtered_issues)
                dropped = len(issues) - len(filtered_issues)
                if dropped:
                    self.logger.info(
                        f"Unified issue filter dropped {dropped} issues for {work.trace_id}"
                    )

            enhanced_result: Dict[str, Any] = {
                "results": filtered_issues,
                "trace_id": work.trace_id,
                "callstack": callstack_list,
                "repo_name": repo_name,
            }
            if work.callstack_data:
                enhanced_result["callstack_data"] = work.callstack_data

            self.results_publisher.add_trace_result(
                repo_name=repo_name,
                trace_id=work.trace_id,
                callstack=callstack_list,
                result=enhanced_result,
            )
            self.logger.info(f"Analysis result published for: {work.trace_id}")

            if self.analyzed_records_registry and work.callstack_text:
                with self._registry_lock:
                    self.analyzed_records_registry.add_analyzed(work.callstack_text)

            return True
        except Exception as e:  # noqa: BLE001
            self.logger.error(f"Failed to publish trace result for {work.trace_id}: {e}")
            return False


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
            # Attach callstack_data from the parent result to each issue so the
            # HTML report can include the original trace in the copy button output.
            # Also set a Callstack text field so issues from the same trace share
            # one callstack overlay entry in the report.
            all_issues = []
            for result in all_results:
                callstack_data = result.get('callstack_data')
                callstack_list = result.get('callstack', [])
                callstack_text = '\n'.join(callstack_list) if callstack_list else ''
                issues_list = None
                if 'results' in result and isinstance(result['results'], list):
                    issues_list = result['results']
                elif 'issues' in result and isinstance(result['issues'], list):
                    issues_list = result['issues']

                if issues_list is not None:
                    for issue in issues_list:
                        if callstack_data and 'original_callstack' not in issue:
                            issue['original_callstack'] = callstack_data
                        if callstack_text and 'Callstack' not in issue:
                            issue['Callstack'] = callstack_text
                        all_issues.append(issue)
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
                        analyzer_type="trace_analysis",
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


    def _run_radar_deduplication(self, all_issues: list, keyword: str) -> list:
        """
        Run radar issue deduplication: download radars by keyword, ingest, and match.

        Annotates each issue dict with a 'radar_matches' key containing match data.
        Returns the same list of issues (possibly annotated). If anything fails,
        raises an exception that the caller should catch.
        """
        import hashlib
        from pathlib import Path
        from hindsight.utils.output_directory_provider import get_output_directory_provider

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
            client_name='TraceAnalyzerRadarDedupe'
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
            issue_id = f"trace_{idx}_{hashlib.md5('|'.join(key_parts).encode()).hexdigest()[:8]}"

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


    def generate_report_from_existing_issues(self, config_file: str, repo_path: str, out_dir: str, issue_dedupe_keyword: str = None):
        """Generate report from existing trace analysis files without running analysis."""
        try:
            self.logger.info("Starting report generation from existing trace analysis issues...")

            # Load and validate configuration
            self.logger.info(f"Loading configuration from: {config_file}")
            config = load_config_tolerant(config_file)
            config['issue_dedupe_keyword'] = issue_dedupe_keyword

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


    def run(self, config_file: str, repo_path: str, hotspot_file: str, out_dir: str, num_traces_to_analyze: int = 100, batch_index: int = 0, issue_dedupe_keyword: str = None):
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
            config['issue_dedupe_keyword'] = issue_dedupe_keyword

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
            registry_dir = os.path.join(output_provider.get_repo_artifacts_dir(), "trace_analysis", "analyzed_records")
            self.analyzed_records_registry = AnalyzedRecordsRegistry(project_name, stored_results_directory=registry_dir)
            self.logger.info(f"Initialized AnalyzedRecordsRegistry with project name: {project_name}")
            stats = self.analyzed_records_registry.get_stats()
            self.logger.info(f"Registry stats: {stats['total_analyzed']} previously analyzed records")

            # Store config file path for use in other methods
            self.config_file = config_file

            # Get API key with fallback to Apple Connect token
            api_key = get_api_key_from_config(config)

            # Prompt logging is now owned by `ConversationLogger` (per-session,
            # instance-scoped). It writes to `{artifacts}/prompts_sent/trace_analysis/`
            # via the `analyzer_name="trace_analysis"` passed to `AnalysisSession.create`.
            output_provider = get_output_directory_provider()
            actual_prompts_dir = f"{output_provider.get_repo_artifacts_dir()}/prompts_sent/trace_analysis"
            self.logger.info(f"Prompt logging will land in: {actual_prompts_dir}")

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
    parser.add_argument(
        "--issue-dedupe",
        type=str,
        default=None,
        metavar="KEYWORD",
        help="Run radar deduplication: download radars matching KEYWORD, then highlight matching issues in the HTML report"
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
            issue_dedupe_keyword=args.issue_dedupe,
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
            issue_dedupe_keyword=args.issue_dedupe,
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