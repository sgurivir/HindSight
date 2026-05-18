#!/usr/bin/env python3
"""
Performance Analysis Module

Orchestrates two-stage LLM analysis for performance optimization:
- Stage A: Context collection along a call path
- Stage B: Performance issue identification from context bundle
"""

import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .llm import Claude, ClaudeConfig
from .tools import Tools
from .perf_context_cache import PerfContextCache
from .iterative.perf_context_analyzer import PerfContextAnalyzer
from .iterative.perf_analysis_analyzer import PerfAnalysisAnalyzer
from ..constants import MAX_TOOL_ITERATIONS, DEFAULT_MAX_TOKENS
from ..prompts.prompt_builder import PromptBuilder
from ..ast_index import RepoAstIndex
from ...utils.directory_tree_util import DirectoryTreeUtil
from ...utils.log_util import get_logger
from ...utils.output_directory_provider import get_output_directory_provider

logger = get_logger(__name__)


@dataclass
class PerfAnalysisConfig:
    """Configuration for performance analysis."""
    api_key: str
    api_url: str
    model: str
    repo_path: str
    max_tokens: int = DEFAULT_MAX_TOKENS
    config: Dict[str, Any] = field(default_factory=dict)
    file_content_provider: Any = None
    llm_provider_type: str = "aws_bedrock"


