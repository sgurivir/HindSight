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
from ..core.constants import DEFAULT_LLM_API_END_POINT
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
    
    def __init__(self, api_key: str, config: dict, dropped_issues_dir: Optional[str] = None, capture_evidence: bool = True, file_content_provider=None):
        """
        Initialize the Trace Response Challenger.
        
        Args:
            api_key: API key for LLM provider
            config: Configuration dictionary (same format as used by analyzers)
            dropped_issues_dir: Directory to save dropped issues (optional)
            capture_evidence: Whether to capture validation evidence (default: True)
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
        from ..core.llm.llm import Claude, ClaudeConfig
        from ..utils.config_util import get_llm_provider_type
        
        original_count = len(issues)
        trace_id = trace_context.get('trace_id', 'unknown_trace')
        self.logger.info(f"TraceResponseChallenger: Starting Level 3 challenging of {original_count} issues for {trace_id}")
        
        # Load the trace response challenger prompt
        prompt_path = Path(__file__).parent.parent / "core" / "prompts" / "traceResponseChallenger.md"
        try:
            with open(prompt_path, 'r', encoding='utf-8') as f:
                system_prompt = f.read()
        except Exception as e:
            self.logger.error(f"Failed to load trace response challenger prompt: {e}")
            # Fallback to dummy challenging
            return self._dummy_challenge_issues(issues, trace_context)
        
        # Initialize LLM instance
        try:
            llm_provider_type = get_llm_provider_type(self.config)
            
            claude_config = ClaudeConfig(
                api_key=self.api_key,
                api_url=self.config.get('api_end_point', DEFAULT_LLM_API_END_POINT),
                model=self.config.get('model', 'claude-3-5-sonnet-20241022'),
                max_tokens=self.config.get('max_tokens', 64000),
                temperature=self.config.get('temperature', 0.1),
                provider_type=llm_provider_type
            )
            
            claude = Claude(claude_config)
            self.logger.info(f"Initialized LLM for Trace Level 3 challenging with provider: {llm_provider_type}")
            
        except Exception as e:
            self.logger.error(f"Failed to initialize LLM for Trace Level 3 challenging: {e}")
            return self._dummy_challenge_issues(issues, trace_context)
        
        challenged_issues = []
        dropped_count = 0
        
        # Format callstack for display
        callstack_display = callstack_text if callstack_text else "\n".join(trace_context.get('callstack', []))
        
        # Process each issue individually
        for i, issue in enumerate(issues, 1):
            try:
                # Prepare issue data for LLM with trace context
                issue_data = {
                    "issue": issue.get('issue', ''),
                    "category": issue.get('category', ''),
                    "issueType": issue.get('issueType', issue.get('issue_type', '')),
                    "severity": issue.get('severity', ''),
                    "description": issue.get('description', ''),
                    "suggestion": issue.get('suggestion', ''),
                    "file_path": issue.get('file_path', issue.get('filePath', '')),
                    "line_number": issue.get('line_number', issue.get('lineNumber', '')),
                    "code_snippet": issue.get('code_snippet', issue.get('codeSnippet', ''))
                }
                
                # Create user message with trace context
                user_message = f"""Please analyze this issue found in trace analysis and determine if it's worth pursuing:

TRACE CONTEXT:
Trace ID: {trace_id}
Repository: {trace_context.get('repo_name', 'Unknown')}

Execution Callstack:
```
{callstack_display}
```

ISSUE DETAILS:
{json.dumps(issue_data, indent=2)}

VALIDATION CHECKLIST FOR TRACE ANALYSIS:
Before making your decision, please consider these critical questions:

1. Is this a real execution path issue?
   - Does the callstack show actual runtime behavior rather than hypothetical scenarios?
   - Is the issue observable in the execution trace?

2. Is this a performance or behavior problem?
   - Would fixing this improve application performance, reliability, or correctness?
   - Is this a critical path that impacts user experience?

3. Is the issue actionable?
   - Can a developer reasonably address this based on the trace information?
   - Is this worth investigating given the execution context?

Based on your analysis as a senior software engineer reviewing execution traces, should this issue be kept (legitimate performance/behavior problem) or filtered out (false positive/not worth pursuing)?

