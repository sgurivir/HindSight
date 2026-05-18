#!/usr/bin/env python3
"""
Performance Analyzer

Analyzes call paths for in-place performance optimization opportunities.
Identifies issues related to CPU, memory, power, and I/O efficiency.

Uses a two-stage LLM process:
- Stage A: Context collection along a call path (with per-function caching)
- Stage B: Performance issue identification

Employs edge coloring to avoid redundant analysis of overlapping paths.
"""

import argparse
import asyncio
import json
import os
import sys
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Set, Tuple

from .analysis_runner import AnalysisRunner
from .analysis_runner_mixins import ReportGeneratorMixin
from .call_path_enumerator import CallPathEnumerator
from .edge_coloring_tracker import EdgeColoringTracker
from .llm_based_analyzer import LLMBasedAnalyzer
from ..core.ast_index import RepoAstIndex
from ..core.async_infra import RateLimiter, run_worker_pool
from ..core.constants import (
    DEFAULT_MAX_TOKENS, DEFAULT_LLM_MODEL, DEFAULT_LLM_API_END_POINT,
    LLM_PROVIDER_RATE_LIMIT, LLM_PROVIDER_RATE_WINDOW_SECONDS,
)
from ..core.lang_util.call_graph_util import CallGraph
from ..core.llm.perf_analysis import PerfAnalysis, PerfAnalysisConfig
from ..core.mcp_tools.analysis_server import AnalysisMCPServer
from ..report.report_generator import generate_html_report
from ..utils.config_util import load_and_validate_config, get_api_key_from_config, get_llm_provider_type
from ..utils.log_util import get_logger, setup_default_logging
from ..utils.output_directory_provider import get_output_directory_provider

setup_default_logging()
logger = get_logger(__name__)

# Perf analyzer defaults
PERF_DEFAULT_MIN_PATH_DEPTH = 3
PERF_DEFAULT_MAX_PATH_DEPTH = 8
PERF_DEFAULT_MAX_PATHS = 500
PERF_DEFAULT_MAX_WORKERS = 4
PERF_DEFAULT_RPM_LIMIT = LLM_PROVIDER_RATE_LIMIT
PERF_DEFAULT_MIN_FUNCTION_LINES = 5


class PerfAnalyzer(LLMBasedAnalyzer):
    """Analyzer that identifies performance optimization opportunities along call paths."""

    def __init__(self):
        super().__init__()
        self.ast_index = RepoAstIndex()
        self.edge_tracker = EdgeColoringTracker()
        self._all_issues: List[Dict[str, Any]] = []

    def name(self) -> str:
        return "PerfAnalyzer"

    def initialize(self, config: Mapping[str, Any]) -> None:
        """Setup, load models, and prepare for analysis."""
        super().initialize(config)
        try:
            self.ast_index.validate_ast_built()
        except RuntimeError as e:
            self.logger.warning(f"AST validation failed: {e}")

    def analyze_function(self, func_record: Mapping[str, Any], mcp_server=None) -> Optional[Mapping[str, Any]]:
        """
        Not used by perf_analyzer (it works on paths, not individual functions).
        Kept for interface compatibility.
        """
        return None

    async def analyze_path(self, path: List[str], mcp_server=None) -> Optional[List[Dict[str, Any]]]:
        """
        Analyze a single call path for performance issues.

        Args:
            path: Ordered list of function names [root, ..., leaf]
            mcp_server: Optional MCP server for tool dispatch

        Returns:
            List of issue dicts or None on failure
        """
        if not self._initialized:
            raise RuntimeError("Analyzer not initialized. Call initialize() first.")

        try:
            self._wait_for_rate_limit()

            perf_config = PerfAnalysisConfig(
                api_key=self.api_key,
                api_url=self.config.get("api_end_point", DEFAULT_LLM_API_END_POINT),
                model=self.config.get("model", DEFAULT_LLM_MODEL),
                repo_path=self.repo_path,
                max_tokens=DEFAULT_MAX_TOKENS,
                config=self.config,
                file_content_provider=self.file_content_provider,
                llm_provider_type=self.config.get("llm_provider_type", "aws_bedrock"),
            )

            analysis = PerfAnalysis(perf_config, mcp_server=mcp_server)
            analysis.ast_index = self.ast_index

            issues = await analysis.analyze_path(path, mcp_server=mcp_server)
            return issues

        except Exception as e:
            self.logger.error(f"Error analyzing path: {e}\n{traceback.format_exc()}")
            return None

    def finalize(self) -> None:
        """Cleanup after analysis."""
        pass

    def get_all_issues(self) -> List[Dict[str, Any]]:
        """Get all collected issues."""
        return self._all_issues

    def add_issues(self, issues: List[Dict[str, Any]]) -> None:
        """Add issues from a path analysis."""
        if issues:
            self._all_issues.extend(issues)


