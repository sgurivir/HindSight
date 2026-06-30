#!/usr/bin/env python3
# Created by Sridhar Gurivireddy
"""
Data Flow Analyzer - Generates call trees from AST call graphs.

This analyzer:
1. Uses LLM-based directory classification to identify directories to ignore
2. Generates AST call graphs (similar to code_analyzer)
3. Generates call trees from the call graphs

Usage:
    python -m hindsight.analyzers.data_flow_analyzer --config config.json --repo /path/to/repo

Example:
    python -m hindsight.analyzers.data_flow_analyzer \\
        --config ~/configs/my_project.json \\
        --repo ~/projects/my_project \\
        --out-dir ~/llm_artifacts
"""

import argparse
import asyncio
import json
import os
import sys
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from .analysis_runner import AnalysisRunner
from .analysis_runner_mixins import UnifiedIssueFilterMixin, ReportGeneratorMixin
from .base_analyzer import BaseAnalyzer
from .directory_classifier import DirectoryClassifier

from ..core.constants import (
    NESTED_CALL_GRAPH_FILE,
    MERGED_SYMBOLS_FILE,
    MERGED_DEFINED_CLASSES_FILE,
    EXTERNAL_INPUT_RATE_LIMIT,
    EXTERNAL_INPUT_DEFAULT_WORKERS,
    SINK_ANALYSIS_RATE_LIMIT,
    SINK_ANALYSIS_DEFAULT_WORKERS,
    PATH_DISCOVERY_MAX_DEPTH,
    PATH_DISCOVERY_MAX_PATHS_PER_PAIR,
    PATH_DISCOVERY_MAX_TOTAL_PATHS,
    FLOW_VULN_RATE_LIMIT,
    FLOW_VULN_DEFAULT_WORKERS,
    FLOW_VULN_MAX_FLOWS_TO_ANALYZE,
)
from ..core.lang_util.call_graph_util import CallGraph, load_call_graph_from_json, print_statistics
from ..core.lang_util.call_tree_util import CallTreeGenerator
from ..core.lang_util.code_navigation import CodeNavigationServer
from ..orchestration.pipeline_security import open_security_llm
from ..utils.config_util import (
    ConfigValidationError,
    load_and_validate_config,
    get_api_key_from_config,
    get_llm_provider_type
)
from ..utils.filtered_file_finder import FilteredFileFinder
from ..utils.log_util import setup_default_logging, get_logger
from ..utils.output_directory_provider import get_output_directory_provider

# Add the project root to Python path for imports
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

# Initialize logging at module level
setup_default_logging()
logger = get_logger(__name__)


class DataFlowAnalyzer(BaseAnalyzer):
    """Analyzer that generates call trees from AST call graphs."""

    def __init__(self):
        super().__init__()
        self.call_tree_generator: Optional[CallTreeGenerator] = None
        self.max_depth: int = 20
        self.sort_by_depth: bool = True

    def name(self) -> str:
        return "DataFlowAnalyzer"

    def initialize(self, config: Mapping[str, Any]) -> None:
        """Setup and prepare for analysis."""
        super().initialize(config)
        self.max_depth = config.get('max_call_depth', 20)
        self.sort_by_depth = config.get('sort_by_depth', True)
        self.call_tree_generator = CallTreeGenerator(
            max_depth=self.max_depth,
            sort_by_depth=self.sort_by_depth
        )

    def analyze_function(self, func_record: Mapping[str, Any]) -> Optional[Mapping[str, Any]]:
        """
        Not used for data flow analysis - this analyzer works on the entire call graph.
        
        The data flow analyzer generates call trees from the complete call graph,
        rather than analyzing individual functions.
        """
        return None

    def finalize(self) -> None:
        """Cleanup after analysis."""
        pass

    def pull_results_from_directory(self, artifacts_dir: str) -> Dict[str, Any]:
        """
        Pull data flow analysis results from the provided artifacts directory.

        Args:
            artifacts_dir: Path to the artifacts directory containing analysis results

        Returns:
            Dictionary containing:
            - 'results': Call tree data
            - 'statistics': Graph statistics
            - 'summary': Summary information
        """
        # For data flow analyzer, results are stored in data_flow_analysis/ subdirectory
        data_flow_dir = os.path.join(artifacts_dir, "data_flow_analysis")
        
        results = {
            'call_tree': None,
            'statistics': {},
            'summary': {
                'analyzer': self.name(),
                'analysis_directory': data_flow_dir
            }
        }
        
        # Load call tree JSON if it exists
        call_tree_path = os.path.join(data_flow_dir, "call_tree.json")
        if os.path.exists(call_tree_path):
            try:
                with open(call_tree_path, 'r') as f:
                    results['call_tree'] = json.load(f)
            except (json.JSONDecodeError, IOError) as e:
                logger.warning(f"Failed to load call tree: {e}")
        
        # Load statistics if they exist
        stats_path = os.path.join(data_flow_dir, "call_graph_statistics.json")
        if os.path.exists(stats_path):
            try:
                with open(stats_path, 'r') as f:
                    results['statistics'] = json.load(f)
            except (json.JSONDecodeError, IOError) as e:
                logger.warning(f"Failed to load statistics: {e}")
        
        return results


