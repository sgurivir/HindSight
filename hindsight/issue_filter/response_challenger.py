#!/usr/bin/env python3
"""
LLM Response Challenger (Level 3 Filtering)

This module provides the Level-3 filter that challenges analysis results
by having an LLM act as a senior software engineer to verify if issues
are legitimate bugs/optimizations worth pursuing.
"""

import os
import sys
import json
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional
from ..core.constants import DEFAULT_LLM_API_END_POINT, RESPONSE_CHALLENGER_MAX_ITERATIONS, DEFAULT_LLM_MODEL, DEFAULT_MAX_TOKENS
from ..utils.log_util import get_logger
from ..utils.output_directory_provider import get_output_directory_provider

_PROMPTS_DIR = Path(__file__).parent.parent / "core" / "prompts"


def _load_prompt_template(filename: str) -> Optional[str]:
    try:
        return (_PROMPTS_DIR / filename).read_text(encoding='utf-8')
    except Exception:
        return None


# Add the project root to Python path for imports
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))


class LLMResponseChallenger:
    """
    Level 3 filter that uses LLM analysis to challenge and verify issues.
    This filter operates on issues that have already passed Level 1 and Level 2 filtering.
    It acts as a senior software engineer reviewing the code to determine if issues are worth pursuing.
    """
    
    def __init__(self, api_key: str, config: dict, dropped_issues_dir: Optional[str] = None, capture_evidence: bool = False, file_content_provider=None, directory_tree_util=None, repo_path: Optional[str] = None, artifacts_dir: Optional[str] = None):
        """
        Initialize the LLM Response Challenger.

        Args:
            api_key: API key for LLM provider
            config: Configuration dictionary (same format as used by analyzers)
            dropped_issues_dir: Directory to save dropped issues (optional)
            capture_evidence: Whether to attach the challenger's free-text reasoning to each kept issue's `evidence` field for HTML rendering. Default False — the reasoning is already persisted to artifacts under response_challenger/ for audit, and surfacing it in the report duplicates the analyzer's `description` text.
            file_content_provider: FileContentProvider instance for file resolution (optional)
            directory_tree_util: DirectoryTreeUtil instance for directory listing (optional)
            repo_path: Path to the repository (optional, for runTerminalCmd tool)
            artifacts_dir: Path to artifacts directory (optional, for code insights)
        """
        self.logger = get_logger(__name__)
        self.api_key = api_key
        self.config = config
        self.dropped_issues_dir = dropped_issues_dir
        self.capture_evidence = capture_evidence
        self.file_content_provider = file_content_provider
        self.directory_tree_util = directory_tree_util
        self.repo_path = repo_path
        self.artifacts_dir = artifacts_dir
        self.trace_context = None  # Store trace context for dropped issues
        
        # Set up dropped issues directory
        self._setup_dropped_issues_directory()
        
        # Enable challenger filtering if we have any API key (including dummy for testing)
        if api_key:
            self.challenger_available = True
            if api_key == "dummy-key":
                self.logger.info("LLMResponseChallenger initialized - Level 3 filtering enabled (dummy mode)")
            else:
                self.logger.info("LLMResponseChallenger initialized - Level 3 filtering enabled")
            
            # Log evidence capture status
            if self.capture_evidence:
                self.logger.info("Evidence capture enabled - validation reasoning will be attached to issues")
            else:
                self.logger.info("Evidence capture disabled - validation reasoning will not be attached")
        else:
            self.challenger_available = False
            self.logger.info("LLMResponseChallenger initialized - Level 3 filtering disabled (no API key)")
    
    def _setup_dropped_issues_directory(self) -> None:
        """Setup the dropped_issues directory under output folder."""
        if self.dropped_issues_dir:
            # Use provided directory
            try:
                os.makedirs(self.dropped_issues_dir, exist_ok=True)
                self.logger.info(f"Level 3 dropped issues directory created: {self.dropped_issues_dir}")
            except Exception as e:
                self.logger.error(f"Failed to create Level 3 dropped issues directory: {e}")
                self.dropped_issues_dir = None
        else:
            # Auto-create directory using output provider
            try:
                output_provider = get_output_directory_provider()
                output_base_dir = output_provider.get_repo_artifacts_dir()
                self.dropped_issues_dir = os.path.join(output_base_dir, "dropped_issues", "level3_response_challenger")
                os.makedirs(self.dropped_issues_dir, exist_ok=True)
                self.logger.info(f"Level 3 dropped issues directory created: {self.dropped_issues_dir}")
            except Exception as e:
                self.logger.error(f"Failed to create Level 3 dropped issues directory: {e}")
                self.dropped_issues_dir = None
    
    def _save_dropped_issue(self, issue: Dict[str, Any], reason: str) -> None:
        """
        Save a dropped issue to a JSON file in the dropped_issues directory.
        For trace analysis, maintains trace context (trace_id, callstack, repo_name, callstack_data).
        
        Args:
            issue: The original issue that was dropped
            reason: The reason why the issue was dropped
        """
        if not self.dropped_issues_dir:
            self.logger.warning("Dropped issues directory not available, cannot save dropped issue")
            return
            
        try:
            # Create a unique filename based on timestamp and issue content
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
            
            # Create a safe filename from issue content
            issue_text = issue.get('issue', 'unknown_issue')[:50]
            safe_issue_text = "".join(c for c in issue_text if c.isalnum() or c in ('_', '-', ' ')).replace(' ', '_')
            
            # Use trace_id in filename if available
            if self.trace_context and 'trace_id' in self.trace_context:
                trace_id = self.trace_context['trace_id']
                filename = f"{trace_id}_level3_dropped_{timestamp}_{safe_issue_text}.json"
            else:
                filename = f"level3_dropped_issue_{timestamp}_{safe_issue_text}.json"
            
            filepath = os.path.join(self.dropped_issues_dir, filename)
            
            # Create the dropped issue record with trace context if available
            dropped_record = {
                "timestamp": datetime.now().isoformat(),
                "filter_level": "Level 3 - Response Challenger",
                "filter_type": "LLM Response Challenger",
                "reason": reason
            }
            
            # Add trace context if available (for trace analysis)
            if self.trace_context:
                dropped_record["trace_id"] = self.trace_context.get('trace_id')
                dropped_record["callstack"] = self.trace_context.get('callstack', [])
                dropped_record["repo_name"] = self.trace_context.get('repo_name')
                if 'callstack_data' in self.trace_context:
                    dropped_record["callstack_data"] = self.trace_context['callstack_data']
                # Store dropped issue in results array to match trace analysis format
                dropped_record["results"] = [issue]
            else:
                # For non-trace analysis (code analysis), use original format
                dropped_record["original_issue"] = issue
            
            # Save to file
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(dropped_record, f, indent=2, ensure_ascii=False)
                
            self.logger.debug(f"Level 3 dropped issue saved to: {filepath}")
            
        except Exception as e:
            self.logger.error(f"Failed to save Level 3 dropped issue: {e}")
    
    def set_trace_context(self, trace_context: Optional[Dict[str, Any]]) -> None:
        """
        Set trace context for dropped issues logging.
        
        Args:
            trace_context: Dictionary containing trace_id, callstack, repo_name, and callstack_data
        """
        self.trace_context = trace_context
    
    def _get_file_paths_from_issue(self, issue: Dict[str, Any]) -> List[str]:
        """
        Extract all relevant file paths from an issue.
        
        Args:
            issue: Issue dictionary
            
        Returns:
            List of file paths mentioned in the issue
        """
        file_paths = []
        
        # Get primary file path
        file_path = issue.get('file_path', issue.get('filePath', ''))
        if file_path and file_path != 'Unknown':
            file_paths.append(file_path)
        
        # Check for additional file paths in description or suggestion
        description = issue.get('description', '')
        suggestion = issue.get('suggestion', '')
        
        # Look for file references in text (simple heuristic)
        for text in [description, suggestion]:
            if not text:
                continue
            # Look for common file extensions
            import re
            file_pattern = r'[\w/\-\.]+\.(mm|m|h|cpp|c|hpp|py|java|js|ts|go|rs)'
            matches = re.findall(file_pattern, text)
            for match in matches:
                if match not in file_paths:
                    file_paths.append(match)
        
        return file_paths
    
    def challenge_issues(self, issues: List[Dict[str, Any]], function_context: Optional[str] = None, trace_context: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """
        Challenge issues using LLM analysis to verify they are worth pursuing.
        
        Args:
            issues: List of issue dictionaries (already filtered by Level 1 and Level 2)
            function_context: Optional original function code context for better analysis
            trace_context: Optional trace context (trace_id, callstack, repo_name, callstack_data) for trace analysis
            
        Returns:
            List of issues with false positives and trivial issues removed
        """
        # Store trace context for dropped issues logging
        if trace_context:
            self.set_trace_context(trace_context)
        if not issues:
            return issues
        
        if not self.is_available():
            # Level 3 filtering not available - return all issues
            original_count = len(issues)
            self.logger.info(f"LLMResponseChallenger: Level 3 filtering not available - returning all {original_count} issues")
            return issues
        
        # Use dummy filtering for dummy API key
        if self.api_key == "dummy-key":
            return self._dummy_challenge_issues(issues, function_context)
        
        # Real LLM-based challenging
        return self._llm_challenge_issues(issues, function_context, trace_context)
    
    def _dummy_challenge_issues(self, issues: List[Dict[str, Any]], function_context: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Dummy challenging for testing purposes.
        
        Args:
            issues: List of issue dictionaries
            function_context: Optional original function code context (not used in dummy mode)
        """
        original_count = len(issues)
        
        # Basic challenging: remove issues that seem like false positives
        challenged_issues = []
        false_positive_indicators = [
            'variable name', 'function name', 'class name',
            'comment', 'documentation', 'formatting',
            'style', 'convention', 'readability',
            'memory leak', 'not released', 'retain', 'autorelease'  # Memory management issues (likely ARC)
        ]
        
        for issue in issues:
            issue_text = issue.get('issue', '').lower()
            description = issue.get('description', '').lower()
            
            # Check if issue seems like a false positive or style issue
            is_false_positive = any(indicator in issue_text or indicator in description
                                  for indicator in false_positive_indicators)
            
            if not is_false_positive:
                # In dummy mode, don't capture evidence
                issue_with_evidence = issue.copy()
                issue_with_evidence['evidence'] = ''
                challenged_issues.append(issue_with_evidence)
        
        challenged_count = len(challenged_issues)
        dropped_count = original_count - challenged_count
        
        if dropped_count > 0:
            self.logger.info(f"LLMResponseChallenger (dummy): Challenged {dropped_count} issues as false positives, keeping {challenged_count} issues")
        else:
            self.logger.info(f"LLMResponseChallenger (dummy): No false positives found, keeping all {challenged_count} issues")
        
        return challenged_issues
    
    def _setup_response_challenger_prompts_logging(self) -> str:
        """
        Setup a separate prompts logging directory for response challenger conversations.
        
        Creates a 'response_challenger' subdirectory under the artifacts directory
        to keep response challenger LLM conversations separate from main analysis conversations.
        
        Returns:
            str: Path to the response challenger prompts directory
        """
        try:
            output_provider = get_output_directory_provider()
            artifacts_dir = output_provider.get_repo_artifacts_dir()
            response_challenger_prompts_dir = os.path.join(artifacts_dir, "response_challenger")
            os.makedirs(response_challenger_prompts_dir, exist_ok=True)
            self.logger.info(f"Setup response challenger prompts directory: {response_challenger_prompts_dir}")
            return response_challenger_prompts_dir
        except Exception as e:
            self.logger.error(f"Failed to setup response challenger prompts directory: {e}")
            return None
    
    def _llm_challenge_issues(self, issues: List[Dict[str, Any]], function_context: Optional[str] = None, trace_context: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """
        Real LLM-based challenging using structured tools like the main code analyzer.

        Uses the new async stack (`SyncStageRunner` + `stage_response_challenger`)
        from a sync entry point — spins up a short-lived `AsyncLLMClient` for the
        duration of the batch, then closes it.

        Args:
            issues: List of issue dictionaries
            function_context: Optional original function code context for better analysis
            trace_context: Optional trace context for trace analysis
        """
        from ..llm import (
            SyncStageRunner,
            make_client_config_from_dict,
            stage_response_challenger,
        )
        from ..llm.tools import ToolContext, build_default_registry

        original_count = len(issues)
        self.logger.info(f"LLMResponseChallenger: Starting Level 3 challenging of {original_count} issues")

        # Load the response challenger prompt.
        prompt_path = Path(__file__).parent.parent / "core" / "prompts" / "responseChallenger.md"
        try:
            with open(prompt_path, 'r', encoding='utf-8') as f:
                system_prompt = f.read()
        except Exception as e:
            self.logger.error(f"Failed to load response challenger prompt: {e}")
            return self._dummy_challenge_issues(issues)

        # Build the sync LLM client.
        try:
            client_config = make_client_config_from_dict(
                api_key=self.api_key,
                config=self.config,
                default_api_url=DEFAULT_LLM_API_END_POINT,
                default_model=DEFAULT_LLM_MODEL,
                default_max_tokens=DEFAULT_MAX_TOKENS,
            )
            runner = SyncStageRunner(client_config)
            self.logger.info("Initialized SyncStageRunner for Level 3 challenging")
        except Exception as e:
            self.logger.error(f"Failed to initialize SyncStageRunner: {e}")
            return self._dummy_challenge_issues(issues)

        # Build the tool registry (file-reading tools only, matching the
        # legacy `supported_tools` list).
        tools = None
        supported_tools: List[str] = []
        if self.file_content_provider:
            try:
                repo_path = (
                    self.repo_path
                    or self.config.get('repo_path', '')
                    or self.config.get('path_to_repo', '')
                )
                if not repo_path and self.file_content_provider:
                    if hasattr(self.file_content_provider, 'repo_path'):
                        repo_path = self.file_content_provider.repo_path or ''
                    elif hasattr(self.file_content_provider, 'get_repo_path'):
                        try:
                            repo_path = self.file_content_provider.get_repo_path() or ''
                        except Exception:
                            pass

                artifacts_dir = self.artifacts_dir
                if not artifacts_dir:
                    output_provider = get_output_directory_provider()
                    artifacts_dir = f"{output_provider.get_repo_artifacts_dir()}/code_insights"

                tool_ctx = ToolContext(
                    repo_path=repo_path or '',
                    file_content_provider=self.file_content_provider,
                    artifacts_dir=artifacts_dir,
                    directory_tree_util=self.directory_tree_util,
                    ignore_dirs=set(self.config.get('exclude_directories', [])),
                )
                tools = build_default_registry(tool_ctx)
                supported_tools = [
                    'readFile', 'getFileContentByLines', 'list_files', 'checkFileSize', 'runTerminalCmd'
                ]
                self.logger.info(
                    f"Tool registry initialized with {len(supported_tools)} tools for Level 3 challenging"
                )
            except Exception as e:
                self.logger.warning(f"Failed to initialize tool registry: {e}")
                self.logger.warning("Level 3 challenging will proceed without file reading tools")
                tools = None
                supported_tools = []

        # Build user prompts for all issues up front so the httpx pool is
        # amortized across the batch.
        user_prompts: List[str] = []
        for issue in issues:
            user_prompts.append(
                self._build_challenger_user_prompt(
                    issue=issue,
                    function_context=function_context,
                    trace_context=trace_context,
                    supported_tools=supported_tools,
                )
            )

        stage = stage_response_challenger(
            system_prompt, max_iterations=RESPONSE_CHALLENGER_MAX_ITERATIONS
        )
        try:
            verdicts = runner.run_many(stage, user_prompts, tools=tools)
        except Exception as e:
            self.logger.error(f"SyncStageRunner.run_many crashed: {e}")
            return self._dummy_challenge_issues(issues)

        # Assemble results.
        challenged_issues: List[Dict[str, Any]] = []
        dropped_count = 0
        for i, (issue, verdict) in enumerate(zip(issues, verdicts), 1):
            if verdict is None:
                self.logger.warning(f"No verdict from challenger for issue {i}; keeping")
                kept = issue.copy()
                kept['evidence'] = ''
                challenged_issues.append(kept)
                continue

            if not isinstance(verdict, dict):
                self.logger.warning(
                    f"Unexpected verdict type for issue {i}: {type(verdict).__name__}; keeping"
                )
                kept = issue.copy()
                kept['evidence'] = ''
                challenged_issues.append(kept)
                continue

            should_filter = bool(verdict.get('result', False))
            reason = verdict.get('reason', '')

            if should_filter:
                dropped_count += 1
                issue_summary = issue.get('issue', '')[:100]
                self.logger.info(
                    f"Issue {i} challenged as not worth pursuing by LLM: {issue_summary}..."
                )
                self.logger.debug(f"Reason: {reason}")
                self._save_dropped_issue(issue, reason)
                continue

            kept = issue.copy()
            if reason and self.capture_evidence:
                kept['evidence'] = reason
                self.logger.debug(f"Attached evidence to issue {i}: {reason[:100]}...")
            else:
                kept['evidence'] = ''
            challenged_issues.append(kept)

        challenged_count = len(challenged_issues)
        if dropped_count > 0:
            self.logger.info(
                f"LLMResponseChallenger: Level 3 dropped {dropped_count}, kept {challenged_count} issues"
            )
        else:
            self.logger.info(
                f"LLMResponseChallenger: Level 3 kept all {challenged_count} issues"
            )
        return challenged_issues

    def _build_challenger_user_prompt(
        self,
        *,
        issue: Dict[str, Any],
        function_context: Optional[str],
        trace_context: Optional[Dict[str, Any]],
        supported_tools: List[str],
    ) -> str:
        """Build one issue's user prompt — same shape as the legacy version
        (pre-fetched file content + tool hints + responseChallengerUserPrompt.md
        template) so the prompts go through the same review pipeline as before."""
        issue_data = {
            "issue": issue.get('issue', ''),
            "category": issue.get('category', ''),
            "issueType": issue.get('issueType', issue.get('issue_type', '')),
            "severity": issue.get('severity', ''),
            "description": issue.get('description', ''),
            "suggestion": issue.get('suggestion', ''),
            "file_path": issue.get('file_path', issue.get('filePath', '')),
            "line_number": issue.get('line_number', issue.get('lineNumber', '')),
            "code_snippet": issue.get('code_snippet', issue.get('codeSnippet', '')),
        }

        context_parts: List[str] = []
        if trace_context:
            trace_id = trace_context.get('trace_id', 'unknown')
            callstack = trace_context.get('callstack', [])
            context_parts.append(
                f"TRACE CONTEXT:\nTrace ID: {trace_id}\nCallstack:\n"
                + "\n".join(callstack[:10])
            )

        file_content_section = ""
        file_content_provided = False
        if function_context:
            file_content_section = (
                f"SOURCE CODE CONTEXT (from analysis stage):\n```\n{function_context}\n```"
            )
            file_content_provided = True
        elif self.file_content_provider:
            file_path = issue_data.get('file_path', '')
            line_number = issue_data.get('line_number', '')
            if file_path and file_path != 'Unknown':
                try:
                    full_content = self.file_content_provider.get_file_content(file_path)
                    if full_content:
                        if line_number and str(line_number).isdigit():
                            line_num = int(line_number)
                            lines = full_content.split('\n')
                            context_window = 50
                            start_line = max(0, line_num - context_window - 1)
                            end_line = min(len(lines), line_num + context_window)
                            numbered_lines = []
                            for idx, line in enumerate(lines[start_line:end_line], start=start_line + 1):
                                marker = ">>>" if idx == line_num else "   "
                                numbered_lines.append(f"{marker} {idx:4d} | {line}")
                            file_content_section = (
                                f"SOURCE FILE CONTENT ({file_path}, lines {start_line + 1}-{end_line}):\n"
                                f"```\n" + "\n".join(numbered_lines) + "\n```"
                            )
                            file_content_provided = True
                        elif len(full_content) <= 50000:
                            file_content_section = (
                                f"SOURCE FILE CONTENT ({file_path}):\n```\n{full_content}\n```"
                            )
                            file_content_provided = True
                except Exception as e:
                    self.logger.debug(f"Failed to pre-fetch file content for issue: {e}")

        if file_content_section:
            context_parts.append(file_content_section)

        if supported_tools:
            if file_content_provided:
                context_parts.append(
                    "ADDITIONAL TOOLS (if needed for further investigation):\n"
                    "You have access to these tools if you need additional context:\n"
                    + "\n".join(f"- {tool}" for tool in supported_tools)
                )
            else:
                context_parts.append(
                    "AVAILABLE TOOLS:\nYou have access to these tools to gather code context:\n"
                    + "\n".join(f"- {tool}" for tool in supported_tools)
                )
                context_parts.append(
                    "\nIMPORTANT: Use these tools to read the actual file contents and verify the issue. "
                    "Start by reading the file at the specified location."
                )

        context_section = "\n\n".join(context_parts) if context_parts else ""
        issue_details_json = json.dumps(issue_data, indent=2)
        file_path_str = issue_data.get('file_path', 'Unknown')
        line_number_str = str(issue_data.get('line_number', 'Unknown'))

        template = _load_prompt_template(
            "responseChallengerUserPrompt.md" if file_content_provided
            else "responseChallengerUserPromptNoContent.md"
        )
        if template:
            return (
                template
                .replace("{context_section}", context_section)
                .replace("{issue_details_json}", issue_details_json)
                .replace("{file_path}", file_path_str)
                .replace("{line_number}", line_number_str)
            )

        # Last-resort fallback if the template files are missing.
        if file_content_provided:
            return (
                "Please analyze this code issue and determine if it's worth pursuing.\n\n"
                "The source code has been provided below for your analysis. You do NOT need to use "
                "tools to read the file - the relevant code is already included.\n\n"
                f"{context_section}\n\n"
                "ISSUE DETAILS:\n"
                f"{issue_details_json}\n\n"
                "FILE LOCATION:\n"
                f"- File: {file_path_str}\n"
                f"- Line: {line_number_str}\n\n"
                "Respond with JSON format:\n"
                "- To filter out the issue: {\"result\": true, \"reason\": \"...\"}\n"
                "- To keep the issue: {\"result\": false, \"reason\": \"...\"}"
            )
        return (
            "Please analyze this code issue and determine if it's worth pursuing.\n\n"
            "IMPORTANT: You must use the available tools to read the actual source code before making "
            "your decision. The file path and line number are provided below.\n\n"
            f"{context_section}\n\n"
            "ISSUE DETAILS:\n"
            f"{issue_details_json}\n\n"
            "FILE LOCATION:\n"
            f"- File: {file_path_str}\n"
            f"- Line: {line_number_str}\n\n"
            "Respond with JSON format:\n"
            "- To filter out the issue: {\"result\": true, \"reason\": \"...\"}\n"
            "- To keep the issue: {\"result\": false, \"reason\": \"...\"}"
        )
    
    def is_available(self) -> bool:
        """
        Check if Level 3 challenging is available.
        
        Returns:
            True if Level 3 challenging can be performed, False otherwise
        """
        return self.challenger_available and bool(self.api_key)
    
    def get_challenger_stats(self) -> Dict[str, Any]:
        """
        Get statistics about the Level 3 challenger performance.
        
        Returns:
            Dictionary with challenger statistics
        """
        if not self.is_available():
            return {"available": False, "reason": "Level 3 challenging not available - no valid API key"}
        
        if self.api_key == "dummy-key":
            return {
                "available": True,
                "challenger_type": "dummy_false_positive_detection",
                "description": "Dummy Level 3 challenging using false positive keyword detection"
            }
        else:
            return {
                "available": True,
                "challenger_type": "real_llm_challenging",
                "description": "Real Level 3 challenging using response challenger prompt with file reading tools"
            }