class PerfAnalysisRunner(ReportGeneratorMixin, AnalysisRunner):
    """
    CLI runner for the performance analyzer.

    Orchestrates: path enumeration → edge coloring → async analysis → dedup → report.
    """

    def __init__(self):
        super().__init__()
        self.analyzer = PerfAnalyzer()
        self.edge_tracker = EdgeColoringTracker()

    def run(self) -> int:
        """Main entry point. Returns exit code."""
        args = self._parse_args()
        try:
            return asyncio.run(self._async_run(args))
        except KeyboardInterrupt:
            logger.info("Analysis interrupted by user")
            return 1
        except Exception as e:
            logger.error(f"Fatal error: {e}\n{traceback.format_exc()}")
            return 1

    async def _async_run(self, args) -> int:
        """Async main logic."""
        # Load config
        config = self._load_config(args)
        if config is None:
            return 1

        repo_path = config["path_to_repo"]
        logger.info(f"Starting performance analysis for: {repo_path}")

        # Initialize analyzer
        self.analyzer.initialize(config)

        # Load call graph
        call_graph = self._build_call_graph()
        if call_graph is None:
            logger.error("Could not build call graph from AST")
            return 1

        logger.info(f"Call graph: {call_graph.get_num_nodes()} nodes, {call_graph.get_num_edges()} edges")

        # Enumerate paths
        entry_points = set(config.get("entry_points", [])) or None
        enumerator = CallPathEnumerator(
            call_graph=call_graph,
            merged_functions=self.analyzer.ast_index.merged_functions or {},
            min_path_depth=config.get("min_path_depth", PERF_DEFAULT_MIN_PATH_DEPTH),
            max_path_depth=config.get("max_path_depth", PERF_DEFAULT_MAX_PATH_DEPTH),
            max_paths=config.get("max_paths", PERF_DEFAULT_MAX_PATHS),
            entry_points=entry_points,
            min_function_lines=config.get("min_function_lines", PERF_DEFAULT_MIN_FUNCTION_LINES),
            hot_modules=config.get("hot_modules"),
        )

        all_paths = enumerator.enumerate_paths()
        if not all_paths:
            logger.warning("No analysis-worthy call paths found")
            return 0

        logger.info(f"Enumerated {len(all_paths)} candidate paths")

        # Edge coloring filter — only analyze paths with novel edges
        paths_to_analyze = [p for p in all_paths if self.edge_tracker.has_novel_edges(p)]
        logger.info(f"After edge coloring filter: {len(paths_to_analyze)} paths to analyze")

        if not paths_to_analyze:
            logger.info("All paths already covered by edge coloring — nothing to analyze")
            return 0

        # Create MCP server for tool dispatch
        mcp_server = self._create_mcp_server(config, call_graph)

        # Run async worker pool
        max_workers = config.get("max_concurrent_analyses", PERF_DEFAULT_MAX_WORKERS)
        rpm_limit = config.get("rpm_limit", PERF_DEFAULT_RPM_LIMIT)
        rate_limiter = RateLimiter(max_requests=rpm_limit,
                                   window_seconds=LLM_PROVIDER_RATE_WINDOW_SECONDS)

        async def worker_fn(path: List[str]) -> Optional[List[Dict]]:
            return await self.analyzer.analyze_path(path, mcp_server=mcp_server)

        def on_result(path: List[str], result: Optional[List[Dict]]) -> None:
            self.edge_tracker.mark_analyzed(path)
            if result:
                self.analyzer.add_issues(result)
                logger.info(f"Path complete: {len(result)} issues found")

        def on_error(path: List[str], exc: Exception) -> None:
            logger.error(f"Path analysis failed: {path[0]}→...→{path[-1]}: {exc}")

        logger.info(f"Starting async analysis: {len(paths_to_analyze)} paths, "
                    f"{max_workers} workers, {rpm_limit} RPM limit")

        await run_worker_pool(
            items=paths_to_analyze,
            worker_fn=worker_fn,
            max_workers=max_workers,
            rate_limiter=rate_limiter,
            on_result=on_result,
            on_error=on_error,
        )

        # Post-processing: deduplicate issues
        all_issues = self.analyzer.get_all_issues()
        deduped_issues = self._deduplicate_issues(all_issues)
        logger.info(f"Issues: {len(all_issues)} total → {len(deduped_issues)} after dedup")

        # Coverage stats
        stats = self.edge_tracker.get_coverage_stats()
        logger.info(f"Edge coverage: {stats['analyzed_edges']} edges analyzed")

        # Generate report
        if deduped_issues:
            self._generate_report(deduped_issues, config)

        # Save raw results
        self._save_results(deduped_issues, config)

        return 0

    def _build_call_graph(self) -> Optional[CallGraph]:
        """Build CallGraph from AST merged_call_graph data."""
        merged_call_graph = self.analyzer.ast_index.merged_call_graph
        if not merged_call_graph:
            return None

        graph = CallGraph()

        if isinstance(merged_call_graph, dict):
            for caller, callees in merged_call_graph.items():
                graph.add_node(caller)
                if isinstance(callees, list):
                    for callee in callees:
                        if isinstance(callee, str):
                            graph.add_edge(caller, callee)
                        elif isinstance(callee, dict):
                            callee_name = callee.get("name") or callee.get("callee")
                            if callee_name:
                                graph.add_edge(caller, callee_name)
                elif isinstance(callees, dict):
                    for callee_name in callees:
                        graph.add_edge(caller, callee_name)

        return graph if graph.get_num_nodes() > 0 else None

    def _create_mcp_server(self, config: dict, call_graph: CallGraph) -> Optional[AnalysisMCPServer]:
        """Create MCP server for unified tool dispatch."""
        try:
            repo_path = config["path_to_repo"]
            output_provider = get_output_directory_provider()
            artifacts_dir = f"{output_provider.get_repo_artifacts_dir()}/code_insights"

            ignore_dirs = set()
            for dir_name in config.get("exclude_directories", []):
                ignore_dirs.add(dir_name)
                ignore_dirs.add(dir_name.upper())
                ignore_dirs.add(dir_name.lower())

            mcp_server = AnalysisMCPServer(
                repo_path=repo_path,
                file_content_provider=self.analyzer.file_content_provider,
                artifacts_dir=artifacts_dir,
                ignore_dirs=ignore_dirs,
                call_graph_data=self.analyzer.ast_index.merged_call_graph,
            )
            return mcp_server
        except Exception as e:
            logger.warning(f"Could not create MCP server: {e}")
            return None

    def _deduplicate_issues(self, issues: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Deduplicate issues by (file_path, function_name, issueType, line_overlap)."""
        if not issues:
            return []

        seen: Dict[str, Dict[str, Any]] = {}
        for issue in issues:
            if not isinstance(issue, dict):
                continue
            file_path = issue.get("file_path", "")
            func_name = issue.get("function_name", "")
            issue_type = issue.get("issueType", "")
            line = str(issue.get("line_number", ""))

            key = f"{file_path}:{func_name}:{issue_type}:{line}"
            if key not in seen:
                seen[key] = issue
            else:
                # Keep the one with longer description (richer context)
                existing_desc = len(seen[key].get("description", ""))
                new_desc = len(issue.get("description", ""))
                if new_desc > existing_desc:
                    seen[key] = issue

        return list(seen.values())

    def _generate_report(self, issues: List[Dict[str, Any]], config: dict) -> None:
        """Generate HTML report."""
        try:
            reports_dir = self.get_reports_directory()
            report_file = os.path.join(reports_dir, "perf_analysis_report.html")
            project_name = config.get("project_name", os.path.basename(config["path_to_repo"]))

            generate_html_report(
                issues=issues,
                output_file=report_file,
                project_name=project_name,
                analysis_type="Performance Analysis",
            )
            logger.info(f"HTML report generated: {report_file}")
        except Exception as e:
            logger.error(f"Report generation failed: {e}")

    def _save_results(self, issues: List[Dict[str, Any]], config: dict) -> None:
        """Save raw results JSON."""
        try:
            output_provider = get_output_directory_provider()
            results_dir = f"{output_provider.get_repo_artifacts_dir()}/results/perf_analysis"
            os.makedirs(results_dir, exist_ok=True)

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_file = os.path.join(results_dir, f"perf_results_{timestamp}.json")

            with open(output_file, "w", encoding="utf-8") as f:
                json.dump(issues, f, indent=2, ensure_ascii=False)

            logger.info(f"Results saved: {output_file} ({len(issues)} issues)")
        except Exception as e:
            logger.error(f"Failed to save results: {e}")

    def _load_config(self, args) -> Optional[dict]:
        """Load and validate configuration."""
        try:
            config = load_and_validate_config(args.config)
            if args.repo:
                config["path_to_repo"] = str(Path(args.repo).resolve())

            # Get API key
            api_key = get_api_key_from_config(config)
            if not api_key:
                logger.error("No API key configured")
                return None
            config["api_key"] = api_key

            # Repo path validation
            if not config.get("path_to_repo"):
                logger.error("No repository path specified")
                return None
            if not os.path.isdir(config["path_to_repo"]):
                logger.error(f"Repository not found: {config['path_to_repo']}")
                return None

            # Set LLM provider type
            config["llm_provider_type"] = get_llm_provider_type(config)

            # Perf-specific config overrides from CLI
            if args.max_path_depth:
                config["max_path_depth"] = args.max_path_depth
            if args.min_path_depth:
                config["min_path_depth"] = args.min_path_depth
            if args.max_paths:
                config["max_paths"] = args.max_paths
            if args.max_workers:
                config["max_concurrent_analyses"] = args.max_workers

            return config
        except Exception as e:
            logger.error(f"Config error: {e}")
            return None

    def _parse_args(self):
        """Parse command-line arguments."""
        parser = argparse.ArgumentParser(description="Hindsight Performance Analyzer")
        parser.add_argument("--config", required=True, help="Path to config JSON file")
        parser.add_argument("--repo", help="Path to repository (overrides config)")
        parser.add_argument("--max-path-depth", type=int, help="Maximum call path depth")
        parser.add_argument("--min-path-depth", type=int, help="Minimum call path depth")
        parser.add_argument("--max-paths", type=int, help="Maximum number of paths to analyze")
        parser.add_argument("--max-workers", type=int, help="Maximum concurrent analyses")
        parser.add_argument("--entry-points", nargs="+", help="Function names to start enumeration from")
        parser.add_argument("--hot-modules", nargs="+", help="Directory names to prioritize")
        return parser.parse_args()


def main():
    """CLI entry point."""
    runner = PerfAnalysisRunner()
    sys.exit(runner.run())


if __name__ == "__main__":
    main()
