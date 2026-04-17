#!/usr/bin/env python3
# Created by Sridhar Gurivireddy
"""
Diff Analysis Module
Handles diff analysis logic specifically for git commit comparisons.
Shares core functionality with code_analysis.py but optimized for diff workflows.

Supports two analysis modes:
1. Chunk-based analysis: Traditional diff chunk analysis
2. Function-level analysis: Analyzes individual functions affected by the diff
"""

import hashlib
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Dict, Any, Tuple, List

from .llm import Claude, ClaudeConfig
from .tools import Tools
from .iterative import DiffContextAnalyzer, DiffAnalysisAnalyzer
from ..constants import MAX_TOOL_ITERATIONS
from ...utils.file_util import write_file
from ...utils.json_util import validate_and_format_json, clean_json_response
from ...utils.log_util import get_logger
from ...utils.output_directory_provider import get_output_directory_provider

logger = get_logger(__name__)


@dataclass
class DiffAnalysisConfig:
    """Configuration for diff analysis"""
    api_key: str
    api_url: str
    model: str
    repo_path: str
    output_file: str
    max_tokens: int = 64000
    temperature: float = 0.1
    config: Dict[str, Any] = None  # Store the full configuration dict
    file_content_provider: Any = None  # FileContentProvider instance for file resolution


