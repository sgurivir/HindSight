#!/usr/bin/env python3
"""
Performance Analyzer — runs on the new async orchestration stack.

Analyzes call paths for in-place performance optimization opportunities.
Identifies issues related to CPU, memory, power, and I/O efficiency.

Uses a two-stage LLM process via `hindsight.orchestration.PerfPipeline`:
- Stage A — Context collection along a call path (with per-function cache)
- Stage B — Performance issue identification

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
from typing import Any, Dict, List, Mapping, Optional, Set

from .analysis_runner import AnalysisRunner
from .analysis_runner_mixins import ReportGeneratorMixin
from .call_path_enumerator import CallPathEnumerator
from .edge_coloring_tracker import EdgeColoringTracker
from .llm_based_analyzer import LLMBasedAnalyzer
from ..core.ast_index import RepoAstIndex
from ..core.constants import (
    DEFAULT_LLM_API_END_POINT,
    DEFAULT_LLM_MODEL,
    DEFAULT_MAX_TOKENS,
    LLM_PROVIDER_RATE_LIMIT,
)
from ..core.lang_util.call_graph_util import CallGraph
from ..orchestration import (
    AnalysisContext,
    AnalysisSession,
    PerfPathWork,
    PerfPipeline,
    PerfRunSummary,
    perf_function_checksum,
)
from ..report.report_generator import generate_html_report
from ..utils.config_util import (
    get_api_key_from_config,
    get_llm_provider_type,
    load_and_validate_config,
)
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
    """Path-based performance analyzer; defers the LLM stages to `PerfPipeline`."""

    def __init__(self):
        super().__init__()
        self.ast_index = RepoAstIndex()
        self.edge_tracker = EdgeColoringTracker()
        self._all_issues: List[Dict[str, Any]] = []

    def name(self) -> str:
        return "PerfAnalyzer"

    def initialize(self, config: Mapping[str, Any]) -> None:
        """Setup, load AST, prepare for analysis."""
        super().initialize(config)
        try:
            self.ast_index.validate_ast_built()
        except RuntimeError as e:
            self.logger.warning(f"AST validation failed: {e}")

    def analyze_function(self, func_record, mcp_server=None):
        """Not used by perf — kept for interface compatibility."""
        return None

    def finalize(self) -> None:
        pass

    def get_all_issues(self) -> List[Dict[str, Any]]:
        return self._all_issues

    def add_issues(self, issues: List[Dict[str, Any]]) -> None:
        if issues:
            self._all_issues.extend(issues)

    # ------------------------------------------------------------------
    # PerfPipeline integration
    # ------------------------------------------------------------------

    def build_path_work(self, path: List[str]) -> PerfPathWork:
        """Materialize a `PerfPathWork` from an AST-resolved call path.

        Mirrors the legacy `PerfAnalysis._get_function_bodies` + checksum
        derivation, but produces the typed work item the new pipeline expects.
        Returns an item with empty bodies/checksums for unknown functions —
        Stage A will just lack pre-collected context for those.
        """
        bodies: Dict[str, Dict[str, Any]] = {}
        checksums: Dict[str, str] = {}
        merged = self.ast_index.merged_functions or {}

        for func_name in path:
            func_data = merged.get(func_name) or {}
            file_path = func_data.get("file", "") or func_data.get("file_path", "")
            start = int(func_data.get("start_line", 0) or 0)
            end = int(func_data.get("end_line", 0) or 0)
            body = func_data.get("body", "") or func_data.get("source", "")
            bodies[func_name] = {
                "file": file_path,
                "start_line": start,
                "end_line": end,
                "body": body,
            }
            checksums[func_name] = perf_function_checksum(func_name, file_path, start, end)

        return PerfPathWork(
            path=tuple(path),
            function_bodies=bodies,
            function_checksums=checksums,
        )


class PerfAnalysisRunner(ReportGeneratorMixin, AnalysisRunner):
    """CLI runner for performance analysis.

    Orchestrates: path enumeration → edge coloring → async analysis via the
    new `PerfPipeline` → dedup → report. CLI surface is preserved verbatim.
    """

    def __init__(self):
        super().__init__()
        self.analyzer = PerfAnalyzer()
        self.edge_tracker = EdgeColoringTracker()

    def run(self) -> int:
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
        config = self._load_config(args)
        if config is None:
            return 1

        repo_path = config["path_to_repo"]
        logger.info(f"Starting performance analysis for: {repo_path}")

        self.analyzer.initialize(config)

        call_graph = self._build_call_graph()
        if call_graph is None:
            logger.error("Could not build call graph from AST")
            return 1

        logger.info(
            f"Call graph: {call_graph.get_num_nodes()} nodes, {call_graph.get_num_edges()} edges"
        )

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

        # Drive the new async pipeline.
        summary = await self._run_pipeline(config, paths_to_analyze)

        # Mark each analyzed path on the edge tracker so future runs short-circuit.
        for path in paths_to_analyze:
            self.edge_tracker.mark_analyzed(path)

        # Post-processing
        all_issues = list(summary.issues)
        self.analyzer.add_issues(all_issues)
        deduped_issues = self._deduplicate_issues(all_issues)
        logger.info(f"Issues: {len(all_issues)} total → {len(deduped_issues)} after dedup")

        stats = self.edge_tracker.get_coverage_stats()
        logger.info(f"Edge coverage: {stats['analyzed_edges']} edges analyzed")

        if deduped_issues:
            self._generate_report(deduped_issues, config)

        self._save_results(deduped_issues, config)
        return 0

    async def _run_pipeline(
        self,
        config: dict,
        paths: List[List[str]],
    ) -> PerfRunSummary:
        """Build a session + pipeline and analyze every path."""
        # Resolve the output base dir from the configured singleton — perf
        # config doesn't carry it explicitly, so fall back to whichever
        # directory `OutputDirectoryProvider` already established.
        output_provider = get_output_directory_provider()
        output_base = (
            output_provider.get_custom_base_dir()
            if output_provider.is_configured()
            else os.path.dirname(output_provider.get_repo_artifacts_dir())
        )

        analysis_ctx = AnalysisContext.from_config(
            repo_path=config["path_to_repo"],
            config={
                **config,
                # Surface the perf-specific worker limit so AnalysisContext
                # picks it up (defaults to PERF_DEFAULT_MAX_WORKERS).
                "code_analyzer_workers": config.get(
                    "max_concurrent_analyses", PERF_DEFAULT_MAX_WORKERS
                ),
            },
            output_base_dir=output_base,
            api_key=config["api_key"],
        )

        work_items = [self.analyzer.build_path_work(path) for path in paths]
        logger.info(
            f"Starting perf pipeline: {len(work_items)} paths, "
            f"{analysis_ctx.max_workers} workers"
        )

        async with AnalysisSession.create(analysis_ctx, analyzer_name="perf_analysis") as session:
            pipeline = PerfPipeline(session)
            return await pipeline.analyze_paths(work_items)

    # ------------------------------------------------------------------
    # Call graph + AST helpers (unchanged from legacy)
    # ------------------------------------------------------------------

    def _build_call_graph(self) -> Optional[CallGraph]:
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

    def _deduplicate_issues(self, issues: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if not issues:
            return []
        seen: Dict[str, Dict[str, Any]] = {}
        for issue in issues:
            if not isinstance(issue, dict):
                continue
            key = (
                f"{issue.get('file_path', '')}:{issue.get('function_name', '')}:"
                f"{issue.get('issueType', '')}:{issue.get('line_number', '')}"
            )
            if key not in seen:
                seen[key] = issue
            else:
                existing_desc = len(seen[key].get("description", ""))
                new_desc = len(issue.get("description", ""))
                if new_desc > existing_desc:
                    seen[key] = issue
        return list(seen.values())

    def _generate_report(self, issues: List[Dict[str, Any]], config: dict) -> None:
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
        try:
            config = load_and_validate_config(args.config)
            if args.repo:
                config["path_to_repo"] = str(Path(args.repo).resolve())

            api_key = get_api_key_from_config(config)
            if not api_key:
                logger.error("No API key configured")
                return None
            config["api_key"] = api_key

            if not config.get("path_to_repo"):
                logger.error("No repository path specified")
                return None
            if not os.path.isdir(config["path_to_repo"]):
                logger.error(f"Repository not found: {config['path_to_repo']}")
                return None

            config["llm_provider_type"] = get_llm_provider_type(config)

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
    runner = PerfAnalysisRunner()
    sys.exit(runner.run())


if __name__ == "__main__":
    main()
