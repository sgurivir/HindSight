#!/usr/bin/env python3
# Created by Sridhar Gurivireddy
"""
Trace Code Analysis Module — Two-Stage Pipeline

Stage A: Context collection — gather source code for all callstack functions
Stage B: Performance analysis — analyze the context bundle in a fresh context window
"""

import os
import json
import pkgutil
import time
from dataclasses import dataclass
from typing import Optional, Dict, Any, List, Tuple

from ..constants import MAX_TOOL_ITERATIONS, DEFAULT_MAX_TOKENS, ModelLimits
from ..llm.llm import Claude, ClaudeConfig
from ..llm.tools import Tools
from ..llm.iterative.trace_context_analyzer import TraceContextAnalyzer
from ..llm.iterative.trace_analysis_analyzer import TraceAnalysisAnalyzer
from ..llm.iterative.trace_solution_validator_analyzer import TraceSolutionValidatorAnalyzer
from ..knowledge.trace_knowledge_store import TraceKnowledgeStore
from .trace_prompt_builder import TracePromptBuilder
from .trace_result_repository import TraceAnalysisResultRepository, TraceAnalysisResult
from .file_name_extractor_from_trace import FileNameExtractorFromTrace

from ...utils.directory_tree_util import DirectoryTreeUtil
from ...utils.file_util import read_file
from ...utils.json_util import validate_and_format_json, clean_json_response
from ...utils.log_util import get_logger
from ...utils.output_directory_provider import get_output_directory_provider

logger = get_logger(__name__)


def _read_prompt_file(filename: str) -> str:
    """Read a prompt file from the hindsight.core.prompts package."""
    try:
        data = pkgutil.get_data('hindsight.core.prompts', filename)
        if data is not None:
            return data.decode('utf-8')
    except Exception as e:
        logger.warning(f"Could not read prompt file {filename}: {e}")
    return ""


KNOWLEDGE_TOOL_NAMES = {"lookup_knowledge", "store_learning", "lookup_function_optimization", "store_function_optimization"}


class _ToolsWithKnowledge:
    """Wraps an underlying tools object and intercepts knowledge store tool calls."""

    def __init__(self, inner_tools, knowledge_store: TraceKnowledgeStore, repo_name: str = ""):
        self._inner = inner_tools
        self._knowledge_store = knowledge_store
        self._repo_name = repo_name

    def execute_tool_use(self, tool_use_block: Dict[str, Any]) -> str:
        tool_name = tool_use_block.get("name", "unknown")
        if tool_name in KNOWLEDGE_TOOL_NAMES:
            params = dict(tool_use_block.get("input", {}))
            params.setdefault("repo_name", self._repo_name)
            return self._knowledge_store.execute_tool(tool_name, params)
        return self._inner.execute_tool_use(tool_use_block)

    def log_tool_usage_summary(self) -> None:
        self._inner.log_tool_usage_summary()


@dataclass
class TraceAnalysisConfig:
    """Configuration for trace analysis"""
    prompt_file_path: str
    api_key: str
    api_url: str
    model: str
    repo_path: str
    output_file: str
    max_tokens: int = DEFAULT_MAX_TOKENS
    config: Dict[str, Any] = None