class PerfAnalysis:
    """
    Performance analysis orchestrator.

    Runs two-stage analysis on a call path:
    1. Stage A — collect context for all functions along the path
    2. Stage B — analyze the context bundle for performance issues
    """

    def __init__(self, config: PerfAnalysisConfig, mcp_server=None):
        self.config = config
        self.ast_index = RepoAstIndex()
        self._load_ast()

        claude_config = ClaudeConfig(
            api_key=config.api_key,
            api_url=config.api_url,
            model=config.model,
            max_tokens=config.max_tokens,
            provider_type=config.llm_provider_type,
        )
        self.claude = Claude(claude_config)

        if mcp_server is not None:
            self.tools = mcp_server.tools
            self._mcp_server = mcp_server
        else:
            try:
                output_provider = get_output_directory_provider()
                output_base_dir = output_provider.get_custom_base_dir()
            except RuntimeError:
                output_base_dir = None

            ignore_dirs = set()
            if config.config.get("exclude_directories"):
                for dir_name in config.config["exclude_directories"]:
                    ignore_dirs.add(dir_name)
                    ignore_dirs.add(dir_name.upper())
                    ignore_dirs.add(dir_name.lower())

            output_provider = get_output_directory_provider()
            artifacts_dir = f"{output_provider.get_repo_artifacts_dir()}/code_insights"

            directory_tree_util = DirectoryTreeUtil()

            self.tools = Tools(
                config.repo_path,
                output_base_dir,
                config.file_content_provider,
                artifacts_dir,
                directory_tree_util,
                ignore_dirs,
            )
            self._mcp_server = None

        # Per-function context cache
        self.context_cache = PerfContextCache()

        self.total_input_tokens = 0
        self.total_output_tokens = 0

    def _load_ast(self) -> None:
        """Initialize AST index (lazy loading)."""
        try:
            self.ast_index.validate_ast_built()
        except RuntimeError as e:
            logger.warning(f"AST validation failed: {e}")

    def get_token_totals(self) -> tuple:
        """Get total input and output tokens used."""
        return self.total_input_tokens, self.total_output_tokens

    def _extract_and_log_token_usage(self, response: Dict[str, Any], iteration: int) -> None:
        """Extract token usage from API response."""
        try:
            usage = response.get("usage", {})
            input_tokens = usage.get("input_tokens", 0) or usage.get("prompt_tokens", 0)
            output_tokens = usage.get("output_tokens", 0) or usage.get("completion_tokens", 0)
            if input_tokens > 0 or output_tokens > 0:
                self.total_input_tokens += input_tokens
                self.total_output_tokens += output_tokens
                logger.info(f"Perf iteration {iteration} — in:{input_tokens:,} out:{output_tokens:,}")
        except Exception as e:
            logger.error(f"Error extracting token usage: {e}")

    async def analyze_path(self, path: List[str], mcp_server=None) -> Optional[List[Dict[str, Any]]]:
        """
        Analyze a single call path for performance issues.

        Args:
            path: List of function names forming the call path [root, ..., leaf]
            mcp_server: Optional MCP server override for this path

        Returns:
            List of performance issue dicts, or None on failure
        """
        path_id = "→".join(path[:3]) + ("→..." if len(path) > 3 else "")
        logger.info(f"Perf analysis starting for path: {path_id}")
        start_time = time.time()

        # Stage A: Context collection
        context_bundle = await self._run_context_collection(path)
        if context_bundle is None:
            logger.warning(f"Stage A failed for path: {path_id}")
            return None

        # Stage B: Performance analysis
        issues = self._run_performance_analysis(context_bundle)

        elapsed = time.time() - start_time
        count = len(issues) if issues else 0
        logger.info(f"Perf analysis complete for {path_id}: {count} issues in {elapsed:.1f}s")
        return issues

    async def _run_context_collection(self, path: List[str]) -> Optional[Dict[str, Any]]:
        """
        Stage A: Collect context for all functions along the call path.
        Uses per-function cache for previously collected functions.
        """
        logger.info(f"Stage A: Context collection for {len(path)} functions")

        # Partition functions into cached vs novel
        cached_contexts: Dict[str, Dict] = {}
        novel_functions: List[str] = []

        for func_name in path:
            checksum = self._get_function_checksum(func_name)
            cached = self.context_cache.get(func_name, checksum)
            if cached:
                cached_contexts[func_name] = cached
            else:
                novel_functions.append(func_name)

        logger.info(f"Stage A: {len(cached_contexts)} cached, {len(novel_functions)} novel functions")

        # Build function bodies from AST for prompt
        function_bodies = self._get_function_bodies(path)

        # Build the context collection prompt
        system_prompt, user_prompt = self._build_context_collection_prompts(
            path, function_bodies, cached_contexts, novel_functions
        )

        if not self.claude.check_token_limit(system_prompt, user_prompt):
            logger.error("Stage A: Input exceeds token limits")
            return None

        context_info = f"perf_context:{path[0]}→{path[-1]}"
        self.claude.start_conversation("perf_context_collection", context_info)

        available_tools = [
            "readFile", "runTerminalCmd", "getSummaryOfFile",
            "inspectDirectoryHierarchy", "list_files", "getFileContentByLines",
            "checkFileSize",
        ]

        analyzer = PerfContextAnalyzer(self.claude)
        raw_result = analyzer.run_iterative_analysis(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            tools_executor=self,
            supported_tools=available_tools,
            token_usage_callback=self._extract_and_log_token_usage,
        )

        if not raw_result:
            logger.error("Stage A: No result from LLM")
            return None

        try:
            context_bundle = json.loads(raw_result)
        except json.JSONDecodeError as e:
            logger.error(f"Stage A: Invalid JSON: {e}")
            return None

        if not isinstance(context_bundle, dict):
            logger.error(f"Stage A: Expected dict, got {type(context_bundle)}")
            return None

        # Cache newly collected per-function contexts
        functions_data = context_bundle.get("functions", {})
        for func_name, func_context in functions_data.items():
            if func_name in novel_functions and isinstance(func_context, dict):
                checksum = self._get_function_checksum(func_name)
                self.context_cache.put(func_name, checksum, func_context)

        return context_bundle

    def _run_performance_analysis(self, context_bundle: Dict[str, Any]) -> Optional[List[Dict[str, Any]]]:
        """
        Stage B: Analyze the context bundle for performance issues.
        """
        logger.info("Stage B: Performance analysis from context bundle")

        system_prompt, user_prompt = self._build_analysis_prompts(context_bundle)

        if not self.claude.check_token_limit(system_prompt, user_prompt):
            logger.error("Stage B: Input exceeds token limits")
            return None

        path_desc = "→".join(context_bundle.get("call_path", [])[:3])
        self.claude.start_conversation("perf_analysis", path_desc)

        # Stage B uses restricted tools
        available_tools = ["readFile", "runTerminalCmd", "getFileContentByLines"]

        analyzer = PerfAnalysisAnalyzer(self.claude)
        raw_result = analyzer.run_iterative_analysis(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            tools_executor=self,
            supported_tools=available_tools,
            token_usage_callback=self._extract_and_log_token_usage,
        )

        if not raw_result:
            logger.error("Stage B: No result from LLM")
            return None

        try:
            issues = json.loads(raw_result)
        except json.JSONDecodeError as e:
            logger.error(f"Stage B: Invalid JSON: {e}")
            return None

        if not isinstance(issues, list):
            logger.error(f"Stage B: Expected list, got {type(issues)}")
            return None

        # Annotate each issue with the call path
        call_path = context_bundle.get("call_path", [])
        for issue in issues:
            if isinstance(issue, dict):
                issue.setdefault("category", "performance")
                issue.setdefault("call_path", call_path)

        return issues

    def _get_function_checksum(self, func_name: str) -> str:
        """Compute a content-based checksum for a function."""
        func_data = None
        if self.ast_index.merged_functions:
            func_data = self.ast_index.merged_functions.get(func_name)

        if func_data and isinstance(func_data, dict):
            file_path = func_data.get("file", "") or func_data.get("file_path", "")
            start = func_data.get("start_line", 0)
            end = func_data.get("end_line", 0)
            key = f"{file_path}:{start}-{end}:{func_name}"
        else:
            key = func_name

        return hashlib.md5(key.encode()).hexdigest()[:16]

    def _get_function_bodies(self, path: List[str]) -> Dict[str, Dict[str, Any]]:
        """Extract function bodies from the AST index for all functions in the path."""
        bodies = {}
        merged = self.ast_index.merged_functions
        if not merged:
            return bodies

        for func_name in path:
            func_data = merged.get(func_name)
            if func_data and isinstance(func_data, dict):
                bodies[func_name] = {
                    "file": func_data.get("file", "") or func_data.get("file_path", ""),
                    "start_line": func_data.get("start_line", 0),
                    "end_line": func_data.get("end_line", 0),
                    "body": func_data.get("body", "") or func_data.get("source", ""),
                }
        return bodies

    def _build_context_collection_prompts(
        self,
        path: List[str],
        function_bodies: Dict[str, Dict],
        cached_contexts: Dict[str, Dict],
        novel_functions: List[str],
    ) -> tuple:
        """Build system and user prompts for Stage A."""
        system_prompt = self._load_prompt_file("perfContextCollectionProcess.md")

        # Build user prompt with path info and function bodies
        parts = [
            f"## Call Path\n\n{' → '.join(path)}\n",
            f"## Function Bodies\n",
        ]

        for func_name in path:
            body_info = function_bodies.get(func_name, {})
            body = body_info.get("body", "(body not available in AST)")
            file_path = body_info.get("file", "unknown")
            start = body_info.get("start_line", "?")
            parts.append(f"\n### {func_name}\nFile: {file_path} (line {start})\n```\n{body}\n```\n")

        if cached_contexts:
            parts.append("\n## Pre-Collected Context (already gathered — do not re-collect)\n")
            for func_name, ctx in cached_contexts.items():
                parts.append(f"\n### {func_name} (cached)\n```json\n{json.dumps(ctx, indent=2)[:2000]}\n```\n")

        if novel_functions:
            parts.append(f"\n## Functions Requiring Context Collection\n\n")
            parts.append(", ".join(novel_functions))
            parts.append("\n\nFocus your tool usage on gathering context for these functions.\n")

        user_prompt = "\n".join(parts)
        return system_prompt, user_prompt

    def _build_analysis_prompts(self, context_bundle: Dict[str, Any]) -> tuple:
        """Build system and user prompts for Stage B."""
        system_prompt = self._load_prompt_file("perfAnalysisProcess.md")
        user_prompt = f"## Context Bundle\n\n```json\n{json.dumps(context_bundle, indent=2, ensure_ascii=False)}\n```"
        return system_prompt, user_prompt

    def _load_prompt_file(self, filename: str) -> str:
        """Load a prompt markdown file from the prompts directory."""
        prompts_dir = os.path.join(os.path.dirname(__file__), "..", "prompts")
        filepath = os.path.join(prompts_dir, filename)
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                return f.read()
        except FileNotFoundError:
            logger.error(f"Prompt file not found: {filepath}")
            return f"You are a performance engineer analyzing code for optimization opportunities."