class DiffAnalysis:
    """
    Diff analysis orchestrator specifically for git commit analysis.
    Handles the complete diff analysis process from input to output.
    """

    def __init__(self, config: DiffAnalysisConfig):
        """
        Initialize DiffAnalysis with configuration.

        Args:
            config: Diff analysis configuration
        """
        self.config = config

        # Initialize Claude client
        claude_config = ClaudeConfig(
            api_key=config.api_key,
            api_url=config.api_url,
            model=config.model,
            max_tokens=config.max_tokens,
            temperature=config.temperature,
            provider_type=config.config.get('llm_provider_type', 'aws_bedrock') if config.config else 'aws_bedrock'
        )
        self.claude = Claude(claude_config)

        # Initialize tools similar to CodeAnalysis
        try:
            output_provider = get_output_directory_provider()
            output_base_dir = output_provider.get_custom_base_dir()
        except RuntimeError:
            # Fallback if singleton not configured
            output_base_dir = None

        # Get the artifacts directory path
        output_provider = get_output_directory_provider()
        artifacts_dir = f"{output_provider.get_repo_artifacts_dir()}/code_insights"

        # Extract ignored directories from config for LLM-based directory filtering
        ignore_dirs = set()
        if config.config:
            exclude_directories = config.config.get('exclude_directories', [])
            if exclude_directories:
                ignore_dirs.update(exclude_directories)
                logger.info(f"Extracted {len(ignore_dirs)} ignored directories from config for diff analysis: {ignore_dirs}")
            else:
                logger.info("No excluded directories found in config for diff analysis")
        else:
            logger.info("No config provided for diff analysis - no directories will be ignored")

        # Initialize DirectoryTreeUtil if available
        directory_tree_util = None
        try:
            from ...utils.directory_tree_util import DirectoryTreeUtil
            directory_tree_util = DirectoryTreeUtil()
            logger.info("Created DirectoryTreeUtil instance for diff analysis")
        except Exception as e:
            logger.warning(f"Could not create DirectoryTreeUtil instance: {e}")

        self.tools = Tools(config.repo_path, output_base_dir, config.file_content_provider, artifacts_dir, directory_tree_util, ignore_dirs)

        # Token tracking
        self.total_input_tokens = 0
        self.total_output_tokens = 0

        logger.info("Initialized DiffAnalysis with provided config and tools support")

    def _extract_and_log_token_usage(self, response: Dict[str, Any], iteration: int) -> None:
        """
        Extract token usage from API response and log it.

        Args:
            response: API response from Claude
            iteration: Current iteration number
        """
        try:
            # Extract token usage from response
            usage = response.get("usage", {})

            input_tokens = (
                usage.get("input_tokens", 0) or
                usage.get("prompt_tokens", 0)
            )
            output_tokens = (
                usage.get("output_tokens", 0) or
                usage.get("completion_tokens", 0)
            )

            if input_tokens > 0 or output_tokens > 0:
                # Log current API call token usage
                logger.info(f"Iteration {iteration} - Input tokens: {input_tokens:,}, Output tokens: {output_tokens:,}")

                # Update totals
                self.total_input_tokens += input_tokens
                self.total_output_tokens += output_tokens

                # Log running totals
                logger.info(f"Running totals - Input tokens: {self.total_input_tokens:,}, Output tokens: {self.total_output_tokens:,}")
            else:
                logger.warning(f"Iteration {iteration} - No token usage information found in API response")

        except Exception as e:
            logger.error(f"Error extracting token usage from API response: {e}")

    def _log_final_token_summary(self) -> None:
        """Log final token usage summary."""
        try:
            total_tokens = self.total_input_tokens + self.total_output_tokens
            logger.info(f"TOKEN USAGE SUMMARY - Input: {self.total_input_tokens:,}, Output: {self.total_output_tokens:,}, Total: {total_tokens:,}")
        except Exception as e:
            logger.error(f"Error logging final token summary: {e}")

    def get_token_totals(self) -> tuple:
        """
        Get the total input and output tokens used by this analysis.

        Returns:
            tuple: (total_input_tokens, total_output_tokens)
        """
        return self.total_input_tokens, self.total_output_tokens

    def _extract_response_text(self, response: Dict[str, Any]) -> Optional[str]:
        """
        Extract text content from LLM response, handling different response formats.
        
        PROVIDER FORMAT DETECTION:
        ==========================
        Uses duck-typing (hasattr) to detect format, not isinstance() or provider_type:
        
        - Claude format: has "content" key with list of content blocks
          Extracts text from blocks with type="text"
        
        - AWS Bedrock format: has "choices" key with message array
          Extracts content from choices[0].message.content
        
        - String format: Direct string response (fallback)
        
        This approach works regardless of which provider (llm_provider_type) was configured.
        
        Args:
            response: LLM response dictionary
            
        Returns:
            str: Extracted text content or None if not found
        """
        if not response:
            return None
        
        # Check if response is string-like
        if hasattr(response, 'strip'):
            return response
            
        # Check if response is dict-like
        if not hasattr(response, 'get'):
            return None
            
        # Detect format based on structure
        has_content_blocks = "content" in response and response.get("content") and hasattr(response.get("content"), '__iter__') and not hasattr(response.get("content"), 'strip')
        has_choices = "choices" in response
        
        # Handle Claude native format
        if has_content_blocks:
            content_blocks = response.get("content", [])
            for block in content_blocks:
                block_type = block.get("type") if hasattr(block, 'get') else None
                if block_type == "text":
                    return block.get("text", "")
        
        # Handle AWS Bedrock format
        elif has_choices:
            choices = response.get("choices", [])
            if choices:
                assistant_message = choices[0].get("message", {})
                return assistant_message.get("content", "")
            
        return None


    def _process_analysis_result(self, result: str) -> Tuple[bool, str]:
        """
        Process and clean the analysis result using the same approach as code_analysis.py.

        Args:
            result: Raw analysis result from Claude

        Returns:
            Tuple[bool, str]: (success, processed_result)
        """
        try:
            logger.info(f"Processing result: {len(result)} characters")

            cleaned_result = clean_json_response(result)

            # Validate and format JSON
            is_valid, final_output = validate_and_format_json(cleaned_result)

            if is_valid:
                logger.info("Result is valid JSON - cleaned and formatted")
            else:
                logger.warning("Result is not valid JSON after cleanup - saving as-is")
                final_output = cleaned_result

            return True, final_output

        except Exception as e:
            logger.error(f"Error processing result: {e}")
            return False, result

    def _save_result(self, result: str) -> bool:
        """
        Save the analysis result to output file.

        Args:
            result: Processed analysis result

        Returns:
            bool: True if successful
        """
        try:
            success = write_file(self.config.output_file, result)
            if success:
                logger.info(f"Results saved to {self.config.output_file}")
            return success
        except Exception as e:
            logger.error(f"Error saving result: {e}")
            return False

    def analyze_diff(self, system_prompt: str, user_message: str) -> Optional[List[Dict[str, Any]]]:
        """
        Analyze diff content using LLM with iterative tool support.

        Args:
            system_prompt: System prompt for diff analysis
            user_message: User message containing diff content and context

        Returns:
            List of analysis results or None on error
        """
        logger.info("Starting diff analysis with tool support...")
        start_time = time.time()

        # Start conversation tracking
        context_info = f"Git diff analysis: {self.config.repo_path}"
        self.claude.start_conversation("diff_analysis", context_info)

        try:
            # Check token limits
            if not self.claude.check_token_limit(system_prompt, user_message):
                total_chars = len(system_prompt + user_message)
                estimated_tokens = self.claude.estimate_tokens(system_prompt + user_message)
                max_input_tokens = self.claude.config.max_tokens - 5000

                logger.error(f"CRITICAL: Input exceeds token limits - ABORTING analysis to prevent API error")
                logger.error(f"System prompt: {len(system_prompt):,} chars")
                logger.error(f"User message: {len(user_message):,} chars")
                logger.error(f"Total content: {total_chars:,} chars")
                logger.error(f"Estimated tokens: {estimated_tokens:,}")
                logger.error(f"Max input tokens allowed: {max_input_tokens:,}")
                logger.error(f"Model limit: {self.claude.config.max_tokens:,}")
                
                self._log_final_token_summary()
                return None

            logger.info(f"Token limit check passed - estimated tokens: {self.claude.estimate_tokens(system_prompt + user_message):,}")

            # Run iterative analysis with tool usage
            analysis_result = self._run_iterative_diff_analysis(system_prompt, user_message)

            # Calculate execution time
            end_time = time.time()
            execution_time = end_time - start_time

            if analysis_result:
                # Parse JSON response using the same approach as code_analysis.py
                try:
                    # Use the shared _process_analysis_result method for consistent JSON handling
                    success, processed_result = self._process_analysis_result(analysis_result)
                    
                    if not success:
                        logger.error("Failed to process LLM response")
                        self._log_final_token_summary()
                        return None
                    
                    # Parse the processed result
                    issues = json.loads(processed_result)
                    
                    if not isinstance(issues, list):
                        if isinstance(issues, dict) and 'results' in issues:
                            issues = issues['results']
                        elif isinstance(issues, dict):
                            logger.warning("Expected list of issues, got dict; wrapping in list")
                            issues = [issues]
                        else:
                            logger.error(f"Expected list of issues, got {type(issues)}")
                            self._log_final_token_summary()
                            return None

                    # Validate that all items in the list are dictionaries (issue objects)
                    valid_issues = []
                    for i, issue in enumerate(issues):
                        if isinstance(issue, dict):
                            valid_issues.append(issue)
                        else:
                            logger.warning(f"Issue at index {i} is not a dictionary (got {type(issue)}): {issue}")
                            logger.warning("Skipping invalid issue - this may indicate an LLM response parsing problem")
                    
                    if len(valid_issues) != len(issues):
                        logger.warning(f"Filtered out {len(issues) - len(valid_issues)} invalid issues from LLM response")
                        logger.warning("This suggests the LLM returned non-dictionary objects in the issues list")
                    
                    issues = valid_issues

                    logger.info(f"LLM analysis found {len(issues)} potential issues")
                    logger.info(f"Total time taken: {execution_time:.2f} seconds")
                    
                    # Log complete conversation
                    self.claude.log_complete_conversation(
                        final_result=processed_result
                    )
                    
                    # Log tool usage summary
                    self.tools.log_tool_usage_summary()
                    self._log_final_token_summary()
                    
                    return issues

                except json.JSONDecodeError as e:
                    logger.error(f"Failed to parse processed LLM response as JSON: {e}")
                    logger.error(f"Processed result: {processed_result if 'processed_result' in locals() else 'N/A'}")
                    logger.error(f"Original response text: {analysis_result}")
                    
                    # Log conversation even for JSON parsing errors
                    self.claude.log_complete_conversation(
                        final_result=f"JSON parsing failed: {e}"
                    )
                    
                    self._log_final_token_summary()
                    return None
            else:
                logger.error("Analysis failed - no result from iterative analysis")
                self._log_final_token_summary()
                return None

        except Exception as e:
            logger.error(f"Error during diff analysis: {e}")
            
            # Log conversation even for unexpected errors
            self.claude.log_complete_conversation(
                final_result=f"Unexpected error during diff analysis: {e}"
            )
            
            self._log_final_token_summary()
            return None

    def run_analysis(self, system_prompt: str, user_message: str) -> bool:
        """
        Run the complete diff analysis process.

        Args:
            system_prompt: System prompt for analysis
            user_message: User message with diff content

        Returns:
            bool: True if analysis completed successfully
        """
        logger.info("Starting diff analysis process...")

        try:
            # Run the analysis
            issues = self.analyze_diff(system_prompt, user_message)

            if issues is not None:
                # Convert issues to JSON string for saving
                result_json = json.dumps(issues, indent=2, ensure_ascii=False)
                
                # Process and clean the result
                success, processed_result = self._process_analysis_result(result_json)

                if success:
                    # Save result
                    save_success = self._save_result(processed_result)

                    if save_success:
                        logger.info("Diff analysis completed successfully!")
                        return True
                    else:
                        logger.error("Failed to save results")
                        return False
                else:
                    logger.error("Failed to process results")
                    return False
            else:
                logger.error("Analysis failed - no result from LLM")
                return False

        except Exception as e:
            logger.error(f"Unexpected error during diff analysis: {e}")
            return False

    def _run_iterative_diff_analysis(self, system_prompt: str, user_prompt: str) -> Optional[str]:
        """
        Run iterative diff analysis using the unified run_iterative_analysis() method.
        
        This method now delegates to the shared implementation in llm.py, which handles:
        - Complete conversation history maintenance
        - Structured tool calling with proper multi-result handling
        - Provider-agnostic response format detection
        - Token usage tracking
        
        Args:
            system_prompt: System prompt for analysis
            user_prompt: Initial user prompt
            
        Returns:
            str: Final analysis result or None on error
        """
        logger.info(f"Starting iterative diff analysis using unified method (max {MAX_TOOL_ITERATIONS} iterations)")

        # Define diff-specific contextual guidance template
        # This ensures the LLM stays focused on diff analysis and JSON output requirements
        context_guidance_template = """
Based on the tool results above, please continue your diff analysis and provide your final analysis in the required JSON format.

🔥 CRITICAL JSON OUTPUT REQUIREMENTS - ABSOLUTE REQUIREMENT:
- You MUST respond with ONLY a JSON array of issue objects
- Your response MUST start with `[` and end with `]`
- NO markdown code blocks (no ```json)
- NO explanatory text before or after the JSON
- NO prose like "Here are the issues I found"
- If no issues found, return exactly: []

FORBIDDEN: Any text before or after the JSON array will cause system failure.

Remember to:
1. Analyze the information provided by the tools
2. Focus on the git diff changes (+ lines) for potential issues
3. Use the tool results to understand context and identify problems
4. Each issue must follow the exact JSON schema from the system prompt

Original diff analysis request: {user_prompt}

YOUR ENTIRE RESPONSE MUST BE VALID JSON - START WITH [ AND END WITH ]
"""

        # Use the unified iterative analysis method from llm.py
        # This ensures consistent tool handling across all analyzers
        analysis_result = self.claude.run_iterative_analysis(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            tools_executor=self,  # Pass self so tools can be accessed via self.tools
            supported_tools=[
                "readFile", "runTerminalCmd", "getSummaryOfFile",
                "inspectDirectoryHierarchy", "list_files",
                "getFileContentByLines", "getFileContent", "checkFileSize"
            ],
            context_guidance_template=context_guidance_template,
            max_iterations=MAX_TOOL_ITERATIONS,
            token_usage_callback=self._extract_and_log_token_usage
        )

        return analysis_result

    def _execute_tool_use(self, tool_use: Dict[str, Any]) -> str:
        """
        Execute a tool_use using the centralized orchestrator.

        Args:
            tool_use: Tool_use block from API response

        Returns:
            str: Tool execution result
        """
        # Use the centralized tool orchestrator from Tools class
        return self.tools.execute_tool_use(tool_use)

    def analyze_function_diff(self, function_prompt_data: Dict[str, Any]) -> Optional[List[Dict[str, Any]]]:
        """
        Analyze a single function in the context of a diff.
        
        This method is used for function-level diff analysis, where each affected
        function is analyzed individually with its call context.
        
        Args:
            function_prompt_data: Dict containing:
                - function: str - function name
                - file_path: str - file path
                - code: str - function code with line numbers and +/- markers
                - changed_lines: List[int] - which lines in function changed
                - data_types_used: List[str] - data types used by this function
                - constants_used: Dict - constants used by this function
                - invoked_functions: List[Dict] - functions this function calls
                - invoking_functions: List[Dict] - functions that call this function
                - diff_context: Dict - wider change context
                
        Returns:
            List of analysis results or None on error
        """
        logger.info(f"Starting function-level diff analysis for: {function_prompt_data.get('function', 'unknown')}")
        start_time = time.time()
        
        # Load the function-level diff analysis prompt
        try:
            prompt_path = Path(__file__).parent.parent / "prompts" / "functionDiffAnalysisPrompt.md"
            with open(prompt_path, 'r', encoding='utf-8') as f:
                system_prompt = f.read()
        except Exception as e:
            logger.error(f"Failed to load function diff analysis prompt: {e}")
            return None
        
        # Build the user message with function context
        user_message = self._build_function_analysis_user_message(function_prompt_data)
        
        # Start conversation tracking
        context_info = f"Function-level diff analysis: {function_prompt_data.get('function', 'unknown')}"
        self.claude.start_conversation("function_diff_analysis", context_info)
        
        try:
            # Check token limits
            if not self.claude.check_token_limit(system_prompt, user_message):
                total_chars = len(system_prompt + user_message)
                estimated_tokens = self.claude.estimate_tokens(system_prompt + user_message)
                
                logger.error(f"CRITICAL: Input exceeds token limits for function analysis")
                logger.error(f"Function: {function_prompt_data.get('function', 'unknown')}")
                logger.error(f"Total content: {total_chars:,} chars, Estimated tokens: {estimated_tokens:,}")
                
                self._log_final_token_summary()
                return None
            
            logger.info(f"Token limit check passed - estimated tokens: {self.claude.estimate_tokens(system_prompt + user_message):,}")
            
            # Run iterative analysis with tool usage
            analysis_result = self._run_iterative_function_analysis(system_prompt, user_message)
            
            # Calculate execution time
            end_time = time.time()
            execution_time = end_time - start_time
            
            if analysis_result:
                try:
                    # Process the result
                    success, processed_result = self._process_analysis_result(analysis_result)
                    
                    if not success:
                        logger.error("Failed to process function analysis LLM response")
                        self._log_final_token_summary()
                        return None
                    
                    # Parse the processed result
                    issues = json.loads(processed_result)
                    
                    if not isinstance(issues, list):
                        if isinstance(issues, dict) and 'results' in issues:
                            issues = issues['results']
                        elif isinstance(issues, dict):
                            logger.warning("Expected list of issues, got dict; wrapping in list")
                            issues = [issues]
                        else:
                            logger.error(f"Expected list of issues, got {type(issues)}")
                            self._log_final_token_summary()
                            return None

                    # Validate issues
                    valid_issues = [issue for issue in issues if isinstance(issue, dict)]
                    
                    if len(valid_issues) != len(issues):
                        logger.warning(f"Filtered out {len(issues) - len(valid_issues)} invalid issues")
                    
                    logger.info(f"Function analysis found {len(valid_issues)} potential issues in {execution_time:.2f}s")
                    
                    # Log complete conversation
                    self.claude.log_complete_conversation(final_result=processed_result)
                    
                    # Log tool usage summary
                    self.tools.log_tool_usage_summary()
                    self._log_final_token_summary()
                    
                    return valid_issues
                    
                except json.JSONDecodeError as e:
                    logger.error(f"Failed to parse function analysis response as JSON: {e}")
                    self.claude.log_complete_conversation(final_result=f"JSON parsing failed: {e}")
                    self._log_final_token_summary()
                    return None
            else:
                logger.error("Function analysis failed - no result from iterative analysis")
                self._log_final_token_summary()
                return None
                
        except Exception as e:
            logger.error(f"Error during function diff analysis: {e}")
            self.claude.log_complete_conversation(final_result=f"Unexpected error: {e}")
            self._log_final_token_summary()
            return None

    def _build_function_analysis_user_message(self, function_prompt_data: Dict[str, Any]) -> str:
        """
        Build the user message for function-level diff analysis.
        
        Args:
            function_prompt_data: Function analysis data
            
        Returns:
            Formatted user message string
        """
        func_name = function_prompt_data.get('function', 'unknown')
        file_path = function_prompt_data.get('file_path', 'unknown')
        code = function_prompt_data.get('code', '')
        changed_lines = function_prompt_data.get('changed_lines', [])
        affected_reason = function_prompt_data.get('affected_reason', 'modified')
        
        # Data types and constants
        data_types_used = function_prompt_data.get('data_types_used', [])
        constants_used = function_prompt_data.get('constants_used', {})
        
        # Related functions
        invoked_functions = function_prompt_data.get('invoked_functions', [])
        invoking_functions = function_prompt_data.get('invoking_functions', [])
        
        # Diff context
        diff_context = function_prompt_data.get('diff_context', {})
        all_changed_files = diff_context.get('all_changed_files', [])
        
        # Build the message
        message_parts = []
        
        # Header
        message_parts.append(f"## Function Being Analyzed\n")
        message_parts.append(f"**Function**: `{func_name}`")
        message_parts.append(f"**File**: `{file_path}`")
        message_parts.append(f"**Affected Reason**: {affected_reason}")
        if changed_lines:
            message_parts.append(f"**Changed Lines**: {', '.join(map(str, changed_lines))}")
        message_parts.append("")
        
        # Function code
        message_parts.append("### Function Code")
        message_parts.append("```")
        message_parts.append(code)
        message_parts.append("```")
        message_parts.append("")
        
        # Data types used
        if data_types_used:
            message_parts.append("## Data Types Used")
            message_parts.append("The following data types are used by this function:")
            for dt in data_types_used:
                message_parts.append(f"- `{dt}`")
            message_parts.append("")
        
        # Constants used
        if constants_used:
            message_parts.append("## Constants Used")
            message_parts.append("The following constants are used by this function:")
            for const_name, const_value in constants_used.items():
                message_parts.append(f"- `{const_name}`: {const_value}")
            message_parts.append("")
        
        # Functions this function calls
        if invoked_functions:
            message_parts.append("## Functions Called by This Function")
            message_parts.append("**Note**: All invoked functions are shown. [MODIFIED] indicates the function was changed in this diff.")
            message_parts.append("")
            for func in invoked_functions:
                status = "[MODIFIED]" if func.get('is_modified', False) else "[UNCHANGED]"
                func_file = func.get('file', 'unknown')
                func_start = func.get('start', '?')
                func_end = func.get('end', '?')
                message_parts.append(f"### {func.get('name', 'unknown')} ({func_file}:{func_start}-{func_end}) {status}")
                if func.get('code'):
                    message_parts.append("```")
                    message_parts.append(func.get('code', ''))
                    message_parts.append("```")
                message_parts.append("")
        
        # Functions that call this function
        if invoking_functions:
            message_parts.append("## Functions That Call This Function")
            message_parts.append("**Note**: All invoking functions are shown. [MODIFIED] indicates the function was changed in this diff.")
            message_parts.append("")
            for func in invoking_functions:
                status = "[MODIFIED]" if func.get('is_modified', False) else "[UNCHANGED]"
                func_file = func.get('file', 'unknown')
                func_start = func.get('start', '?')
                func_end = func.get('end', '?')
                message_parts.append(f"### {func.get('name', 'unknown')} ({func_file}:{func_start}-{func_end}) {status}")
                if func.get('code'):
                    message_parts.append("```")
                    message_parts.append(func.get('code', ''))
                    message_parts.append("```")
                message_parts.append("")
        
        # Wider change context
        if all_changed_files:
            message_parts.append("## Wider Change Context")
            message_parts.append(f"This function is part of a commit that modifies {len(all_changed_files)} files:")
            for f in all_changed_files:
                marker = "(this file)" if f == file_path else ""
                message_parts.append(f"- `{f}` {marker}")
            message_parts.append("")
        
        # Analysis instructions
        message_parts.append("## Analysis Instructions")
        message_parts.append("")
        message_parts.append("1. Focus on the changed lines (marked with + or -)")
        message_parts.append("2. Consider how changes affect the function's behavior")
        message_parts.append("3. Check if changes are consistent with related functions")
        message_parts.append("4. Report issues ONLY on changed lines when possible")
        message_parts.append("")
        message_parts.append("🎯 **IMPORTANT**: When reporting line numbers, focus on the actually changed lines (lines with + prefix) to ensure your findings can be properly commented on in the pull request.")
        message_parts.append("")
        message_parts.append("🔥 **CRITICAL JSON OUTPUT REMINDER**: Your final response MUST be a valid JSON array starting with `[` and ending with `]`. No markdown, no explanatory text - ONLY the JSON array.")
        
        return "\n".join(message_parts)

    def _run_iterative_function_analysis(self, system_prompt: str, user_prompt: str) -> Optional[str]:
        """
        Run iterative function-level analysis using the unified run_iterative_analysis() method.
        
        Args:
            system_prompt: System prompt for analysis
            user_prompt: Initial user prompt with function context
            
        Returns:
            str: Final analysis result or None on error
        """
        logger.info(f"Starting iterative function analysis using unified method (max {MAX_TOOL_ITERATIONS} iterations)")
        
        # Define function-specific contextual guidance template
        context_guidance_template = """
Based on the tool results above, please continue your function-level diff analysis and provide your final analysis in the required JSON format.

🔥 CRITICAL JSON OUTPUT REQUIREMENTS - ABSOLUTE REQUIREMENT:
- You MUST respond with ONLY a JSON array of issue objects
- Your response MUST start with `[` and end with `]`
- NO markdown code blocks (no ```json)
- NO explanatory text before or after the JSON
- NO prose like "Here are the issues I found"
- If no issues found, return exactly: []

FORBIDDEN: Any text before or after the JSON array will cause system failure.

Remember to:
1. Focus on the specific function being analyzed
2. Consider the function's relationship with callers and callees
3. Report issues on changed lines (+ prefix) when possible
4. Each issue must follow the exact JSON schema from the system prompt

Original function analysis request: {user_prompt}

YOUR ENTIRE RESPONSE MUST BE VALID JSON - START WITH [ AND END WITH ]
"""
        
        # Use the unified iterative analysis method from llm.py
        analysis_result = self.claude.run_iterative_analysis(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            tools_executor=self,
            supported_tools=[
                "readFile", "runTerminalCmd", "getSummaryOfFile",
                "inspectDirectoryHierarchy", "list_files",
                "getFileContentByLines", "getFileContent", "checkFileSize"
            ],
            context_guidance_template=context_guidance_template,
            max_iterations=MAX_TOOL_ITERATIONS,
            token_usage_callback=self._extract_and_log_token_usage
        )

        return analysis_result

    def run_diff_context_collection(self, prompt_data: dict) -> Optional[dict]:
        """
        Diff Context Collection: Collect code context for diff analysis.

        Uses DiffContextAnalyzer for stage-isolated JSON extraction that correctly
        identifies dict with 'changed_functions' key (not the last valid JSON).

        Args:
            prompt_data: Dict containing function, file_path, code, changed_lines,
                         data_types_used, constants_used, invoked_functions,
                         invoking_functions, diff_context
        Returns:
            Diff context bundle dict on success, None on failure
        """
        func_name = prompt_data.get('function', 'unknown')
        file_path = prompt_data.get('file_path', 'unknown')
        logger.info(f"Diff Context Collection: Starting for {func_name}")
        start_time = time.time()

        # Check for existing diff context bundle on disk
        try:
            output_provider = get_output_directory_provider()
            diff_bundles_dir = f"{output_provider.get_repo_artifacts_dir()}/diff_context_bundles"
            func_hash = hashlib.md5(f"{func_name}@{file_path}".encode()).hexdigest()[:8]
            bundle_path = f"{diff_bundles_dir}/{func_hash}.json"

            if os.path.exists(bundle_path):
                logger.info(f"Diff Context Collection: Found existing bundle at {bundle_path}")
                with open(bundle_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
        except Exception as e:
            logger.warning(f"Diff Context Collection: Could not check for existing bundle: {e}")
            bundle_path = None
            diff_bundles_dir = None

        # Load diff context collection prompt
        try:
            prompt_path = Path(__file__).parent.parent / "prompts" / "diffContextCollectionProcess.md"
            with open(prompt_path, 'r', encoding='utf-8') as f:
                system_prompt = f.read()
        except Exception as e:
            logger.error(f"Diff Context Collection: Failed to load prompt: {e}")
            return None

        # Build user message from prompt_data
        user_message = self._build_function_analysis_user_message(prompt_data)
        user_message += "\n\nCollect all context needed for this function diff and return a JSON diff context bundle as described in the system prompt."

        # Start conversation tracking
        self.claude.start_conversation("diff_context_collection", f"{func_name} in {file_path}")

        try:
            if not self.claude.check_token_limit(system_prompt, user_message):
                logger.error(f"Diff Context Collection: Input exceeds token limits for {func_name}")
                return None

            available_tools = [
                "readFile", "runTerminalCmd", "getSummaryOfFile",
                "inspectDirectoryHierarchy", "list_files", "getFileContentByLines",
                "getFileContent", "checkFileSize"
            ]

            # Use DiffContextAnalyzer for stage-isolated JSON extraction
            # This ensures we find dict with 'changed_functions' key, not the last valid JSON
            analyzer = DiffContextAnalyzer(
                claude=self.claude,
                tools_executor=self,
                supported_tools=available_tools,
                max_iterations=20,
                token_usage_callback=self._extract_and_log_token_usage
            )

            diff_context_bundle_str = analyzer.run_iterative_analysis(
                system_prompt=system_prompt,
                user_prompt=user_message
            )

            end_time = time.time()
            logger.info(f"Diff Context Collection: Completed in {end_time - start_time:.2f}s")

            if diff_context_bundle_str is None:
                logger.error(f"Diff Context Collection: No result from LLM for {func_name}")
                return None

            # Parse the JSON string into a dict
            try:
                diff_context_bundle = json.loads(diff_context_bundle_str)
                if not isinstance(diff_context_bundle, dict):
                    logger.error(f"Diff Context Collection: Expected dict, got {type(diff_context_bundle)}")
                    return None
            except json.JSONDecodeError as e:
                logger.error(f"Diff Context Collection: Invalid JSON: {e}")
                return None

            # Save diff context bundle to disk
            if diff_bundles_dir and bundle_path:
                try:
                    os.makedirs(diff_bundles_dir, exist_ok=True)
                    with open(bundle_path, 'w', encoding='utf-8') as f:
                        json.dump(diff_context_bundle, f, indent=2, ensure_ascii=False)
                    logger.info(f"Diff Context Collection: Bundle saved to {bundle_path}")
                except Exception as e:
                    logger.warning(f"Diff Context Collection: Could not save bundle: {e}")

            self.claude.log_complete_conversation(final_result=json.dumps(diff_context_bundle))
            return diff_context_bundle

        except Exception as e:
            logger.error(f"Diff Context Collection: Unexpected error: {e}")
            self.claude.log_complete_conversation(final_result=f"Error: {e}")
            return None

    def run_diff_analysis_from_context(self, diff_context_bundle: dict) -> Optional[List[Dict[str, Any]]]:
        """
        Diff Analysis: Perform diff analysis from the gathered diff context bundle.

        Uses DiffAnalysisAnalyzer for stage-isolated JSON extraction that correctly
        identifies array of issue dicts (not array of strings like collection_notes).

        Args:
            diff_context_bundle: Diff context bundle from Diff Context Collection

        Returns:
            List of issue dicts on success, None on failure
        """
        func_name = diff_context_bundle.get("primary_function", {}).get("name", "unknown")
        file_path = diff_context_bundle.get("primary_function", {}).get("file_path", "unknown")
        logger.info(f"Diff Analysis: Starting for {func_name}")
        start_time = time.time()

        # Load diff analysis prompt
        try:
            prompt_path = Path(__file__).parent.parent / "prompts" / "diffAnalysisProcess.md"
            with open(prompt_path, 'r', encoding='utf-8') as f:
                system_prompt = f.read()
        except Exception as e:
            logger.error(f"Diff Analysis: Failed to load prompt: {e}")
            return None

        # Build user message: context bundle + output schema
        user_message = f"## Diff Context Bundle for Analysis\n\n"
        user_message += f"**Function**: `{func_name}` in `{file_path}`\n\n"
        user_message += "The following diff context bundle contains all code needed for your analysis.\n\n"
        user_message += "```json\n"
        user_message += json.dumps(diff_context_bundle, indent=2, ensure_ascii=False)
        user_message += "\n```\n\n"
        user_message += "Analyze the changed lines (marked with +) and return a JSON array of issues.\n\n"
        user_message += "🔥 CRITICAL: Return ONLY a valid JSON array starting with [ and ending with ]. If no issues, return []."

        # Start conversation tracking
        self.claude.start_conversation("diff_analysis", f"{func_name} in {file_path}")

        try:
            stage_b_tools = ["readFile", "runTerminalCmd"]

            # Use DiffAnalysisAnalyzer for stage-isolated JSON extraction
            # This ensures we find array of issue dicts, not array of strings
            analyzer = DiffAnalysisAnalyzer(
                claude=self.claude,
                tools_executor=self,
                supported_tools=stage_b_tools,
                max_iterations=15,
                token_usage_callback=self._extract_and_log_token_usage
            )

            issues_str = analyzer.run_iterative_analysis(
                system_prompt=system_prompt,
                user_prompt=user_message
            )

            end_time = time.time()
            logger.info(f"Diff Analysis: Completed in {end_time - start_time:.2f}s")

            if issues_str is None:
                logger.error(f"Diff Analysis: No result for {func_name}")
                return None

            # Parse the JSON string into a list
            try:
                issues = json.loads(issues_str)
                if not isinstance(issues, list):
                    logger.error(f"Diff Analysis: Expected list, got {type(issues)}")
                    return None
            except json.JSONDecodeError as e:
                logger.error(f"Diff Analysis: Invalid JSON: {e}")
                return None

            self.claude.log_complete_conversation(final_result=json.dumps(issues))
            self.tools.log_tool_usage_summary()
            self._log_final_token_summary()

            logger.info(f"Diff Analysis: Found {len(issues)} issues for {func_name}")
            return issues

        except Exception as e:
            logger.error(f"Diff Analysis: Unexpected error: {e}")
            self.claude.log_complete_conversation(final_result=f"Error: {e}")
            return None