class DataFlowAnalysisRunner(UnifiedIssueFilterMixin, ReportGeneratorMixin, AnalysisRunner):
    """Main runner class for data flow analysis.
    
    Uses UnifiedIssueFilterMixin for shared issue filter initialization.
    Uses ReportGeneratorMixin for shared report generation functionality.
    """

    def __init__(self):
        """Initialize the runner with logging setup."""
        super().__init__()
        self.call_tree_generator: Optional[CallTreeGenerator] = None

    def get_default_data_flow_paths(self, repo_path: str, output_base_dir: str = None) -> dict:
        """
        Get default output paths for data flow analysis.

        Args:
            repo_path: Path to the repository
            output_base_dir: Optional output base directory

        Returns:
            Dictionary containing default paths for data flow analysis
        """
        try:
            output_provider = get_output_directory_provider()
            artifacts_dir = output_provider.get_repo_artifacts_dir()
            data_flow_dir = os.path.join(artifacts_dir, "data_flow_analysis")
        except RuntimeError:
            # Fallback to parameter-based approach
            if output_base_dir:
                repo_name = os.path.basename(repo_path.rstrip('/'))
                data_flow_dir = os.path.join(
                    os.path.expanduser(output_base_dir),
                    repo_name,
                    "data_flow_analysis"
                )
            else:
                from ..utils.file_util import get_artifacts_temp_subdir_path
                data_flow_dir = get_artifacts_temp_subdir_path(
                    repo_path, "data_flow_analysis", output_base_dir
                )

        return {
            'data_flow_dir': data_flow_dir,
            'call_tree_json': os.path.join(data_flow_dir, "call_tree.json"),
            'call_tree_text': os.path.join(data_flow_dir, "call_tree.txt"),
            'statistics_file': os.path.join(data_flow_dir, "call_graph_statistics.json")
        }

    def _run_directory_classification(self, config: dict) -> None:
        """
        Run DirectoryClassifier to get enhanced exclusions.
        
        This method:
        1. Runs DirectoryClassifier (static + LLM-based) to get enhanced exclude directories
        2. Updates config with the enhanced exclusions
        
        Args:
            config: Configuration dictionary
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
            
            # Update config with enhanced exclusions
            config['exclude_directories'] = enhanced_exclude_dirs
            self.logger.info("Updated config with enhanced exclude directories")
            
        except Exception as e:
            self.logger.warning(f"DirectoryClassifier failed, using user-provided exclusions: {e}")

    def _generate_call_tree(self, config: dict) -> Dict[str, Any]:
        """
        Generate call tree from the AST call graph.
        
        Args:
            config: Configuration dictionary
            
        Returns:
            Dictionary containing call tree and metadata
        """
        self.logger.info("Generating call tree from AST call graph...")
        
        # Get paths
        ast_call_graph_dir = config['astCallGraphDir']
        nested_call_graph_path = os.path.join(ast_call_graph_dir, NESTED_CALL_GRAPH_FILE)
        
        # Check if call graph exists
        if not os.path.exists(nested_call_graph_path):
            self.logger.error(f"Call graph file not found: {nested_call_graph_path}")
            return {}
        
        # Initialize call tree generator with sorting option
        max_depth = config.get('max_call_depth', 20)
        sort_by_depth = config.get('sort_by_depth', True)
        self.call_tree_generator = CallTreeGenerator(
            max_depth=max_depth,
            sort_by_depth=sort_by_depth
        )
        
        # Load and generate
        self.call_tree_generator.load_from_json(nested_call_graph_path)
        call_tree = self.call_tree_generator.generate_call_tree()
        
        # Get output paths
        data_flow_paths = self.get_default_data_flow_paths(
            config['path_to_repo'],
            config.get('output_base_dir')
        )
        
        # Create output directory
        os.makedirs(data_flow_paths['data_flow_dir'], exist_ok=True)
        
        # Write outputs
        self.call_tree_generator.write_json(
            data_flow_paths['call_tree_json'],
            pretty=True
        )
        self.logger.info(f"Call tree JSON written to: {data_flow_paths['call_tree_json']}")
        
        show_location = config.get('show_location', True)
        self.call_tree_generator.write_text(
            data_flow_paths['call_tree_text'],
            show_location=show_location
        )
        self.logger.info(f"Call tree text written to: {data_flow_paths['call_tree_text']}")
        
        # Write statistics
        stats = self.call_tree_generator.get_statistics()
        with open(data_flow_paths['statistics_file'], 'w') as f:
            json.dump(stats, f, indent=2)
        self.logger.info(f"Statistics written to: {data_flow_paths['statistics_file']}")
        
        # Print statistics to console
        self.logger.info("\n=== CALL GRAPH STATISTICS ===")
        self.logger.info(f"  Number of Nodes: {stats['num_nodes']}")
        self.logger.info(f"  Number of Edges: {stats['num_edges']}")
        self.logger.info(f"  Graph Depth: {stats['graph_depth']}")
        self.logger.info(f"  Leaf Nodes: {stats['num_leaf_nodes']}")
        self.logger.info(f"  Root Nodes: {stats['num_root_nodes']}")
        self.logger.info(f"  Total Paths (DAG): {stats['total_paths']}")
        self.logger.info(f"  Mean Edges/Node: {stats['mean_edges_per_node']:.2f}")
        
        return call_tree

    def _run_external_input_analysis(
        self,
        config: dict,
        call_tree: Dict[str, Any],
        max_workers: int
    ) -> Dict[str, Any]:
        """
        Step 4: Use LLM with MCP code navigation tools to determine which
        functions accept external (untrusted) input.

        Runs async workers in parallel, rate-limited to EXTERNAL_INPUT_RATE_LIMIT req/min.

        Args:
            config: Configuration dictionary (must have path_to_repo, astCallGraphDir)
            call_tree: The call tree dict from Step 3
            max_workers: Number of parallel async workers

        Returns:
            Annotated call tree dict with ext_input fields
        """
        from .external_input_analyzer import ExternalInputAnalyzer
        from ..core.constants import (
            DEFAULT_LLM_API_END_POINT, DEFAULT_LLM_MODEL,
            DATA_FLOW_ANALYZER_MODEL, DATA_FLOW_ANALYZER_MAX_TOKENS,
            ModelLimits,
        )

        self.logger.info("Starting external input analysis with MCP code navigation...")

        # Load the raw call graph data for the MCP server
        ast_call_graph_dir = config['astCallGraphDir']
        nested_call_graph_path = os.path.join(ast_call_graph_dir, NESTED_CALL_GRAPH_FILE)

        with open(nested_call_graph_path, 'r') as f:
            call_graph_data = json.load(f)

        # Initialize MCP code navigation server
        mcp_server = CodeNavigationServer(
            repo_path=config['path_to_repo'],
            call_graph_data=call_graph_data
        )
        self.logger.info(f"MCP server initialized with {mcp_server.graph.get_num_nodes()} symbols")

        # LLM client config — the actual `AsyncLLMClient` is opened lazily inside
        # the inner async function so the httpx pool gets `aclose`d cleanly when
        # the asyncio.run scope exits.
        api_key = get_api_key_from_config(config)
        model_name = config.get('model', DATA_FLOW_ANALYZER_MODEL)
        max_tokens = config.get('max_tokens', DATA_FLOW_ANALYZER_MAX_TOKENS)
        context_window = ModelLimits.get_context_window(model_name)

        # Collect all function names from the call tree, respecting directory/file filters
        all_functions: List[str] = []

        def collect_functions(node: Dict[str, Any]) -> None:
            func_name = node.get("function", "")
            if func_name and func_name != "ROOT":
                if self._should_analyze_function(node, config):
                    all_functions.append(func_name)
            for child in node.get("children", []):
                collect_functions(child)

        collect_functions(call_tree.get("call_tree", {}))
        # Deduplicate while preserving order
        seen: set = set()
        unique_functions = []
        for f in all_functions:
            if f not in seen:
                seen.add(f)
                unique_functions.append(f)

        self.logger.info(f"Functions after directory/file filtering: {len(unique_functions)}")

        # Load cached results from previous runs
        data_flow_paths = self.get_default_data_flow_paths(
            config['path_to_repo'], config.get('output_base_dir')
        )
        cache_path = os.path.join(data_flow_paths['data_flow_dir'], "ext_input_cache.json")
        cached_results: Dict[str, tuple] = {}
        if os.path.exists(cache_path):
            try:
                with open(cache_path, 'r') as f:
                    cache_data = json.load(f)
                for func_name, entry in cache_data.items():
                    cached_results[func_name] = (entry["ext_input"], entry["reason"])
                self.logger.info(f"Loaded {len(cached_results)} cached results from {cache_path}")
            except (json.JSONDecodeError, KeyError, TypeError) as e:
                self.logger.warning(f"Failed to load cache file, starting fresh: {e}")
                cached_results = {}

        # Filter out functions that already have successful cached results
        failed_reasons = {"LLM request failed", "Empty LLM response", "No answer produced",
                          "Max iterations exhausted without valid answer"}
        functions_to_analyze = [
            f for f in unique_functions
            if f not in cached_results
            or cached_results[f][1] in failed_reasons
            or cached_results[f][1].startswith("Error:")
        ]

        self.logger.info(f"Analyzing {len(functions_to_analyze)} functions for external input "
                         f"({len(unique_functions) - len(functions_to_analyze)} cached, "
                         f"workers={max_workers}, rate_limit={EXTERNAL_INPUT_RATE_LIMIT}/min)")

        # Callback to persist each result incrementally
        import threading
        cache_lock = threading.Lock()

        def _persist_result(func_name: str, ext_input: bool, reason: str) -> None:
            with cache_lock:
                cached_results[func_name] = (ext_input, reason)
                cache_data = {
                    fn: {"ext_input": ei, "reason": r}
                    for fn, (ei, r) in cached_results.items()
                }
                os.makedirs(os.path.dirname(cache_path), exist_ok=True)
                with open(cache_path, 'w') as f:
                    json.dump(cache_data, f, indent=2)

        async def _run_ext_input_pool():
            """Open the async LLM client once, run the (analyze → retry) pool,
            return the analyzer + merged results. The httpx pool is closed when
            this context exits."""
            async with open_security_llm(
                api_key=api_key,
                config=config,
                model_override=model_name,
                max_tokens_override=max_tokens,
            ) as async_llm_request:
                if functions_to_analyze:
                    analyzer = ExternalInputAnalyzer(
                        mcp_server=mcp_server,
                        llm_request_fn=async_llm_request,
                        rate_limit=EXTERNAL_INPUT_RATE_LIMIT,
                        max_workers=max_workers,
                        context_window=context_window,
                        on_result_callback=_persist_result,
                    )

                    results = await analyzer.analyze_all(functions_to_analyze)

                    # Retry functions that failed due to LLM/rate-limit errors
                    failed_functions = [
                        func for func, (_, reason) in results.items()
                        if reason in failed_reasons or reason.startswith("Error:")
                    ]

                    if failed_functions:
                        self.logger.info(
                            f"Retrying {len(failed_functions)} failed functions after 60s backoff..."
                        )
                        await asyncio.sleep(60)
                        retry_analyzer = ExternalInputAnalyzer(
                            mcp_server=mcp_server,
                            llm_request_fn=async_llm_request,
                            rate_limit=EXTERNAL_INPUT_RATE_LIMIT,
                            max_workers=max_workers,
                            context_window=context_window,
                            on_result_callback=_persist_result,
                        )
                        retry_results = await retry_analyzer.analyze_all(failed_functions)
                        for func, result in retry_results.items():
                            results[func] = result
                        analyzer._results.update(retry_results)
                else:
                    # All functions were cached — create analyzer just for annotation
                    analyzer = ExternalInputAnalyzer(
                        mcp_server=mcp_server,
                        llm_request_fn=async_llm_request,
                        rate_limit=EXTERNAL_INPUT_RATE_LIMIT,
                        max_workers=max_workers,
                        context_window=context_window,
                    )
                    results = {}
                return analyzer, results

        analyzer, results = asyncio.run(_run_ext_input_pool())

        # Merge cached results into analyzer for annotation
        all_results = dict(cached_results)
        all_results.update(results)
        analyzer._results = all_results

        # Annotate the call tree
        annotated_tree = analyzer.annotate_call_tree(call_tree)

        # Write output
        output_path = os.path.join(data_flow_paths['data_flow_dir'], "call_tree_with_sources.json")
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, 'w') as f:
            json.dump(annotated_tree, f, indent=2)

        # Write a flat list of functions that accept external input (with reasons)
        ext_input_functions = [
            {
                "function": node["function"],
                "location": node["location"],
                "reason": all_results[node["function"]][1],
            }
            for node in annotated_tree["call_tree"]["children"]
            if node.get("ext_input")
        ]
        ext_input_path = os.path.join(data_flow_paths['data_flow_dir'], "external_input_functions.json")
        with open(ext_input_path, 'w') as f:
            json.dump(ext_input_functions, f, indent=2)

        ext_count = len(ext_input_functions)
        self.logger.info(f"External input analysis complete: {ext_count}/{len(all_results)} functions accept external input")
        self.logger.info(f"Output written to: {output_path}")
        self.logger.info(f"External input functions written to: {ext_input_path}")

        return annotated_tree

    def _run_sink_discovery_with_llm(
        self,
        config: dict,
        call_tree: Dict[str, Any],
        max_workers: int
    ) -> Dict[str, Any]:
        """
        Use LLM with MCP code navigation tools to determine which
        functions are security-relevant data sinks.

        Mirrors _run_external_input_analysis: async workers in parallel,
        rate-limited, with caching.

        Args:
            config: Configuration dictionary
            call_tree: The call tree dict (may already have ext_input annotations)
            max_workers: Number of parallel async workers

        Returns:
            Dict with sink analysis results and annotated call tree
        """
        from .sink_analyzer import SinkAnalyzer
        from ..core.constants import (
            DEFAULT_LLM_API_END_POINT, DEFAULT_LLM_MODEL,
            DATA_FLOW_ANALYZER_MODEL, DATA_FLOW_ANALYZER_MAX_TOKENS,
            ModelLimits,
        )

        self.logger.info("Starting sink discovery with LLM...")

        # Load the raw call graph data for the MCP server
        ast_call_graph_dir = config['astCallGraphDir']
        nested_call_graph_path = os.path.join(ast_call_graph_dir, NESTED_CALL_GRAPH_FILE)

        with open(nested_call_graph_path, 'r') as f:
            call_graph_data = json.load(f)

        # Initialize MCP code navigation server
        mcp_server = CodeNavigationServer(
            repo_path=config['path_to_repo'],
            call_graph_data=call_graph_data
        )
        self.logger.info(f"MCP server initialized with {mcp_server.graph.get_num_nodes()} symbols")

        api_key = get_api_key_from_config(config)
        model_name = config.get('model', DATA_FLOW_ANALYZER_MODEL)
        max_tokens = config.get('max_tokens', DATA_FLOW_ANALYZER_MAX_TOKENS)
        context_window = ModelLimits.get_context_window(model_name)

        # Collect all function names from the call tree, respecting directory/file filters
        all_functions: List[str] = []

        def collect_functions(node: Dict[str, Any]) -> None:
            func_name = node.get("function", "")
            if func_name and func_name != "ROOT":
                if self._should_analyze_function(node, config):
                    all_functions.append(func_name)
            for child in node.get("children", []):
                collect_functions(child)

        collect_functions(call_tree.get("call_tree", {}))
        seen: set = set()
        unique_functions = []
        for f in all_functions:
            if f not in seen:
                seen.add(f)
                unique_functions.append(f)

        self.logger.info(f"Functions after directory/file filtering: {len(unique_functions)}")

        # Load cached results
        data_flow_paths = self.get_default_data_flow_paths(
            config['path_to_repo'], config.get('output_base_dir')
        )
        cache_path = os.path.join(data_flow_paths['data_flow_dir'], "sink_cache.json")
        cached_results: Dict[str, tuple] = {}
        if os.path.exists(cache_path):
            try:
                with open(cache_path, 'r') as f:
                    cache_data = json.load(f)
                for func_name, entry in cache_data.items():
                    cached_results[func_name] = (
                        entry["is_sink"], entry["reason"], entry.get("category", "none")
                    )
                self.logger.info(f"Loaded {len(cached_results)} cached sink results from {cache_path}")
            except (json.JSONDecodeError, KeyError, TypeError) as e:
                self.logger.warning(f"Failed to load sink cache, starting fresh: {e}")
                cached_results = {}

        # Filter out already-cached functions
        failed_reasons = {"LLM request failed", "Empty LLM response", "No answer produced",
                          "Max iterations exhausted without valid answer"}
        functions_to_analyze = [
            f for f in unique_functions
            if f not in cached_results
            or cached_results[f][1] in failed_reasons
            or cached_results[f][1].startswith("Error:")
        ]

        self.logger.info(f"Analyzing {len(functions_to_analyze)} functions for sinks "
                         f"({len(unique_functions) - len(functions_to_analyze)} cached, "
                         f"workers={max_workers}, rate_limit={SINK_ANALYSIS_RATE_LIMIT}/min)")

        # Incremental cache persistence
        import threading
        cache_lock = threading.Lock()

        def _persist_result(func_name: str, is_sink: bool, reason: str, category: str) -> None:
            with cache_lock:
                cached_results[func_name] = (is_sink, reason, category)
                cache_data = {
                    fn: {"is_sink": s, "reason": r, "category": c}
                    for fn, (s, r, c) in cached_results.items()
                }
                os.makedirs(os.path.dirname(cache_path), exist_ok=True)
                with open(cache_path, 'w') as f:
                    json.dump(cache_data, f, indent=2)

        async def _run_sink_pool():
            """Open the async LLM client once for the sink discovery run."""
            async with open_security_llm(
                api_key=api_key,
                config=config,
                model_override=model_name,
                max_tokens_override=max_tokens,
            ) as async_llm_request:
                if functions_to_analyze:
                    analyzer = SinkAnalyzer(
                        mcp_server=mcp_server,
                        llm_request_fn=async_llm_request,
                        rate_limit=SINK_ANALYSIS_RATE_LIMIT,
                        max_workers=max_workers,
                        context_window=context_window,
                        on_result_callback=_persist_result,
                    )

                    results = await analyzer.analyze_all(functions_to_analyze)

                    # Retry failed functions
                    failed_functions = [
                        func for func, (_, reason, _) in results.items()
                        if reason in failed_reasons or reason.startswith("Error:")
                    ]

                    if failed_functions:
                        self.logger.info(
                            f"Retrying {len(failed_functions)} failed sink functions after 60s backoff..."
                        )
                        await asyncio.sleep(60)
                        retry_analyzer = SinkAnalyzer(
                            mcp_server=mcp_server,
                            llm_request_fn=async_llm_request,
                            rate_limit=SINK_ANALYSIS_RATE_LIMIT,
                            max_workers=max_workers,
                            context_window=context_window,
                            on_result_callback=_persist_result,
                        )
                        retry_results = await retry_analyzer.analyze_all(failed_functions)
                        for func, result in retry_results.items():
                            results[func] = result
                        analyzer._results.update(retry_results)
                else:
                    analyzer = SinkAnalyzer(
                        mcp_server=mcp_server,
                        llm_request_fn=async_llm_request,
                        rate_limit=SINK_ANALYSIS_RATE_LIMIT,
                        max_workers=max_workers,
                        context_window=context_window,
                    )
                    results = {}
                return analyzer, results

        analyzer, results = asyncio.run(_run_sink_pool())

        # Merge cached + new results
        all_results = dict(cached_results)
        all_results.update(results)
        analyzer._results = all_results

        # Write data_sinks.json — flat list of functions classified as sinks
        sink_functions = []
        for func_name, (is_sink, reason, category) in all_results.items():
            if is_sink:
                # Look up location from the call tree
                location = self._find_function_location(call_tree, func_name)
                sink_functions.append({
                    "function": func_name,
                    "location": location,
                    "reason": reason,
                    "category": category,
                })

        data_sinks_path = os.path.join(data_flow_paths['data_flow_dir'], "data_sinks.json")
        os.makedirs(os.path.dirname(data_sinks_path), exist_ok=True)
        with open(data_sinks_path, 'w') as f:
            json.dump(sink_functions, f, indent=2)

        sink_count = len(sink_functions)
        self.logger.info(f"Sink discovery complete: {sink_count}/{len(all_results)} functions are sinks")
        self.logger.info(f"Data sinks written to: {data_sinks_path}")

        return {
            "all_results": all_results,
            "sink_functions": sink_functions,
            "analyzer": analyzer,
        }

    def _run_sink_discovery_with_domain_understanding(
        self,
        config: dict,
        call_tree: Dict[str, Any],
        sink_results: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Stage 2: Refine sink discovery using domain-specific understanding.

        Currently a no-op placeholder. Future implementation will use
        domain context (e.g., "this is a time daemon" or "this is a web server")
        to adjust sink classifications — promoting domain-specific sinks that
        the generic LLM pass may have missed, and demoting false positives.

        Args:
            config: Configuration dictionary
            call_tree: The call tree dict
            sink_results: Results from stage 1 (_run_sink_discovery_with_llm)

        Returns:
            The sink_results dict, unchanged.
        """
        self.logger.info("Sink discovery with domain understanding: no-op (not yet implemented)")
        return sink_results

    def _find_function_location(
        self, call_tree: Dict[str, Any], func_name: str
    ) -> List[Dict[str, Any]]:
        """Find the location entries for a function in the call tree."""
        def _search(node: Dict[str, Any]) -> Optional[List[Dict[str, Any]]]:
            if node.get("function") == func_name:
                return node.get("location", [])
            for child in node.get("children", []):
                found = _search(child)
                if found is not None:
                    return found
            return None

        result = _search(call_tree.get("call_tree", {}))
        return result if result is not None else []

    def _should_analyze_function(self, node: Dict[str, Any], config: dict) -> bool:
        """
        Check if a function node should be analyzed based on include_directories,
        exclude_directories, and exclude_files config filters.

        Uses partial path matching consistent with code_analyzer behavior.

        Args:
            node: A call tree node with 'function' and 'location' fields
            config: Configuration dictionary containing filtering parameters

        Returns:
            bool: True if the function should be analyzed
        """
        include_directories = config.get('include_directories', [])
        exclude_directories = config.get('exclude_directories', [])
        exclude_files = config.get('exclude_files', [])

        if not include_directories and not exclude_directories and not exclude_files:
            return True

        locations = node.get("location", [])
        if not locations:
            return True

        for loc in locations:
            file_path = loc.get("file_path", "")
            if not file_path:
                continue
            normalized = file_path.lstrip('./')
            if FilteredFileFinder.should_analyze_by_directory_filters(
                normalized, include_directories, exclude_directories, exclude_files
            ):
                return True

        return False

    def _run_path_discovery(
        self,
        config: dict,
        call_tree: Dict[str, Any],
        sink_results: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        """
        Step 6: Static path discovery — find call paths from sources to sinks.

        Loads the call graph, source functions, and sink functions, then runs
        BFS-based path finding to produce candidate data flow paths.

        Args:
            config: Configuration dictionary (must have astCallGraphDir, path_to_repo)
            call_tree: The call tree dict (with ext_input annotations)
            sink_results: Results dict from sink discovery (contains sink_functions list)

        Returns:
            List of candidate flow dicts
        """
        from .path_discovery import PathDiscovery

        self.logger.info("Starting static path discovery (source → sink)...")

        # Load the call graph
        ast_call_graph_dir = config['astCallGraphDir']
        nested_call_graph_path = os.path.join(ast_call_graph_dir, NESTED_CALL_GRAPH_FILE)

        with open(nested_call_graph_path, 'r') as f:
            call_graph_data = json.load(f)

        call_graph = load_call_graph_from_json(call_graph_data)
        self.logger.info(f"Call graph loaded: {call_graph.get_num_nodes()} nodes, {call_graph.get_num_edges()} edges")

        # Collect source functions from the annotated call tree
        source_functions: set = set()

        def collect_sources(node: Dict[str, Any]) -> None:
            if node.get("ext_input"):
                func_name = node.get("function", "")
                if func_name:
                    source_functions.add(func_name)
            for child in node.get("children", []):
                collect_sources(child)

        collect_sources(call_tree.get("call_tree", {}))

        # Also load from ext_input_cache.json for completeness (cached results
        # cover all analyzed functions, not just direct children of ROOT)
        data_flow_paths = self.get_default_data_flow_paths(
            config['path_to_repo'], config.get('output_base_dir')
        )
        ext_cache_path = os.path.join(data_flow_paths['data_flow_dir'], "ext_input_cache.json")
        if os.path.exists(ext_cache_path):
            with open(ext_cache_path, 'r') as f:
                ext_cache = json.load(f)
            for func_name, entry in ext_cache.items():
                if entry.get("ext_input"):
                    source_functions.add(func_name)

        # Collect sink functions → category mapping
        sink_function_map: Dict[str, str] = {}
        for sink_entry in sink_results.get("sink_functions", []):
            sink_function_map[sink_entry["function"]] = sink_entry.get("category", "unknown")

        self.logger.info(f"Sources: {len(source_functions)}, Sinks: {len(sink_function_map)}")

        # Run path discovery
        max_depth = config.get('path_discovery_max_depth', PATH_DISCOVERY_MAX_DEPTH)
        max_per_pair = config.get('path_discovery_max_paths_per_pair', PATH_DISCOVERY_MAX_PATHS_PER_PAIR)
        max_total = config.get('path_discovery_max_total_paths', PATH_DISCOVERY_MAX_TOTAL_PATHS)

        discoverer = PathDiscovery(
            call_graph=call_graph,
            max_path_depth=max_depth,
            max_paths_per_pair=max_per_pair,
            max_total_paths=max_total,
        )

        candidate_flows = discoverer.discover_paths(source_functions, sink_function_map)

        # Enrich flows with location data
        for flow in candidate_flows:
            flow["source_location"] = self._find_function_location(call_tree, flow["source"])
            flow["sink_location"] = self._find_function_location(call_tree, flow["sink"])

        # Write candidate_flows.json
        output_path = os.path.join(data_flow_paths['data_flow_dir'], "candidate_flows.json")
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, 'w') as f:
            json.dump(candidate_flows, f, indent=2)

        # Write statistics
        stats = discoverer.get_statistics(candidate_flows)
        stats_path = os.path.join(data_flow_paths['data_flow_dir'], "path_discovery_statistics.json")
        with open(stats_path, 'w') as f:
            json.dump(stats, f, indent=2)

        self.logger.info(f"Path discovery complete:")
        self.logger.info(f"  Candidate flows: {stats['total_candidate_flows']}")
        self.logger.info(f"  Unique (source, sink) pairs: {stats['unique_pairs']}")
        self.logger.info(f"  Unique sources with paths: {stats['unique_sources']}")
        self.logger.info(f"  Unique sinks reached: {stats['unique_sinks']}")
        if stats['path_length_distribution']:
            self.logger.info(f"  Path lengths: {stats['path_length_distribution']}")
        if stats['flows_by_sink_category']:
            top_cats = list(stats['flows_by_sink_category'].items())[:5]
            self.logger.info(f"  Top sink categories: {dict(top_cats)}")
        self.logger.info(f"  Output: {output_path}")

        return candidate_flows

    def _run_flow_vulnerability_analysis(
        self,
        config: dict,
        candidate_flows: List[Dict[str, Any]],
        max_workers: int,
    ) -> List[Dict[str, Any]]:
        """
        Step 7: LLM-based vulnerability analysis of candidate data flow paths.

        For each candidate flow, the LLM queries the CWE catalog to find relevant
        weakness patterns, reads code along the path, and determines whether untrusted
        data reaches the sink without adequate sanitization.

        Args:
            config: Configuration dictionary
            candidate_flows: List of candidate flow dicts from path discovery
            max_workers: Number of parallel async workers

        Returns:
            List of confirmed vulnerability findings
        """
        from .flow_vulnerability_analyzer import FlowVulnerabilityAnalyzer
        from ..core.knowledge.cwe_store import CWEStore
        from ..core.constants import (
            DEFAULT_LLM_API_END_POINT,
            DATA_FLOW_ANALYZER_MODEL, DATA_FLOW_ANALYZER_MAX_TOKENS,
            ModelLimits,
        )

        self.logger.info("Starting flow vulnerability analysis (CWE anti-pattern detection)...")

        if not candidate_flows:
            self.logger.info("No candidate flows to analyze")
            return []

        # Prioritize shorter paths (more likely to be exploitable)
        sorted_flows = sorted(candidate_flows, key=lambda f: f.get("path_length", 99))
        max_flows = config.get("flow_vuln_max_flows", FLOW_VULN_MAX_FLOWS_TO_ANALYZE)
        flows_to_analyze = sorted_flows[:max_flows]

        if len(candidate_flows) > max_flows:
            self.logger.info(
                f"Capped at {max_flows} flows (from {len(candidate_flows)} candidates), "
                f"prioritizing shortest paths"
            )

        # Load CWE catalog
        cwe_store = CWEStore()
        self.logger.info(f"CWE catalog loaded: {cwe_store.entry_count} entries")

        # Load the call graph data for MCP server
        ast_call_graph_dir = config['astCallGraphDir']
        nested_call_graph_path = os.path.join(ast_call_graph_dir, NESTED_CALL_GRAPH_FILE)

        with open(nested_call_graph_path, 'r') as f:
            call_graph_data = json.load(f)

        # Initialize MCP code navigation server
        mcp_server = CodeNavigationServer(
            repo_path=config['path_to_repo'],
            call_graph_data=call_graph_data
        )
        self.logger.info(f"MCP server initialized with {mcp_server.graph.get_num_nodes()} symbols")

        # Build LLM client config
        api_key = get_api_key_from_config(config)
        model_name = config.get('model', DATA_FLOW_ANALYZER_MODEL)
        max_tokens = config.get('max_tokens', DATA_FLOW_ANALYZER_MAX_TOKENS)
        context_window = ModelLimits.get_context_window(model_name)

        data_flow_paths = self.get_default_data_flow_paths(
            config['path_to_repo'], config.get('output_base_dir')
        )
        cache_path = os.path.join(data_flow_paths['data_flow_dir'], "vulnerability_cache.json")
        prompts_dir = os.path.join(data_flow_paths['data_flow_dir'], "prompts_sent")

        self.logger.info(
            f"Analyzing {len(flows_to_analyze)} flows for vulnerabilities "
            f"(workers={max_workers}, rate_limit={FLOW_VULN_RATE_LIMIT}/min)"
        )

        async def _run_flow_vuln_pool():
            """Open the async LLM client once for the vulnerability scan."""
            async with open_security_llm(
                api_key=api_key,
                config=config,
                model_override=model_name,
                max_tokens_override=max_tokens,
            ) as async_llm_request:
                analyzer = FlowVulnerabilityAnalyzer(
                    mcp_server=mcp_server,
                    cwe_store=cwe_store,
                    llm_request_fn=async_llm_request,
                    rate_limit=FLOW_VULN_RATE_LIMIT,
                    max_workers=max_workers,
                    context_window=context_window,
                    prompts_dir=prompts_dir,
                    cache_path=cache_path,
                )
                return await analyzer.analyze_all(flows_to_analyze)

        all_results = asyncio.run(_run_flow_vuln_pool())

        # Collect all confirmed vulnerabilities
        all_vulns: List[Dict[str, Any]] = []
        for flow_key, vulns in all_results.items():
            source, sink = flow_key.split("|", 1) if "|" in flow_key else (flow_key, "")
            for vuln in vulns:
                vuln["source"] = source
                vuln["sink"] = sink
                all_vulns.append(vuln)

        # Write output
        output_path = os.path.join(data_flow_paths['data_flow_dir'], "vulnerabilities.json")
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, 'w') as f:
            json.dump(all_vulns, f, indent=2)

        vuln_count = len(all_vulns)
        flows_with_vulns = sum(1 for v in all_results.values() if v)
        self.logger.info(f"Flow vulnerability analysis complete:")
        self.logger.info(f"  Total vulnerabilities found: {vuln_count}")
        self.logger.info(f"  Flows with vulnerabilities: {flows_with_vulns}/{len(all_results)}")
        if all_vulns:
            severity_counts = {}
            for v in all_vulns:
                sev = v.get("severity", "unknown")
                severity_counts[sev] = severity_counts.get(sev, 0) + 1
            self.logger.info(f"  By severity: {severity_counts}")
        self.logger.info(f"  Output: {output_path}")

        return all_vulns

    def merge_include_exclude_directories_from_config_and_params(
        self,
        config_dict: Dict[str, Any],
        include_directories: List[str] = None,
        exclude_directories: List[str] = None
    ):
        """
        Merge include and exclude directories from config and command-line parameters.

        Args:
            config_dict: Configuration dictionary
            include_directories: List of directories to include from command line
            exclude_directories: List of directories to exclude from command line

        Returns:
            Tuple of (computed_include_directories, computed_exclude_directories)
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

    def run(
        self,
        config_dict: Dict[str, Any],
        repo_path: str,
        out_dir: str,
        force_recreate_ast: bool = False,
        exclude_directories: List[str] = None,
        include_directories: List[str] = None,
        exclude_files: List[str] = None,
        max_call_depth: int = 20,
        show_location: bool = True,
        sort_by_depth: bool = True,
        max_workers: int = EXTERNAL_INPUT_DEFAULT_WORKERS,
        enable_cwe_analysis: bool = True,
    ):
        """
        Main entry point for the Data Flow Analyzer.

        Args:
            config_dict: Configuration dictionary
            repo_path: Path to repository directory
            out_dir: Output directory
            force_recreate_ast: Force recreation of AST call graphs
            exclude_directories: List of directories to exclude
            include_directories: List of directories to include
            exclude_files: List of files to exclude
            max_call_depth: Maximum depth for call tree generation
            show_location: Show file locations in text output
            sort_by_depth: Sort branches by depth (longest first, default: True)
            max_workers: Number of parallel workers for external input analysis
            enable_cwe_analysis: Enable CWE vulnerability analysis on candidate flows
        """
        # Merge include/exclude directories from config and params
        computed_include_directories, computed_exclude_directories = \
            self.merge_include_exclude_directories_from_config_and_params(
                config_dict, include_directories, exclude_directories
            )

        # Merge exclude_files from config and params
        config_exclude_files = config_dict.get('exclude_files', []) or []
        exclude_files_from_config = set(config_exclude_files) if config_exclude_files else set()
        exclude_files_from_args = set(exclude_files) if exclude_files else set()
        computed_exclude_files = list(exclude_files_from_config.union(exclude_files_from_args))

        self.logger.info(f"Arguments passed to runner.run:")
        self.logger.info(f"  config_dict: {config_dict}")
        self.logger.info(f"  repo_path: {repo_path}")
        self.logger.info(f"  out_dir: {out_dir}")
        self.logger.info(f"  force_recreate_ast: {force_recreate_ast}")
        self.logger.info(f"  exclude_directories: {computed_exclude_directories}")
        self.logger.info(f"  include_directories: {computed_include_directories}")
        self.logger.info(f"  exclude_files: {computed_exclude_files}")
        self.logger.info(f"  max_call_depth: {max_call_depth}")
        self.logger.info(f"  show_location: {show_location}")
        self.logger.info(f"  sort_by_depth: {sort_by_depth}")

        try:
            # Start sleep prevention early to keep Mac awake during entire analysis
            self._start_sleep_prevention()

            config = config_dict.copy()

            # Set repo_path in config
            config['path_to_repo'] = repo_path
            config['max_call_depth'] = max_call_depth
            config['show_location'] = show_location
            config['sort_by_depth'] = sort_by_depth
            config['enable_cwe_analysis'] = enable_cwe_analysis

            # Set merged directories and files
            config['exclude_directories'] = computed_exclude_directories
            config['include_directories'] = computed_include_directories
            config['exclude_files'] = computed_exclude_files

            # Determine the output base directory
            output_base_dir = out_dir
            if output_base_dir:
                output_base_dir = os.path.abspath(output_base_dir)
                self.logger.info(f"Using output directory: {output_base_dir}")
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

            # Step 1: Directory Classification (LLM-based)
            self.logger.info("\n\n=== DIRECTORY CLASSIFICATION ===")
            self._run_directory_classification(config)

            # Step 2: AST Generation
            self.logger.info("\n\n=== AST CALL GRAPH GENERATION ===")
            
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
                # Generate AST call graph
                nested_call_graph_path = self._generate_ast_call_graph(config)
                self.logger.info("AST call graph generation completed successfully!")
            else:
                self.logger.info("Skipping AST call graph generation - using existing files")

            # Step 3: Call Tree Generation
            self.logger.info("\n\n=== CALL TREE GENERATION ===")
            data_flow_paths = self.get_default_data_flow_paths(
                config['path_to_repo'], config.get('output_base_dir')
            )
            call_tree_path = data_flow_paths['call_tree_json']

            if not force_recreate_ast and os.path.exists(call_tree_path):
                self.logger.info(f"Existing call tree found - loading from {call_tree_path}")
                with open(call_tree_path, 'r') as f:
                    call_tree = json.load(f)
            else:
                call_tree = self._generate_call_tree(config)
            
            if call_tree:
                metadata = call_tree.get('metadata', {})
                self.logger.info(f"\nCall tree generation completed!")
                self.logger.info(f"  Total functions: {metadata.get('total_functions', 0)}")
                self.logger.info(f"  Root nodes: {metadata.get('total_root_nodes', 0)}")
                self.logger.info(f"  DAG edges: {metadata.get('dag_edges_count', 0)}")

            # Step 4: External Input Analysis (LLM + MCP tools)
            if call_tree:
                self.logger.info("\n\n=== EXTERNAL INPUT ANALYSIS ===")
                self._run_external_input_analysis(config, call_tree, max_workers)

            # Step 5: Sink Discovery (LLM + domain understanding)
            sink_results = None
            if call_tree:
                self.logger.info("\n\n=== SINK DISCOVERY ===")
                # Stage 1: LLM-based sink classification
                sink_results = self._run_sink_discovery_with_llm(config, call_tree, max_workers)
                # Stage 2: Domain-understanding refinement (no-op for now)
                self._run_sink_discovery_with_domain_understanding(config, call_tree, sink_results)

            # Step 6: Path Discovery (static graph traversal)
            candidate_flows = None
            if call_tree and sink_results:
                candidate_flows_path = os.path.join(
                    data_flow_paths['data_flow_dir'], "candidate_flows.json"
                )
                if os.path.exists(candidate_flows_path):
                    self.logger.info("\n\n=== PATH DISCOVERY (Source → Sink) ===")
                    self.logger.info(f"Existing candidate_flows.json found - loading")
                    with open(candidate_flows_path, 'r') as f:
                        candidate_flows = json.load(f)
                else:
                    self.logger.info("\n\n=== PATH DISCOVERY (Source → Sink) ===")
                    candidate_flows = self._run_path_discovery(config, call_tree, sink_results)

            # Step 7: Flow Vulnerability Analysis (LLM + CWE tools)
            if candidate_flows and config.get('enable_cwe_analysis', True):
                self.logger.info("\n\n=== FLOW VULNERABILITY ANALYSIS (CWE) ===")
                self._run_flow_vulnerability_analysis(config, candidate_flows, max_workers)
            elif candidate_flows and not config.get('enable_cwe_analysis', True):
                self.logger.info("\n\nCWE vulnerability analysis disabled by config")

            self.logger.info("\n\nData flow analysis pipeline completed successfully!")

        except ConfigValidationError as e:
            self.logger.error(f"Configuration validation failed: {e}")
            print(f"\n❌ Analysis failed with configuration error")
            print(f"Error: {e}")
            sys.exit(1)
        except Exception as e:
            self.logger.error(f"Unexpected error: {e}")
            print(f"\n❌ Analysis failed with unexpected error")
            print(f"Error: {e}")
            traceback.print_exc()
            sys.exit(1)
        finally:
            # Always stop sleep prevention when done
            self._stop_sleep_prevention()


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Data Flow Analyzer - Generates call trees from AST call graphs",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
EXAMPLES:
=========
# Basic usage
python -m hindsight.analyzers.data_flow_analyzer --config config.json --repo /path/to/repo

# With custom output directory
python -m hindsight.analyzers.data_flow_analyzer --config config.json --repo /path/to/repo --out-dir ~/my_artifacts

# Force AST regeneration
python -m hindsight.analyzers.data_flow_analyzer --config config.json --repo /path/to/repo --force-recreate-ast

# With directory filters
python -m hindsight.analyzers.data_flow_analyzer --config config.json --repo /path/to/repo \\
    --include-directories src lib --exclude-directories test vendor

# Custom call depth
python -m hindsight.analyzers.data_flow_analyzer --config config.json --repo /path/to/repo --max-call-depth 30
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
        help="Output directory (default: ~/llm_artifacts)"
    )
    parser.add_argument(
        "--force-recreate-ast",
        action="store_true",
        help="Force recreation of AST call graphs even if they already exist"
    )
    parser.add_argument(
        "--exclude-directories",
        nargs="+",
        help="List of directories to exclude from analysis"
    )
    parser.add_argument(
        "--include-directories",
        nargs="+",
        help="List of directories to include in analysis"
    )
    parser.add_argument(
        "--exclude-files",
        nargs="+",
        help="List of files to exclude from analysis"
    )
    parser.add_argument(
        "--max-call-depth",
        type=int,
        default=20,
        help="Maximum depth for call tree generation (default: 20)"
    )
    parser.add_argument(
        "--show-location",
        action="store_true",
        default=True,
        help="Show file locations in text output (default: True)"
    )
    parser.add_argument(
        "--no-show-location",
        action="store_false",
        dest="show_location",
        help="Hide file locations in text output"
    )
    parser.add_argument(
        "--sort-by-depth",
        action="store_true",
        default=True,
        help="Sort branches by depth (longest first, default: True)"
    )
    parser.add_argument(
        "--no-sort-by-depth",
        action="store_false",
        dest="sort_by_depth",
        help="Sort branches alphabetically instead of by depth"
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=EXTERNAL_INPUT_DEFAULT_WORKERS,
        help=f"Number of parallel workers for external input analysis (default: {EXTERNAL_INPUT_DEFAULT_WORKERS})"
    )
    parser.add_argument(
        "--no-cwe-analysis",
        action="store_true",
        default=False,
        help="Disable CWE vulnerability analysis on candidate flows (Step 7)"
    )

    args = parser.parse_args()

    # Setup logging
    setup_default_logging()

    # Load config
    logger.info(f"Loading configuration from: {args.config}")
    try:
        config = load_and_validate_config(args.config)
    except ConfigValidationError as e:
        logger.error(f"Configuration validation failed: {e}")
        sys.exit(1)

    # Create and run analyzer
    runner = DataFlowAnalysisRunner()
    
    runner.run(
        config_dict=config,
        repo_path=args.repo,
        out_dir=args.out_dir,
        force_recreate_ast=args.force_recreate_ast,
        exclude_directories=args.exclude_directories,
        include_directories=args.include_directories,
        exclude_files=args.exclude_files,
        max_call_depth=args.max_call_depth,
        show_location=args.show_location,
        sort_by_depth=args.sort_by_depth,
        max_workers=args.workers,
        enable_cwe_analysis=not args.no_cwe_analysis,
    )


if __name__ == "__main__":
    main()