class TraceCodeAnalysis:
    """
    Two-stage trace analysis orchestrator.

    Stage A: Context collection — gathers source code for callstack functions
    Stage B: Performance analysis — identifies issues from the context bundle
    """

    def __init__(self, config: TraceAnalysisConfig, mcp_server=None):
        """
        Initialize TraceCodeAnalysis with configuration.

        Args:
            config: Trace analysis configuration
            mcp_server: Optional AnalysisMCPServer instance for tool dispatch
        """
        self.config = config

        # Use model-aware context window limits
        context_window = ModelLimits.get_context_window(config.model)
        max_output = ModelLimits.get_max_output_tokens(config.model)
        effective_max_tokens = min(config.max_tokens, max_output)

        claude_config = ClaudeConfig(
            api_key=config.api_key,
            api_url=config.api_url,
            model=config.model,
            max_tokens=effective_max_tokens,
            provider_type=config.config.get('llm_provider_type', 'aws_bedrock') if config.config else 'aws_bedrock'
        )
        self.claude = Claude(claude_config)

        if mcp_server is not None:
            self.tools = mcp_server
            self._mcp_server = mcp_server
        else:
            try:
                output_provider = get_output_directory_provider()
                output_base_dir = output_provider.get_custom_base_dir()
            except RuntimeError:
                output_base_dir = None

            ignore_dirs = set()
            if config.config and config.config.get('exclude_directories'):
                for dir_name in config.config.get('exclude_directories', []):
                    ignore_dirs.add(dir_name)
                    ignore_dirs.add(dir_name.upper())
                    ignore_dirs.add(dir_name.lower())

            file_content_provider = None
            try:
                trace_result_repository = TraceAnalysisResultRepository.get_instance()
                if hasattr(trace_result_repository, 'file_content_provider'):
                    file_content_provider = trace_result_repository.file_content_provider
            except Exception:
                pass

            output_provider = get_output_directory_provider()
            artifacts_dir = f"{output_provider.get_repo_artifacts_dir()}/code_insights"

            directory_tree_util = None
            try:
                from ...analyzers.analysis_runner import AnalysisRunner
                directory_tree_util = AnalysisRunner().directory_tree_util
            except Exception as e:
                logger.warning(f"Could not get DirectoryTreeUtil from AnalysisRunner: {e}")
                try:
                    directory_tree_util = DirectoryTreeUtil()
                except Exception as e2:
                    logger.error(f"Could not create DirectoryTreeUtil instance: {e2}")

            self.tools = Tools(config.repo_path, output_base_dir, file_content_provider, artifacts_dir, directory_tree_util, ignore_dirs)
            self._mcp_server = None

        # Knowledge store for persistent learnings
        self._knowledge_store = TraceKnowledgeStore()

        # Wrap tools to intercept knowledge store calls
        repo_name = os.path.basename(config.repo_path.rstrip('/')) if config.repo_path else ""
        self.tools = _ToolsWithKnowledge(self.tools, self._knowledge_store, repo_name)

        # Token tracking
        self.total_input_tokens = 0
        self.total_output_tokens = 0

        logger.info(f"Initialized TraceCodeAnalysis (model={config.model}, context_window={context_window:,})")

    def _get_supported_tools_stage_a(self) -> List[str]:
        """Full tool set for context collection (Stage A)."""
        tools = [
            "readFile", "runTerminalCmd", "getSummaryOfFile",
            "inspectDirectoryHierarchy", "list_files",
            "getFileContentByLines", "getFileContent", "checkFileSize",
            "lookup_knowledge", "lookup_function_optimization",
        ]
        if self._mcp_server is not None and self._mcp_server._code_nav_server is not None:
            tools.extend([
                "search_symbol", "get_symbol", "get_function_body",
                "get_file_ast", "get_callers", "get_callees", "find_references"
            ])
        return tools

    def _get_supported_tools_stage_b(self) -> List[str]:
        """Reduced tool set for analysis (Stage B)."""
        return [
            "readFile", "runTerminalCmd", "getFileContentByLines",
            "lookup_knowledge", "store_learning",
            "lookup_function_optimization", "store_function_optimization",
        ]

    def _extract_and_log_token_usage(self, response: Dict[str, Any], iteration: int) -> None:
        """Extract token usage from API response and log it."""
        try:
            usage = response.get("usage", {})
            input_tokens = usage.get("input_tokens", 0) or usage.get("prompt_tokens", 0)
            output_tokens = usage.get("output_tokens", 0) or usage.get("completion_tokens", 0)

            if input_tokens > 0 or output_tokens > 0:
                logger.info(f"Iteration {iteration} - Input: {input_tokens:,}, Output: {output_tokens:,}")
                self.total_input_tokens += input_tokens
                self.total_output_tokens += output_tokens
                logger.info(f"Running totals - Input: {self.total_input_tokens:,}, Output: {self.total_output_tokens:,}")
            else:
                logger.warning(f"Iteration {iteration} - No token usage in response")
        except Exception as e:
            logger.error(f"Error extracting token usage: {e}")

    def _log_final_token_summary(self) -> None:
        """Log final token usage summary."""
        total = self.total_input_tokens + self.total_output_tokens
        logger.info(f"Token consumed: Input: {self.total_input_tokens:,}, Output: {self.total_output_tokens:,}, Total: {total:,}")

    def get_token_totals(self) -> tuple:
        """Get total input and output tokens."""
        return self.total_input_tokens, self.total_output_tokens

    # ─────────────────────────────────────────────────────────────────────────
    # Stage A: Context Collection
    # ─────────────────────────────────────────────────────────────────────────

    def run_context_collection(self, prompt_content: str, extracted_file_paths: List[str] = None) -> Optional[Dict[str, Any]]:
        """
        Stage A: Collect code context for the callstack trace.

        Args:
            prompt_content: The trace prompt content (callstack + code context)
            extracted_file_paths: File paths extracted from the trace

        Returns:
            Context bundle dict on success, None on failure
        """
        logger.info("Stage A: Starting context collection...")
        start_time = time.time()

        context_info = os.path.basename(self.config.prompt_file_path)
        self.claude.start_conversation("trace_context_collection", context_info)

        try:
            # Build system prompt from template
            system_prompt = _read_prompt_file("traceContextCollectionProcess.md")
            if not system_prompt:
                logger.error("Stage A: Failed to load traceContextCollectionProcess.md")
                return None

            # Build user prompt with the trace content
            user_parts = [
                "## Callstack Trace\n\n",
                "Collect context for the following callstack trace:\n\n",
                "======\n",
                prompt_content,
                "\n======\n",
            ]

            if extracted_file_paths:
                valid_paths = [p for p in extracted_file_paths if p and p.strip()]
                if valid_paths:
                    user_parts.append("\n## Known File Paths\n\n")
                    user_parts.append("These files have been identified in the trace:\n\n")
                    for path in valid_paths:
                        user_parts.append(f"- {path}\n")
                    user_parts.append("\n")

            user_prompt = "".join(user_parts)

            # Token limit check
            if not self.claude.check_token_limit(system_prompt, user_prompt):
                logger.error("Stage A: Input exceeds token limits - aborting")
                return None

            # Run iterative context collection
            supported_tools = self._get_supported_tools_stage_a()

            analyzer = TraceContextAnalyzer(self.claude)
            raw_result = analyzer.run_iterative_analysis(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                tools_executor=self,
                supported_tools=supported_tools,
                context_guidance_template="""
Based on the tool results above, continue gathering context for the callstack. Remember:
1. Collect source code for ALL functions in the call path
2. Prioritize the leaf function (bottom of stack) — it needs full source
3. Preserve original source-file line numbers in ALL code snippets
4. Your output MUST be a valid JSON object starting with {{ and ending with }}

{user_prompt}
""",
                token_usage_callback=self._extract_and_log_token_usage
            )

            elapsed = time.time() - start_time
            logger.info(f"Stage A: Completed in {elapsed:.2f}s")

            if not raw_result:
                logger.error("Stage A: No result from LLM")
                return None

            # Parse context bundle
            try:
                context_bundle = json.loads(raw_result)
            except json.JSONDecodeError as e:
                logger.error(f"Stage A: Invalid JSON: {e}")
                return None

            if isinstance(context_bundle, list):
                candidate = next((item for item in context_bundle if isinstance(item, dict) and 'call_path' in item), None)
                if candidate:
                    context_bundle = candidate
                else:
                    logger.error("Stage A: Got list without valid context bundle")
                    return None

            if not isinstance(context_bundle, dict):
                logger.error(f"Stage A: Expected dict, got {type(context_bundle)}")
                return None

            self.claude.log_complete_conversation(final_result=json.dumps(context_bundle)[:5000])
            logger.info(f"Stage A: Successful — collected {len(context_bundle.get('functions', {}))} functions")
            return context_bundle

        except Exception as e:
            logger.error(f"Stage A: Unexpected error: {e}")
            self.claude.log_complete_conversation(final_result=f"Error: {e}")
            return None

    # ─────────────────────────────────────────────────────────────────────────
    # Stage B: Performance Analysis (fresh context window)
    # ─────────────────────────────────────────────────────────────────────────

    def run_analysis_from_context(self, context_bundle: Dict[str, Any]) -> Optional[List[Dict[str, Any]]]:
        """
        Stage B: Analyze the context bundle for performance issues.
        Runs in a fresh context window — no carry-over from Stage A.

        Args:
            context_bundle: Context bundle from Stage A

        Returns:
            List of issue dicts on success, None on failure
        """
        logger.info("Stage B: Starting performance analysis from context bundle...")
        start_time = time.time()

        call_path = context_bundle.get("call_path", [])
        path_desc = "→".join(call_path[:3]) + ("→..." if len(call_path) > 3 else "")
        self.claude.start_conversation("trace_analysis", path_desc)

        try:
            # Build system prompt from template
            system_prompt = _read_prompt_file("traceAnalysisProcess.md")
            if not system_prompt:
                logger.error("Stage B: Failed to load traceAnalysisProcess.md")
                return None

            # Build user prompt with context bundle
            user_prompt = (
                "## Context Bundle\n\n"
                "Analyze the following pre-collected context for performance issues:\n\n"
                f"```json\n{json.dumps(context_bundle, indent=2, ensure_ascii=False)}\n```\n"
            )

            # Token limit check
            if not self.claude.check_token_limit(system_prompt, user_prompt):
                logger.error("Stage B: Input exceeds token limits - aborting")
                return None

            # Run analysis with reduced tool set
            supported_tools = self._get_supported_tools_stage_b()

            analyzer = TraceAnalysisAnalyzer(self.claude)
            raw_result = analyzer.run_iterative_analysis(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                tools_executor=self,
                supported_tools=supported_tools,
                context_guidance_template="""
Based on the tool results above, complete your performance analysis. Remember:
1. Your response MUST be ONLY a valid JSON array starting with [ and ending with ]
2. Focus on performance bottlenecks in the callstack execution path
3. If no issues found, return exactly: []

{user_prompt}
""",
                token_usage_callback=self._extract_and_log_token_usage
            )

            elapsed = time.time() - start_time
            logger.info(f"Stage B: Completed in {elapsed:.2f}s")

            if not raw_result:
                logger.error("Stage B: No result from LLM")
                return None

            # Parse issues
            is_valid, processed = validate_and_format_json(raw_result)
            try:
                issues = json.loads(processed if is_valid else raw_result)
            except json.JSONDecodeError as e:
                logger.error(f"Stage B: Failed to parse JSON: {e}")
                return None

            if not isinstance(issues, list):
                if isinstance(issues, dict) and 'results' in issues:
                    issues = issues['results']
                elif isinstance(issues, dict):
                    issues = [issues]
                else:
                    issues = []

            valid_issues = [i for i in issues if isinstance(i, dict)]
            if len(valid_issues) != len(issues):
                logger.warning(f"Stage B: Filtered out {len(issues) - len(valid_issues)} invalid items")

            self.claude.log_complete_conversation(final_result=json.dumps(valid_issues)[:5000])
            self.tools.log_tool_usage_summary()
            logger.info(f"Stage B: Found {len(valid_issues)} issues")
            return valid_issues

        except Exception as e:
            logger.error(f"Stage B: Unexpected error: {e}")
            self.claude.log_complete_conversation(final_result=f"Error: {e}")
            return None

    # ─────────────────────────────────────────────────────────────────────────
    # Orchestrator: run_analysis() calls both stages
    # ─────────────────────────────────────────────────────────────────────────

    def run_analysis(self) -> bool:
        """
        Run the complete two-stage trace analysis pipeline.

        Stage A: Context collection (with full tool access)
        Stage B: Performance analysis (fresh context, reduced tools)

        Returns:
            bool: True if analysis completed successfully
        """
        logger.info("Starting two-stage trace analysis...")
        start_time = time.time()

        try:
            # Load prompt content
            prompt_content = read_file(self.config.prompt_file_path)
            if not prompt_content:
                logger.error(f"Failed to read prompt file: {self.config.prompt_file_path}")
                return False

            logger.info(f"Loaded prompt: {self.config.prompt_file_path} ({len(prompt_content)} chars)")

            # Extract file paths from trace
            extracted_file_paths = []
            try:
                file_name_extractor = FileNameExtractorFromTrace(
                    config=self.config.config,
                    repo_path=self.config.repo_path
                )
                extracted_file_paths = file_name_extractor.get_all_file_paths(prompt_content)
                if extracted_file_paths:
                    logger.info(f"Extracted {len(extracted_file_paths)} file paths from trace")
            except Exception as e:
                logger.warning(f"Failed to extract file names from trace: {e}")

            # Stage A: Context collection
            context_bundle = self.run_context_collection(prompt_content, extracted_file_paths)
            if context_bundle is None:
                logger.warning("Stage A failed — falling back to single-stage analysis")
                return self._run_single_stage_fallback(prompt_content, extracted_file_paths)

            # Stage B: Performance analysis (fresh context window)
            issues = self.run_analysis_from_context(context_bundle)
            if issues is None:
                logger.error("Stage B failed — no issues returned")
                self._log_final_token_summary()
                return False

            # Stage C: Solution validation — filter issues with unsafe/incorrect solutions
            if issues:
                issues = self._validate_solutions(issues, context_bundle)

            # Save results
            result_json = json.dumps(issues, indent=2, ensure_ascii=False)
            save_success = self._save_result(result_json)

            elapsed = time.time() - start_time
            if save_success:
                logger.info(f"Two-stage trace analysis completed: {len(issues)} issues in {elapsed:.2f}s")
                self._log_final_token_summary()
                return True
            else:
                logger.error("Failed to save results")
                return False

        except Exception as e:
            logger.error(f"Unexpected error during trace analysis: {e}")
            self._log_final_token_summary()
            return False

    def _run_single_stage_fallback(self, prompt_content: str, extracted_file_paths: List[str]) -> bool:
        """
        Fallback: single-stage analysis when Stage A fails.
        Uses the original single-pass approach.
        """
        logger.info("Running single-stage fallback analysis...")

        context_info = os.path.basename(self.config.prompt_file_path)
        self.claude.start_conversation("trace_analysis_fallback", context_info)

        system_prompt, user_prompt = TracePromptBuilder.build_complete_prompt(prompt_content, extracted_file_paths)

        if not self.claude.check_token_limit(system_prompt, user_prompt):
            logger.error("Fallback: Input exceeds token limits")
            self._log_final_token_summary()
            return False

        supported_tools = self._get_supported_tools_stage_a()

        from ..llm.iterative.trace_analysis_analyzer import TraceAnalysisAnalyzer
        analyzer = TraceAnalysisAnalyzer(self.claude)
        analysis_result = analyzer.run_iterative_analysis(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            tools_executor=self,
            supported_tools=supported_tools,
            context_guidance_template="""
Based on the tool results above, continue your trace analysis. Remember:
1. Identify performance bottlenecks in the callstack
2. Provide specific, actionable recommendations
3. Your response MUST be a valid JSON array starting with [ and ending with ]

{user_prompt}
""",
            token_usage_callback=self._extract_and_log_token_usage
        )

        if not analysis_result:
            logger.error("Fallback: No result from LLM")
            self._log_final_token_summary()
            return False

        success, processed_result, _ = self._process_analysis_result(analysis_result)
        if success:
            save_success = self._save_result(processed_result)
            self.tools.log_tool_usage_summary()
            self._log_final_token_summary()
            return save_success
        else:
            logger.error("Fallback: Failed to process results")
            self._log_final_token_summary()
            return False

    # ─────────────────────────────────────────────────────────────────────────
    # Helpers
    # ─────────────────────────────────────────────────────────────────────────

    def _validate_solutions(self, issues: List[Dict[str, Any]], context_bundle: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Stage C: Validate each issue's proposed solution for correctness.
        Drops issues only when the validator returns valid=false with confidence.
        Low-confidence rejections are kept and annotated with a `validation` field.
        """
        if not issues:
            return issues

        logger.info(f"Stage C: Validating {len(issues)} issue solution(s)...")
        start_time = time.time()

        system_prompt = _read_prompt_file("traceSolutionValidator.md")
        if not system_prompt:
            logger.warning("Stage C: Failed to load traceSolutionValidator.md — skipping validation")
            return issues

        validated: List[Dict[str, Any]] = []
        dropped = 0
        low_conf = 0
        for i, issue in enumerate(issues):
            kept_issue = self._validate_single_solution(issue, context_bundle, system_prompt, i + 1)
            if kept_issue is None:
                dropped += 1
                continue
            if isinstance(kept_issue.get("validation"), dict) and kept_issue["validation"].get("low_confidence"):
                low_conf += 1
            validated.append(kept_issue)

        elapsed = time.time() - start_time
        logger.info(
            f"Stage C: {len(validated)}/{len(issues)} kept "
            f"(dropped={dropped}, low_confidence={low_conf}) in {elapsed:.2f}s"
        )
        return validated

    def _validate_single_solution(self, issue: Dict[str, Any], context_bundle: Dict[str, Any],
                                  system_prompt: str, issue_index: int) -> Optional[Dict[str, Any]]:
        """
        Validate a single issue's solution against the full context bundle,
        using iterative tool-enabled analysis.

        Returns:
            The issue dict (possibly annotated with a `validation` field) if kept,
            or None if the validator confidently rejected it.
        """
        function_name = issue.get("functionName", "unknown")

        try:
            user_prompt = (
                "## Issue to Validate\n\n"
                f"```json\n{json.dumps(issue, indent=2, ensure_ascii=False)}\n```\n\n"
                "## Full Context Bundle\n\n"
                "The following is the same pre-collected context the analyzer used. "
                "It contains the call path and every function the analyzer inspected. "
                "If something you need is missing, use the tools described in the system prompt.\n\n"
                f"```json\n{json.dumps(context_bundle, indent=2, ensure_ascii=False)}\n```\n"
            )

            if not self.claude.check_token_limit(system_prompt, user_prompt):
                logger.warning(
                    f"Stage C issue #{issue_index} ({function_name}): "
                    "Input exceeds token limits — keeping issue with low_confidence"
                )
                issue = dict(issue)
                issue["validation"] = {
                    "low_confidence": True,
                    "reason": "Validator input exceeded token limits; bundle not sent.",
                }
                return issue

            context_info = f"validate_{function_name}"
            self.claude.start_conversation("trace_solution_validation", context_info)

            supported_tools = self._get_supported_tools_stage_b()

            analyzer = TraceSolutionValidatorAnalyzer(self.claude)
            raw_result = analyzer.run_iterative_analysis(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                tools_executor=self,
                supported_tools=supported_tools,
                context_guidance_template=(
                    "Based on the tool results above, finish validating the proposed solution. "
                    "Respond ONLY with a JSON object containing 'valid' (bool), optionally "
                    "'low_confidence' (bool), and 'reason' (string). Do not request more tools "
                    "unless you truly need them.\n\n{user_prompt}"
                ),
                token_usage_callback=self._extract_and_log_token_usage,
            )

            if not raw_result:
                logger.warning(
                    f"Stage C issue #{issue_index} ({function_name}): "
                    "No validator output — keeping issue with low_confidence"
                )
                issue = dict(issue)
                issue["validation"] = {
                    "low_confidence": True,
                    "reason": "Validator returned no parseable output.",
                }
                return issue

            try:
                cleaned = clean_json_response(raw_result)
                result = json.loads(cleaned)
            except json.JSONDecodeError as e:
                logger.warning(
                    f"Stage C issue #{issue_index} ({function_name}): "
                    f"Failed to parse verdict — keeping issue with low_confidence: {e}"
                )
                issue = dict(issue)
                issue["validation"] = {
                    "low_confidence": True,
                    "reason": f"Validator produced unparseable output: {e}",
                }
                return issue

            is_valid = result.get("valid", True)
            low_confidence = bool(result.get("low_confidence", False))
            reason = result.get("reason", "")

            if not is_valid and not low_confidence:
                logger.info(
                    f"Stage C issue #{issue_index} ({function_name}): REJECTED — {reason[:200]}"
                )
                return None

            if low_confidence:
                logger.info(
                    f"Stage C issue #{issue_index} ({function_name}): "
                    f"LOW_CONFIDENCE (valid={is_valid}) — keeping: {reason[:200]}"
                )
                issue = dict(issue)
                issue["validation"] = {
                    "low_confidence": True,
                    "valid": is_valid,
                    "reason": reason,
                }
                return issue

            logger.debug(f"Stage C issue #{issue_index} ({function_name}): validated")
            return issue

        except Exception as e:
            logger.warning(
                f"Stage C issue #{issue_index} ({function_name}): "
                f"Validation error — keeping issue with low_confidence: {e}"
            )
            issue = dict(issue)
            issue["validation"] = {
                "low_confidence": True,
                "reason": f"Validator raised exception: {e}",
            }
            return issue

    def _process_analysis_result(self, result: str) -> Tuple[bool, str, bool]:
        """Process and clean the analysis result."""
        try:
            cleaned_result = clean_json_response(result)
            is_valid, final_output = validate_and_format_json(cleaned_result)
            if is_valid:
                logger.info("Result is valid JSON")
            else:
                logger.warning("Result is not valid JSON after cleanup - saving as-is")
                final_output = cleaned_result
            return True, final_output, False
        except Exception as e:
            logger.error(f"Error processing result: {e}")
            return False, result, False

    def _save_result(self, result: str) -> bool:
        """Save the analysis result to output file."""
        try:
            trace_result_repository = TraceAnalysisResultRepository.get_instance()
            return trace_result_repository.save_trace_result(
                output_file=self.config.output_file,
                results_data=result,
                metadata={
                    'prompt_file_path': self.config.prompt_file_path,
                    'repo_path': self.config.repo_path,
                    'timestamp': time.time(),
                    'analysis_type': 'two_stage_trace'
                }
            )
        except Exception as e:
            logger.error(f"Error saving result: {e}")
            return TraceAnalysisResult.save_result(
                result=result,
                output_file=self.config.output_file,
                prompt_file_path=self.config.prompt_file_path,
                repo_path=self.config.repo_path
            )
