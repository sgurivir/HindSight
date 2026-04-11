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
from ..core.constants import DEFAULT_LLM_API_END_POINT, RESPONSE_CHALLENGER_MAX_ITERATIONS
from ..utils.log_util import get_logger
from ..utils.output_directory_provider import get_output_directory_provider

# Add the project root to Python path for imports
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))


class LLMResponseChallenger:
    """
    Level 3 filter that uses LLM analysis to challenge and verify issues.
    This filter operates on issues that have already passed Level 1 and Level 2 filtering.
    It acts as a senior software engineer reviewing the code to determine if issues are worth pursuing.
    """
    
    def __init__(self, api_key: str, config: dict, dropped_issues_dir: Optional[str] = None, capture_evidence: bool = True, file_content_provider=None, directory_tree_util=None, repo_path: Optional[str] = None, artifacts_dir: Optional[str] = None):
        """
        Initialize the LLM Response Challenger.
        
        Args:
            api_key: API key for LLM provider
            config: Configuration dictionary (same format as used by analyzers)
            dropped_issues_dir: Directory to save dropped issues (optional)
            capture_evidence: Whether to capture validation evidence (default: True)
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
        
        Uses the shared Tools class from hindsight.core.llm.tools.tools for OpenAI-compatible
        tool execution, ensuring consistency with the main code analyzer pipeline.
        
        Args:
            issues: List of issue dictionaries
            function_context: Optional original function code context for better analysis
            trace_context: Optional trace context for trace analysis
        """
        from ..core.llm.llm import Claude, ClaudeConfig
        from ..core.llm.tools.tools import Tools
        from ..utils.config_util import get_llm_provider_type
        
        original_count = len(issues)
        self.logger.info(f"LLMResponseChallenger: Starting Level 3 challenging of {original_count} issues")
        
        # Load the response challenger prompt
        prompt_path = Path(__file__).parent.parent / "core" / "prompts" / "responseChallenger.md"
        try:
            with open(prompt_path, 'r', encoding='utf-8') as f:
                system_prompt = f.read()
        except Exception as e:
            self.logger.error(f"Failed to load response challenger prompt: {e}")
            # Fallback to dummy challenging
            return self._dummy_challenge_issues(issues)
        
        # Initialize LLM instance using existing infrastructure
        try:
            # Use the existing LLM infrastructure that handles AWS Bedrock properly
            llm_provider_type = get_llm_provider_type(self.config)
            
            # Create ClaudeConfig using existing config structure
            claude_config = ClaudeConfig(
                api_key=self.api_key,
                api_url=self.config.get('api_end_point', DEFAULT_LLM_API_END_POINT),
                model=self.config.get('model', 'claude-3-5-sonnet-20241022'),
                max_tokens=self.config.get('max_tokens', 64000),
                temperature=self.config.get('temperature', 0.1),
                provider_type=llm_provider_type
            )
            
            # Create Claude instance using proper config
            claude = Claude(claude_config)
            
            self.logger.info(f"Initialized LLM for Level 3 challenging with provider: {llm_provider_type}")
            
            # Setup separate prompts logging directory for response challenger
            # This keeps response challenger conversations separate from main analysis
            response_challenger_prompts_dir = self._setup_response_challenger_prompts_logging()
            if response_challenger_prompts_dir:
                # Override the Claude class prompts directory for response challenger
                Claude._prompts_dir = response_challenger_prompts_dir
                Claude._conversation_counter = 0  # Reset counter for this directory
                self.logger.info(f"Response challenger conversations will be logged to: {response_challenger_prompts_dir}")
            
        except Exception as e:
            self.logger.error(f"Failed to initialize LLM for Level 3 challenging: {e}")
            # Fallback to dummy challenging
            return self._dummy_challenge_issues(issues)
        
        # Initialize tools executor using the shared Tools class from main analyzer
        # The Tools class provides OpenAI-compatible tool execution via execute_tool_use()
        # This is consistent with how code_analysis.py and diff_analysis.py use tools
        tools_executor = None
        supported_tools = []
        
        if self.file_content_provider:
            try:
                from ..utils.output_directory_provider import get_output_directory_provider
                output_provider = get_output_directory_provider()
                
                # Use repo_path from constructor if provided, otherwise try config/file_content_provider
                repo_path = self.repo_path or ''
                
                # If not provided via constructor, try config
                if not repo_path:
                    repo_path = self.config.get('repo_path', '') or self.config.get('path_to_repo', '')
                
                # If still empty, try to get from file_content_provider
                if not repo_path and self.file_content_provider:
                    if hasattr(self.file_content_provider, 'repo_path'):
                        repo_path = self.file_content_provider.repo_path or ''
                    elif hasattr(self.file_content_provider, 'get_repo_path'):
                        try:
                            repo_path = self.file_content_provider.get_repo_path() or ''
                        except Exception:
                            pass
                
                if not repo_path:
                    self.logger.warning("No repo_path found - runTerminalCmd tool may not work correctly")
                else:
                    self.logger.debug(f"Using repo_path for Tools: {repo_path}")
                
                # Use artifacts_dir from constructor if provided, otherwise use output provider
                artifacts_dir = self.artifacts_dir
                if not artifacts_dir:
                    artifacts_dir = f"{output_provider.get_repo_artifacts_dir()}/code_insights"
                
                # Create Tools instance - it provides .tools attribute via self-reference pattern
                # The run_iterative_analysis expects tools_executor.tools.execute_tool_use()
                # We create a simple wrapper that provides this interface
                tools_instance = Tools(
                    repo_path=repo_path,
                    file_content_provider=self.file_content_provider,
                    artifacts_dir=artifacts_dir,
                    directory_tree_util=self.directory_tree_util,
                    ignore_dirs=set(self.config.get('exclude_directories', []))
                )
                
                # Create a simple wrapper to match the expected API pattern
                # tools_executor.tools.execute_tool_use() is the expected interface
                class ToolsExecutorWrapper:
                    """Wrapper to provide .tools attribute for tools_executor API compatibility."""
                    def __init__(self, tools_inst):
                        self.tools = tools_inst
                
                tools_executor = ToolsExecutorWrapper(tools_instance)
                
                # Enable file reading tools - these are the tools available in the shared Tools class
                supported_tools = ['readFile', 'getFileContentByLines', 'list_files', 'checkFileSize', 'runTerminalCmd']
                self.logger.info(f"Tools executor initialized with {len(supported_tools)} tools for Level 3 challenging")
                self.logger.info(f"Using shared Tools class from hindsight.core.llm.tools.tools")
                
            except Exception as e:
                self.logger.warning(f"Failed to initialize tools executor for Level 3 challenging: {e}")
                self.logger.warning("Level 3 challenging will proceed without file reading tools")
        
        challenged_issues = []
        dropped_count = 0
        
        # Process each issue individually using structured approach like main code analyzer
        for i, issue in enumerate(issues, 1):
            try:
                # Extract file paths from the issue
                file_paths = self._get_file_paths_from_issue(issue)
                
                # Prepare issue data for LLM with code context
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
                
                # Build context message - only include tools information
                # Let the LLM use tools to fetch the actual code
                context_parts = []
                
                # Add trace context if available (for trace analysis)
                if trace_context:
                    trace_id = trace_context.get('trace_id', 'unknown')
                    callstack = trace_context.get('callstack', [])
                    context_parts.append(f"TRACE CONTEXT:\nTrace ID: {trace_id}\nCallstack:\n" + "\n".join(callstack[:10]))  # First 10 frames
                
                # Add tools information - LLM should use these to fetch code
                if tools_executor and supported_tools:
                    context_parts.append(f"AVAILABLE TOOLS:\nYou have access to these tools to gather code context:\n" + "\n".join(f"- {tool}" for tool in supported_tools))
                    context_parts.append("\nIMPORTANT: Use these tools to read the actual file contents and verify the issue. Start by reading the file at the specified location.")
                
                context_section = "\n\n".join(context_parts) if context_parts else ""
                
                # Create user message with issue data only - no code context provided
                # LLM must use tools to fetch the code
                user_message = f"""Please analyze this code issue and determine if it's worth pursuing.

IMPORTANT: You must use the available tools to read the actual source code before making your decision. The file path and line number are provided below.

{context_section}

ISSUE DETAILS:
{json.dumps(issue_data, indent=2)}

FILE LOCATION:
- File: {issue_data.get('file_path', 'Unknown')}
- Line: {issue_data.get('line_number', 'Unknown')}

INSTRUCTIONS:
1. Analyze the actual code to verify if the issue is legitimate
2. Consider the validation checklist below

VALIDATION CHECKLIST:
Before making your decision, please consider these critical questions:

1. Is there concrete evidence in the actual code?
   - Can you point to specific lines or code patterns that support this issue?
   - Is the issue based on actual observable code behavior rather than assumptions?

2. Would fixing this provide meaningful value?
   - Would addressing this issue provide tangible benefits to code quality, performance, or maintainability?
   - Is this worth a developer's time to investigate and fix?

Based on your analysis as a senior software engineer and the validation checklist above, should this issue be kept (legitimate bug/optimization) or filtered out (false positive/not worth pursuing)?

IMPORTANT: You MUST provide a detailed "reason" field in your response explaining your decision, regardless of whether you keep or filter the issue.

Respond with JSON format:
- To filter out the issue: {{"result": true, "reason": "detailed explanation of why this is a false positive or not worth pursuing"}}
- To keep the issue: {{"result": false, "reason": "detailed explanation of why this is a legitimate issue worth fixing, including specific evidence from the code"}}"""
                
                # Use structured analysis like main code analyzer
                self.logger.info(f"Challenging issue {i}/{original_count} with LLM using structured approach")
                
                # Start conversation tracking
                claude.start_conversation("issue_challenge", f"issue_{i}")
                
                # Use run_iterative_analysis for structured responses like main code analyzer
                analysis_result = claude.run_iterative_analysis(
                    system_prompt=system_prompt,
                    user_prompt=user_message,
                    tools_executor=tools_executor,  # Provide tools for file reading
                    supported_tools=supported_tools,   # Enable file reading tools
                    max_iterations=RESPONSE_CHALLENGER_MAX_ITERATIONS  # Use constant for complex investigations
                )
                
                # Log the complete conversation to markdown file in response_challenger directory
                try:
                    claude.log_complete_conversation(final_result=analysis_result if analysis_result else "No result")
                except Exception as log_error:
                    self.logger.warning(f"Failed to log conversation for issue {i}: {log_error}")
                
                if analysis_result:
                    try:
                        # Clean and parse LLM response using the same cleanup utility as main LLM code
                        from ..utils.json_util import clean_json_response
                        cleaned_content = clean_json_response(analysis_result.strip())
                        
                        # Check if we have valid JSON content after cleaning
                        if cleaned_content and cleaned_content.strip():
                            result = json.loads(cleaned_content)
                            
                            # Normalize arrays: if LLM returns a list, extract the first element
                            if isinstance(result, list) and result:
                                self.logger.debug(f"LLM returned array with {len(result)} elements, extracting first element")
                                result = result[0]
                            
                            # Defensive type checking: ensure result is a dict
                            if not isinstance(result, dict):
                                self.logger.warning(f"Unexpected type after JSON parsing for issue {i}: {type(result).__name__}")
                                self.logger.debug(f"cleaned_content was: {cleaned_content}")
                                # Keep issue if result is not a dict (with empty evidence)
                                issue_with_evidence = issue.copy()
                                issue_with_evidence['evidence'] = ''
                                challenged_issues.append(issue_with_evidence)
                                continue
                            
                            should_filter = result.get('result', False)
                            reason = result.get('reason', '')  # Empty string if no reason provided
                            
                            if not should_filter:
                                # Keep the issue and attach evidence if available
                                issue_with_evidence = issue.copy()
                                
                                # Only attach evidence if reason is non-empty AND capture is enabled
                                # If LLM doesn't generate evidence, field remains empty (no error/warning)
                                if reason and self.capture_evidence:
                                    issue_with_evidence['evidence'] = reason
                                    self.logger.debug(f"Attached evidence to issue {i}: {reason[:100]}...")
                                else:
                                    issue_with_evidence['evidence'] = ''  # Empty by default
                                
                                challenged_issues.append(issue_with_evidence)
                            else:
                                # Filter out the issue (LLM says it's not worth pursuing)
                                dropped_count += 1
                                issue_summary = issue.get('issue', '')[:100]
                                self.logger.info(f"Issue {i} challenged as not worth pursuing by LLM: {issue_summary}...")
                                self.logger.debug(f"Reason: {reason}")
                                
                                # Save the dropped issue
                                self._save_dropped_issue(issue, reason)
                        else:
                            self.logger.warning(f"No valid JSON content after cleaning for issue {i}")
                            self.logger.warning(f"Original response was: {analysis_result}")
                            # Keep issue if no valid content after cleaning (with empty evidence)
                            issue_with_evidence = issue.copy()
                            issue_with_evidence['evidence'] = ''
                            challenged_issues.append(issue_with_evidence)
                            
                    except json.JSONDecodeError as e:
                        self.logger.warning(f"Failed to parse cleaned LLM response for issue {i}: {e}")
                        self.logger.warning(f"Cleaned response content was: {cleaned_content}")
                        self.logger.warning(f"Original response was: {analysis_result}")
                        # Keep issue if we can't parse cleaned response (with empty evidence)
                        issue_with_evidence = issue.copy()
                        issue_with_evidence['evidence'] = ''
                        challenged_issues.append(issue_with_evidence)
                else:
                    self.logger.warning(f"No response from structured analysis for issue {i}")
                    # Keep issue if no response (with empty evidence)
                    issue_with_evidence = issue.copy()
                    issue_with_evidence['evidence'] = ''
                    challenged_issues.append(issue_with_evidence)
                    
            except Exception as e:
                self.logger.error(f"Error challenging issue {i} with structured LLM: {e}")
                # Keep issue if error occurs (with empty evidence)
                issue_with_evidence = issue.copy()
                issue_with_evidence['evidence'] = ''
                challenged_issues.append(issue_with_evidence)
        
        challenged_count = len(challenged_issues)
        
        if dropped_count > 0:
            self.logger.info(f"LLMResponseChallenger: Level 3 challenged {dropped_count} issues as not worth pursuing, keeping {challenged_count} issues")
        else:
            self.logger.info(f"LLMResponseChallenger: Level 3 found all issues worth pursuing, keeping all {challenged_count} issues")
        
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