#!/usr/bin/env python3
"""
Trace Analysis Response Challenger (Level 3 Filtering)

This module provides Level-3 filtering specifically for trace analysis results.
It challenges trace analysis findings by having an LLM verify if issues found in
execution traces are legitimate performance/behavior problems worth pursuing.

Unlike the code analysis response challenger, this one:
1. Maintains trace context (trace_id, callstack, repo_name, callstack_data)
2. Uses a trace-specific validation prompt
3. Saves dropped issues in trace analysis format
"""

import os
import sys
import json
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional
from ..core.constants import DEFAULT_LLM_API_END_POINT, DEFAULT_LLM_MODEL, DEFAULT_MAX_TOKENS
from ..utils.log_util import get_logger
from ..utils.output_directory_provider import get_output_directory_provider

# Add the project root to Python path for imports
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))


class TraceResponseChallenger:
    """
    Level 3 filter specifically for trace analysis that uses LLM to challenge and verify issues.
    This filter operates on issues from trace analysis that have already passed Level 1 and Level 2 filtering.
    It acts as a senior software engineer reviewing execution traces to determine if issues are worth pursuing.
    """
    
    def __init__(self, api_key: str, config: dict, dropped_issues_dir: Optional[str] = None, capture_evidence: bool = False, file_content_provider=None):
        """
        Initialize the Trace Response Challenger.

        Args:
            api_key: API key for LLM provider
            config: Configuration dictionary (same format as used by analyzers)
            dropped_issues_dir: Directory to save dropped issues (optional)
            capture_evidence: Whether to attach challenger reasoning to each kept issue (default: False — challenger artifacts are still persisted to disk).
            file_content_provider: FileContentProvider instance for file resolution (optional)
        """
        self.logger = get_logger(__name__)
        self.api_key = api_key
        self.config = config
        self.dropped_issues_dir = dropped_issues_dir
        self.capture_evidence = capture_evidence
        self.file_content_provider = file_content_provider
        
        # Set up dropped issues directory
        self._setup_dropped_issues_directory()
        
        # Enable challenger filtering if we have any API key (including dummy for testing)
        if api_key:
            self.challenger_available = True
            if api_key == "dummy-key":
                self.logger.info("TraceResponseChallenger initialized - Level 3 filtering enabled (dummy mode)")
            else:
                self.logger.info("TraceResponseChallenger initialized - Level 3 filtering enabled")
            
            # Log evidence capture status
            if self.capture_evidence:
                self.logger.info("Evidence capture enabled - validation reasoning will be attached to issues")
            else:
                self.logger.info("Evidence capture disabled - validation reasoning will not be attached")
        else:
            self.challenger_available = False
            self.logger.info("TraceResponseChallenger initialized - Level 3 filtering disabled (no API key)")
    
    def _setup_dropped_issues_directory(self) -> None:
        """Setup the dropped_issues directory under output folder."""
        if self.dropped_issues_dir:
            # Use provided directory
            try:
                os.makedirs(self.dropped_issues_dir, exist_ok=True)
                self.logger.info(f"Trace Level 3 dropped issues directory created: {self.dropped_issues_dir}")
            except Exception as e:
                self.logger.error(f"Failed to create Trace Level 3 dropped issues directory: {e}")
                self.dropped_issues_dir = None
        else:
            # Auto-create directory using output provider
            try:
                output_provider = get_output_directory_provider()
                output_base_dir = output_provider.get_repo_artifacts_dir()
                self.dropped_issues_dir = os.path.join(output_base_dir, "dropped_issues", "level3_trace_response_challenger")
                os.makedirs(self.dropped_issues_dir, exist_ok=True)
                self.logger.info(f"Trace Level 3 dropped issues directory created: {self.dropped_issues_dir}")
            except Exception as e:
                self.logger.error(f"Failed to create Trace Level 3 dropped issues directory: {e}")
                self.dropped_issues_dir = None
    
    def _save_dropped_issue(self, issue: Dict[str, Any], reason: str, trace_context: Dict[str, Any]) -> None:
        """
        Save a dropped issue to a JSON file in trace analysis format.
        
        Args:
            issue: The original issue that was dropped
            reason: The LLM's reasoning for why the issue was dropped
            trace_context: Trace context containing trace_id, callstack, repo_name, callstack_data
        """
        if not self.dropped_issues_dir:
            self.logger.warning("Dropped issues directory not available, cannot save dropped issue")
            return
            
        try:
            # Create a unique filename based on trace_id and timestamp
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
            trace_id = trace_context.get('trace_id', 'unknown_trace')
            
            # Create a safe filename from issue content
            issue_text = issue.get('issue', 'unknown_issue')[:50]
            safe_issue_text = "".join(c for c in issue_text if c.isalnum() or c in ('_', '-', ' ')).replace(' ', '_')
            
            filename = f"{trace_id}_level3_dropped_{timestamp}_{safe_issue_text}.json"
            filepath = os.path.join(self.dropped_issues_dir, filename)
            
            # Create the dropped issue record in trace analysis format
            dropped_record = {
                "timestamp": datetime.now().isoformat(),
                "filter_level": "Level 3 - Trace Response Challenger",
                "filter_type": "LLM Response Challenger",
                "trace_id": trace_context.get('trace_id'),
                "callstack": trace_context.get('callstack', []),
                "repo_name": trace_context.get('repo_name'),
                "results": [issue],  # Store dropped issue in results array to match trace analysis format
                "reason": reason  # LLM's reasoning for dropping the issue
            }
            
            # Add callstack_data if available
            if 'callstack_data' in trace_context:
                dropped_record["callstack_data"] = trace_context['callstack_data']
            
            # Save to file
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(dropped_record, f, indent=2, ensure_ascii=False)
                
            self.logger.debug(f"Trace Level 3 dropped issue saved to: {filepath}")
            
        except Exception as e:
            self.logger.error(f"Failed to save Trace Level 3 dropped issue: {e}")
    
    def challenge_issues(self, issues: List[Dict[str, Any]], trace_context: Dict[str, Any], callstack_text: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Challenge trace analysis issues using LLM to verify they are worth pursuing.
        
        Args:
            issues: List of issue dictionaries (already filtered by Level 1 and Level 2)
            trace_context: Trace context containing trace_id, callstack, repo_name, callstack_data
            callstack_text: Optional callstack in text format for better analysis
            
        Returns:
            List of issues with false positives and trivial issues removed
        """
        if not issues:
            return issues
        
        if not self.is_available():
            # Level 3 filtering not available - return all issues
            original_count = len(issues)
            self.logger.info(f"TraceResponseChallenger: Level 3 filtering not available - returning all {original_count} issues")
            return issues
        
        # Use dummy filtering for dummy API key
        if self.api_key == "dummy-key":
            return self._dummy_challenge_issues(issues, trace_context)
        
        # Real LLM-based challenging
        return self._llm_challenge_issues(issues, trace_context, callstack_text)
    
    def _dummy_challenge_issues(self, issues: List[Dict[str, Any]], trace_context: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Dummy challenging for testing purposes.
        
        Args:
            issues: List of issue dictionaries
            trace_context: Trace context for logging dropped issues
        """
        original_count = len(issues)
        
        # Basic challenging: remove issues that seem like false positives
        challenged_issues = []
        false_positive_indicators = [
            'variable name', 'function name', 'class name',
            'comment', 'documentation', 'formatting',
            'style', 'convention', 'readability'
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
            else:
                # Save dropped issue
                self._save_dropped_issue(issue, "Dummy mode: False positive indicator detected", trace_context)
        
        challenged_count = len(challenged_issues)
        dropped_count = original_count - challenged_count
        
        if dropped_count > 0:
            self.logger.info(f"TraceResponseChallenger (dummy): Challenged {dropped_count} issues as false positives, keeping {challenged_count} issues")
        else:
            self.logger.info(f"TraceResponseChallenger (dummy): No false positives found, keeping all {challenged_count} issues")
        
        return challenged_issues
    
    def _llm_challenge_issues(self, issues: List[Dict[str, Any]], trace_context: Dict[str, Any], callstack_text: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Real LLM-based challenging using trace-specific validation prompt.

        Args:
            issues: List of issue dictionaries
            trace_context: Trace context containing trace_id, callstack, repo_name, callstack_data
            callstack_text: Optional callstack in text format for better analysis
        """
        from ..llm import (
            SyncStageRunner,
            make_client_config_from_dict,
            stage_response_challenger,
        )

        original_count = len(issues)
        trace_id = trace_context.get('trace_id', 'unknown_trace')
        self.logger.info(
            f"TraceResponseChallenger: Starting Level 3 challenging of {original_count} issues for {trace_id}"
        )

        # Load the trace response challenger prompt
        prompt_path = Path(__file__).parent.parent / "core" / "prompts" / "traceResponseChallenger.md"
        try:
            with open(prompt_path, 'r', encoding='utf-8') as f:
                system_prompt = f.read()
        except Exception as e:
            self.logger.error(f"Failed to load trace response challenger prompt: {e}")
            return self._dummy_challenge_issues(issues, trace_context)

        try:
            client_config = make_client_config_from_dict(
                api_key=self.api_key,
                config=self.config,
                default_api_url=DEFAULT_LLM_API_END_POINT,
                default_model=DEFAULT_LLM_MODEL,
                default_max_tokens=DEFAULT_MAX_TOKENS,
            )
            runner = SyncStageRunner(client_config)
            self.logger.info("Initialized SyncStageRunner for Trace Level 3 challenging")
        except Exception as e:
            self.logger.error(f"Failed to initialize SyncStageRunner: {e}")
            return self._dummy_challenge_issues(issues, trace_context)

        # Build all user prompts up front, then run them in a single batch
        # so the httpx pool is amortized across the trace.
        callstack_display = callstack_text if callstack_text else "\n".join(trace_context.get('callstack', []))
        user_prompts: List[str] = []
        issue_data_list: List[Dict[str, Any]] = []
        for issue in issues:
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
            issue_data_list.append(issue_data)
            user_prompts.append(
                self._build_trace_user_prompt(
                    issue_data=issue_data,
                    trace_id=trace_id,
                    repo_name=trace_context.get('repo_name', 'Unknown'),
                    callstack_display=callstack_display,
                )
            )

        stage = stage_response_challenger(system_prompt, max_iterations=3)
        try:
            results = runner.run_many(stage, user_prompts)
        except Exception as e:
            self.logger.error(f"SyncStageRunner.run_many crashed for {trace_id}: {e}")
            return self._dummy_challenge_issues(issues, trace_context)

        challenged_issues: List[Dict[str, Any]] = []
        dropped_count = 0

        for i, (issue, verdict) in enumerate(zip(issues, results), 1):
            if verdict is None:
                self.logger.warning(
                    f"No verdict from challenger for trace issue {i} ({trace_id}); keeping issue"
                )
                kept = issue.copy()
                kept['evidence'] = ''
                challenged_issues.append(kept)
                continue

            # `stage_response_challenger` already validated `result: bool`
            should_filter = bool(verdict.get('result', False)) if isinstance(verdict, dict) else False
            reason = verdict.get('reason', '') if isinstance(verdict, dict) else ''

            if should_filter:
                dropped_count += 1
                self.logger.info(
                    f"Trace issue {i} ({trace_id}) challenged as not worth pursuing: "
                    f"{issue.get('issue', '')[:100]}..."
                )
                self.logger.debug(f"Reason: {reason}")
                self._save_dropped_issue(issue, reason, trace_context)
                continue

            kept = issue.copy()
            if reason and self.capture_evidence:
                kept['evidence'] = reason
            else:
                kept['evidence'] = ''
            challenged_issues.append(kept)

        challenged_count = len(challenged_issues)
        if dropped_count > 0:
            self.logger.info(
                f"TraceResponseChallenger: Level 3 dropped {dropped_count}, "
                f"kept {challenged_count} issues for {trace_id}"
            )
        else:
            self.logger.info(
                f"TraceResponseChallenger: Level 3 kept all {challenged_count} issues for {trace_id}"
            )

        return challenged_issues

    @staticmethod
    def _build_trace_user_prompt(
        *,
        issue_data: Dict[str, Any],
        trace_id: str,
        repo_name: str,
        callstack_display: str,
    ) -> str:
        return (
            "Please analyze this issue found in trace analysis and determine if it's worth pursuing:\n\n"
            "TRACE CONTEXT:\n"
            f"Trace ID: {trace_id}\n"
            f"Repository: {repo_name}\n\n"
            "Execution Callstack:\n"
            "```\n"
            f"{callstack_display}\n"
            "```\n\n"
            "ISSUE DETAILS:\n"
            f"{json.dumps(issue_data, indent=2)}\n\n"
            "VALIDATION CHECKLIST FOR TRACE ANALYSIS:\n"
            "Before making your decision, please consider these critical questions:\n\n"
            "1. Is this a real execution path issue?\n"
            "   - Does the callstack show actual runtime behavior rather than hypothetical scenarios?\n"
            "   - Is the issue observable in the execution trace?\n\n"
            "2. Is this a performance or behavior problem?\n"
            "   - Would fixing this improve application performance, reliability, or correctness?\n"
            "   - Is this a critical path that impacts user experience?\n\n"
            "3. Is the issue actionable?\n"
            "   - Can a developer reasonably address this based on the trace information?\n"
            "   - Is this worth investigating given the execution context?\n\n"
            "Based on your analysis as a senior software engineer reviewing execution traces, should this "
            "issue be kept (legitimate performance/behavior problem) or filtered out "
            "(false positive/not worth pursuing)?\n\n"
            "IMPORTANT: You MUST provide a detailed \"reason\" field in your response explaining your decision, "
            "regardless of whether you keep or filter the issue.\n\n"
            "Respond with JSON format:\n"
            "- To filter out the issue: {\"result\": true, \"reason\": \"detailed explanation of why this is a "
            "false positive or not worth pursuing in the context of this execution trace\"}\n"
            "- To keep the issue: {\"result\": false, \"reason\": \"detailed explanation of why this is a "
            "legitimate issue worth fixing, including specific evidence from the execution trace\"}"
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
            return {"available": False, "reason": "Trace Level 3 challenging not available - no valid API key"}
        
        if self.api_key == "dummy-key":
            return {
                "available": True,
                "challenger_type": "dummy_trace_false_positive_detection",
                "description": "Dummy Trace Level 3 challenging using false positive keyword detection"
            }
        else:
            return {
                "available": True,
                "challenger_type": "real_llm_trace_challenging",
                "description": "Real Trace Level 3 challenging using trace response challenger prompt"
            }