IMPORTANT: You MUST provide a detailed "reason" field in your response explaining your decision, regardless of whether you keep or filter the issue.

Respond with JSON format:
- To filter out the issue: {{"result": true, "reason": "detailed explanation of why this is a false positive or not worth pursuing in the context of this execution trace"}}
- To keep the issue: {{"result": false, "reason": "detailed explanation of why this is a legitimate issue worth fixing, including specific evidence from the execution trace"}}"""
                
                self.logger.info(f"Challenging trace issue {i}/{original_count} for {trace_id} with LLM")
                
                # Start conversation tracking
                claude.start_conversation("trace_issue_challenge", f"{trace_id}_issue_{i}")
                
                # Use run_iterative_analysis for structured responses
                analysis_result = claude.run_iterative_analysis(
                    system_prompt=system_prompt,
                    user_prompt=user_message,
                    tools_executor=None,
                    supported_tools=[],
                    max_iterations=3
                )
                
                if analysis_result:
                    try:
                        from ..utils.json_util import clean_json_response
                        cleaned_content = clean_json_response(analysis_result.strip())
                        
                        if cleaned_content and cleaned_content.strip():
                            result = json.loads(cleaned_content)
                            
                            # Normalize arrays
                            if isinstance(result, list) and result:
                                self.logger.debug(f"LLM returned array with {len(result)} elements, extracting first element")
                                result = result[0]
                            
                            if not isinstance(result, dict):
                                self.logger.warning(f"Unexpected type after JSON parsing for issue {i}: {type(result).__name__}")
                                issue_with_evidence = issue.copy()
                                issue_with_evidence['evidence'] = ''
                                challenged_issues.append(issue_with_evidence)
                                continue
                            
                            should_filter = result.get('result', False)
                            reason = result.get('reason', '')
                            
                            if not should_filter:
                                # Keep the issue and attach evidence if available
                                issue_with_evidence = issue.copy()
                                
                                if reason and self.capture_evidence:
                                    issue_with_evidence['evidence'] = reason
                                    self.logger.debug(f"Attached evidence to trace issue {i}: {reason[:100]}...")
                                else:
                                    issue_with_evidence['evidence'] = ''
                                
                                challenged_issues.append(issue_with_evidence)
                            else:
                                # Filter out the issue
                                dropped_count += 1
                                issue_summary = issue.get('issue', '')[:100]
                                self.logger.info(f"Trace issue {i} challenged as not worth pursuing by LLM: {issue_summary}...")
                                self.logger.info(f"Reason: {reason}")
                                
                                # Save the dropped issue with trace context
                                self._save_dropped_issue(issue, reason, trace_context)
                        else:
                            self.logger.warning(f"No valid JSON content after cleaning for trace issue {i}")
                            issue_with_evidence = issue.copy()
                            issue_with_evidence['evidence'] = ''
                            challenged_issues.append(issue_with_evidence)
                            
                    except json.JSONDecodeError as e:
                        self.logger.warning(f"Failed to parse cleaned LLM response for trace issue {i}: {e}")
                        issue_with_evidence = issue.copy()
                        issue_with_evidence['evidence'] = ''
                        challenged_issues.append(issue_with_evidence)
                else:
                    self.logger.warning(f"No response from structured analysis for trace issue {i}")
                    issue_with_evidence = issue.copy()
                    issue_with_evidence['evidence'] = ''
                    challenged_issues.append(issue_with_evidence)
                    
            except Exception as e:
                self.logger.error(f"Error challenging trace issue {i} with LLM: {e}")
                issue_with_evidence = issue.copy()
                issue_with_evidence['evidence'] = ''
                challenged_issues.append(issue_with_evidence)
        
        challenged_count = len(challenged_issues)
        
        if dropped_count > 0:
            self.logger.info(f"TraceResponseChallenger: Level 3 challenged {dropped_count} issues as not worth pursuing for {trace_id}, keeping {challenged_count} issues")
        else:
            self.logger.info(f"TraceResponseChallenger: Level 3 found all issues worth pursuing for {trace_id}, keeping all {challenged_count} issues")
        
        return challenged_issues
    
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