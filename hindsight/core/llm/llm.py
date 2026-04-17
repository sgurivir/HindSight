#!/usr/bin/env python3
# Created by Sridhar Gurivireddy
"""
LLM Client Module
Handles communication with AWS Bedrock for code analysis

TOOL INVOCATION ARCHITECTURE:
==============================
This module uses JSON-embedded tool requests for tool invocation.
Structured/native tool calls are never used.

1. Tool Request Format (JSON-Embedded):
   - System prompts describe tools as JSON objects
   - LLM returns: ```json {"tool": "readFile", "path": "file.py", "reason": "..."} ```
   - _extract_json_tool_requests() extracts these JSON requests using regex

2. Tool Result Format (Plain Text):
   - Format: {"role": "user", "content": "[TOOL_RESULT: tool_id]\nresult"}

3. Tool Execution Flow:
   - run_iterative_analysis() manages the conversation loop
   - Detects JSON tool requests in LLM response text via _extract_json_tool_requests()
   - Executes tools via _execute_json_tool_request()
   - Returns results to LLM in next iteration using plain text format
   - Continues until analysis is complete
"""

import json
import os
import re
import shutil
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Dict, Any, List, Callable

from .providers.base_provider import BaseLLMProvider, LLMConfig
from .providers.aws_bedrock_provider import AWSBedrockProvider
from ...utils.file_util import write_file, ensure_directory_exists
from ...utils.json_util import clean_json_response
from ...utils.log_util import get_logger
from ...utils.output_directory_provider import get_output_directory_provider

logger = get_logger(__name__)

# Constants
DEFAULT_MAX_RETRIES = 3
DEFAULT_RETRY_DELAYS = [30, 60, 90]  # Wait times in seconds for each retry


class ConversationState:
    """
    Manages conversation state and history for proper MCP implementation.
    Ensures that full conversation history is maintained across tool interactions.

    Tool results are added as plain text user messages:
    Format: {"role": "user", "content": "[TOOL_RESULT: tool_id]\nresult"}
    """

    def __init__(self):
        self.messages = []
        self.system_prompt = None
        self.original_request = None

    def set_system_prompt(self, system_prompt: str):
        """Set the system prompt for this conversation."""
        self.system_prompt = system_prompt

    def set_original_request(self, request: str):
        """Set the original user request for context preservation."""
        self.original_request = request

    def add_user_message(self, content: str):
        """Add a user message to the conversation history."""
        self.messages.append({"role": "user", "content": content})

    def add_assistant_message(self, content: Any):
        """Add an assistant message to the conversation history."""
        self.messages.append({"role": "assistant", "content": content})

    def add_tool_result(self, tool_use_id: str, result: str):
        """Add a tool result to the conversation history as a plain text user message."""
        tool_result_message = f"[TOOL_RESULT: {tool_use_id}]\n{result}"
        self.messages.append({
            "role": "user",
            "content": tool_result_message
        })

    def get_full_conversation(self) -> List[Dict]:
        """Get the complete conversation history."""
        return self.messages.copy()

    def get_conversation_with_context(self, additional_context: str = None) -> List[Dict]:
        """
        Get conversation history with additional context for tool results.
        This helps Claude understand what to do with tool results.
        """
        messages = self.messages.copy()

        # If we have tool results and additional context, add contextual guidance
        if additional_context and self.original_request:
            contextual_message = f"""
{additional_context}

Original analysis request: {self.original_request}

Please continue your analysis based on the tool results above.
"""
            messages.append({"role": "user", "content": contextual_message})

        return messages

    def clear(self):
        """Clear the conversation state."""
        self.messages = []
        self.system_prompt = None
        self.original_request = None


@dataclass
class ClaudeConfig:
    """Configuration for LLM client"""
    api_key: str
    api_url: str
    model: str
    max_tokens: int = 64000
    temperature: float = 0.05
    timeout: int = 300
    provider_type: str = "aws_bedrock"


def create_llm_provider(config: ClaudeConfig) -> BaseLLMProvider:
    """
    Create an AWSBedrockProvider from the given configuration.

    Args:
        config: LLM configuration

    Returns:
        AWSBedrockProvider: Configured provider instance
    """
    llm_config = LLMConfig(
        api_key=config.api_key,
        api_url=config.api_url,
        model=config.model,
        max_tokens=config.max_tokens,
        temperature=config.temperature,
        timeout=config.timeout
    )
    logger.info(f"Creating AWS Bedrock provider for model: {config.model}")
    return AWSBedrockProvider(llm_config)


class Claude:
    """
    Claude API client for code analysis.
    Handles communication with Claude API and manages conversation flow.
    """

    # Class variable to track conversation count across all instances
    _conversation_counter = 0
    _prompts_dir = None
    _errors_dir = None
    
    # NEW: Track current issue directory for numbered subdirectory logging
    _current_issue_number = None
    _current_issue_dir = None

    def __init__(self, config: ClaudeConfig):
        """
        Initialize Claude API client.

        Args:
            config: Claude configuration
        """
        self.config = config
        self.provider = create_llm_provider(config)

        # Instance variables for conversation tracking
        self.conversation_messages = []
        self.conversation_responses = []
        self.conversation_metadata = {}

        logger.info(f"Initialized Claude client with model: {config.model}")

    @classmethod
    def setup_prompts_logging(cls) -> None:
        """
        Setup conversation logging directory.
        Uses the OutputDirectoryProvider singleton for directory configuration.
        Does NOT clear existing prompts - use clear_older_prompts() explicitly for that.
        """
        output_provider = get_output_directory_provider()
        artifacts_dir = output_provider.get_repo_artifacts_dir()
        final_output_dir = artifacts_dir

        new_prompts_dir = os.path.join(final_output_dir, "prompts_sent")

        # Create errors directory under results/
        results_dir = os.path.join(final_output_dir, "results")
        ensure_directory_exists(results_dir)
        new_errors_dir = os.path.join(results_dir, "errors")

        # Check if we're already set up for this directory
        if (cls._prompts_dir == new_prompts_dir and
            cls._errors_dir == new_errors_dir and
            os.path.exists(cls._prompts_dir)):
            logger.debug(f"Prompts logging already set up for: {cls._prompts_dir}")
            return

        # Update class variables
        cls._prompts_dir = new_prompts_dir
        cls._errors_dir = new_errors_dir

        # Create prompts directory if it doesn't exist (but don't clear existing content)
        ensure_directory_exists(cls._prompts_dir)
        logger.info(f"Setup conversation logging directory: {cls._prompts_dir}")

        # Create errors directory under results/
        ensure_directory_exists(cls._errors_dir)
        logger.info(f"Setup errors logging directory: {cls._errors_dir}")

    @classmethod
    def clear_older_prompts(cls) -> None:
        """
        Clear existing prompts directory and reset conversation counter.
        This should be called explicitly at the beginning of analysis runs.
        """
        if not cls._prompts_dir:
            logger.warning("Prompts directory not set up, cannot clear")
            return

        # Reset conversation counter and issue tracking
        cls._conversation_counter = 0
        cls._current_issue_number = None
        cls._current_issue_dir = None
        
        # Clear existing prompts directory if it exists
        if os.path.exists(cls._prompts_dir):
            shutil.rmtree(cls._prompts_dir)
            logger.info(f"Cleared existing prompts directory: {cls._prompts_dir}")
            
            # Recreate the directory
            ensure_directory_exists(cls._prompts_dir)
            logger.info(f"Recreated prompts directory: {cls._prompts_dir}")
        else:
            logger.debug(f"Prompts directory does not exist, nothing to clear: {cls._prompts_dir}")

    @classmethod
    def start_issue_logging(cls, issue_number: int) -> None:
        """
        Start logging prompts for a specific issue in a numbered subdirectory.
        
        This creates a numbered subdirectory (e.g., prompts_sent/1/, prompts_sent/2/)
        for each analyzed function/issue, preventing prompts from being overwritten
        when analyzing multiple functions.
        
        Args:
            issue_number: The issue/function number (1-based)
        """
        if not cls._prompts_dir:
            logger.warning("Prompts directory not set up, cannot start issue logging")
            return
            
        cls._current_issue_number = issue_number
        cls._current_issue_dir = os.path.join(cls._prompts_dir, str(issue_number))
        
        # Create the numbered subdirectory
        ensure_directory_exists(cls._current_issue_dir)
        
        # Reset conversation counter for this issue
        cls._conversation_counter = 0
        
        logger.info(f"Started issue logging in: {cls._current_issue_dir}")
    
    @classmethod
    def end_issue_logging(cls) -> None:
        """
        End logging for the current issue and reset to root prompts directory.
        
        This should be called after completing analysis of a function/issue
        to ensure subsequent prompts go to the correct location.
        """
        if cls._current_issue_number is not None:
            logger.info(f"Ended issue logging for issue {cls._current_issue_number}")
        cls._current_issue_number = None
        cls._current_issue_dir = None

    def start_conversation(self, analysis_type: str = "unknown", context_info: str = ""):
        """
        Start a new conversation and initialize tracking.

        Args:
            analysis_type: Type of analysis (e.g., "code_analysis", "trace_analysis")
            context_info: Additional context information (e.g., file name, function name)
        """
        self.conversation_messages = []
        self.conversation_responses = []
        self.conversation_metadata = {
            'analysis_type': analysis_type,
            'context_info': context_info,
            'start_time': time.strftime('%Y-%m-%d %H:%M:%S'),
            'model': self.config.model,
            'max_tokens': self.config.max_tokens,
            'temperature': self.config.temperature
        }
        logger.debug(f"Started new conversation for {analysis_type}: {context_info}")

    def log_complete_conversation(self, final_result: str = None, double_check_info: str = None) -> str:
        """
        Log the complete conversation to a single markdown file.
        Accurately represents the exact communication paradigm between code and LLM.

        Args:
            final_result: The final analysis result
            double_check_info: Information about double-check validation if performed

        Returns:
            str: Path to the conversation file
        """
        if not self._prompts_dir:
            logger.warning("Conversation logging not setup")
            return None

        self.__class__._conversation_counter += 1
        analysis_type = self.conversation_metadata.get('analysis_type', 'unknown')
        safe_type = analysis_type.replace(' ', '_').replace('/', '_').replace('\\', '_')
        conversation_filename = f"step{self._conversation_counter}_{safe_type}.md"
        
        # Use issue-specific directory if set, otherwise use root prompts directory
        target_dir = self._current_issue_dir if self._current_issue_dir else self._prompts_dir
        conversation_path = os.path.join(target_dir, conversation_filename)

        # Build conversation content
        conversation_content = f"# CONVERSATION {self._conversation_counter}\n\n"
        conversation_content += f"**Analysis Type:** {self.conversation_metadata.get('analysis_type', 'unknown')}\n"
        conversation_content += f"**Context:** {self.conversation_metadata.get('context_info', 'N/A')}\n"
        conversation_content += f"**Start Time:** {self.conversation_metadata.get('start_time', 'unknown')}\n"
        conversation_content += f"**Model:** {self.conversation_metadata.get('model', 'unknown')}\n"
        conversation_content += f"**Max Tokens:** {self.conversation_metadata.get('max_tokens', 'unknown')}\n"
        conversation_content += f"**Temperature:** {self.conversation_metadata.get('temperature', 'unknown')}\n\n"

        conversation_content += "---\n\n"

        # Log all conversation turns exactly as they were sent to the API
        for i, (messages, response) in enumerate(zip(self.conversation_messages, self.conversation_responses), 1):
            conversation_content += f"## Turn {i}\n\n"

            # Log all messages in this turn exactly as they were sent
            for j, message in enumerate(messages, 1):
                role = message.get('role', 'unknown')
                content = message.get('content', '')
                cache_control = message.get('cache_control')

                # Handle both string content and list content (content blocks)
                # Check if content is list-like or string-like
                is_list_content = hasattr(content, '__iter__') and not hasattr(content, 'strip')
                is_string_content = hasattr(content, 'strip')
                
                if is_list_content:
                    # Content blocks format - convert to readable string
                    formatted_content = self._format_content_blocks(content)
                elif is_string_content:
                    # Convert literal \n characters to actual line breaks for better markdown readability
                    formatted_content = content.replace('\\n', '\n')
                else:
                    # Fallback for other types
                    formatted_content = str(content)

                conversation_content += f"### Message {j} ({role.upper()})\n"
                if cache_control:
                    conversation_content += f"**Cache Control:** {cache_control}\n\n"
                conversation_content += f"```\n{formatted_content}\n```\n\n"

            # Log the response - handle both Claude native and AWS Bedrock formats
            if response and not response.get('error'):
                # Check for Claude native format first
                response_content = response.get("content")
                has_content_blocks = response_content and hasattr(response_content, '__iter__') and not hasattr(response_content, 'strip')
                
                if "content" in response and has_content_blocks:
                    # Claude native format
                    content_blocks = response.get("content", [])
                    formatted_response = self._format_content_blocks(content_blocks)
                    
                    conversation_content += f"### ASSISTANT RESPONSE\n"
                    conversation_content += f"```\n{formatted_response}\n```\n\n"
                
                # Check for AWS Bedrock format
                elif "choices" in response:
                    # AWS Bedrock format
                    choices = response.get("choices", [])
                    if choices:
                        assistant_message = choices[0].get("message", {})
                        assistant_content = assistant_message.get("content", "")

                        conversation_content += f"### ASSISTANT RESPONSE\n"
                        conversation_content += f"```\n{assistant_content}\n```\n\n"
                
                else:
                    # Unknown format - log as JSON
                    conversation_content += f"### ASSISTANT RESPONSE (Unknown Format)\n"
                    conversation_content += f"```json\n{response}\n```\n\n"

                # Log token usage if available
                usage = response.get("usage", {})
                input_tokens = usage.get("input_tokens", 0) or usage.get("prompt_tokens", 0)
                output_tokens = usage.get("output_tokens", 0) or usage.get("completion_tokens", 0)

                if input_tokens > 0 or output_tokens > 0:
                    conversation_content += f"**Token Usage:** Input: {input_tokens:,}, Output: {output_tokens:,}\n\n"
            else:
                conversation_content += f"### ERROR RESPONSE\n"
                conversation_content += f"```\n{response}\n```\n\n"

            conversation_content += "---\n\n"

        # Add final result if provided
        if final_result:
            conversation_content += f"## FINAL ANALYSIS RESULT\n\n"
            conversation_content += f"```json\n{final_result}\n```\n\n"

        # Add double-check information if provided
        if double_check_info:
            conversation_content += f"## DOUBLE-CHECK VALIDATION\n\n"
            conversation_content += f"{double_check_info}\n\n"

        conversation_content += f"**End Time:** {time.strftime('%Y-%m-%d %H:%M:%S')}\n"
        conversation_content += "=" * 80 + "\n"

        # Write to file
        success = write_file(conversation_path, conversation_content)
        if success:
            logger.info(f"Logged complete conversation to: {conversation_filename}")
            return conversation_path
        else:
            logger.warning(f"Failed to log conversation to: {conversation_filename}")
            return None

    @classmethod
    def _dump_token_limit_error_context(cls, messages: List[Dict[str, str]], total_content_length: int, estimated_tokens: int, max_input_tokens: int) -> None:
        """
        Dump context information when token limit errors occur for investigation.

        Args:
            messages: The messages that caused the token limit error
            total_content_length: Total character count
            estimated_tokens: Estimated token count
            max_input_tokens: Maximum allowed input tokens
        """
        if not cls._errors_dir:
            logger.warning("Errors directory not setup, cannot dump token limit error context")
            return

        try:
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S_%f')[:-3]  # Include milliseconds
            error_filename = f"too_large_context_error_{timestamp}.txt"
            error_path = os.path.join(cls._errors_dir, error_filename)

            # Build detailed context information
            context_content = f"=== TOKEN LIMIT ERROR CONTEXT ===\n"
            context_content += f"Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            context_content += f"Total content: {total_content_length:,} characters\n"
            context_content += f"Estimated tokens: {estimated_tokens:,}\n"
            context_content += f"Max input tokens: {max_input_tokens:,}\n"
            context_content += f"Number of messages: {len(messages)}\n\n"

            # Add detailed breakdown by message
            context_content += "=== MESSAGE BREAKDOWN ===\n"
            for i, message in enumerate(messages, 1):
                role = message.get('role', 'unknown')
                content = message.get('content', '')
                content_length = len(content)
                estimated_msg_tokens = content_length // 3  # Same estimation as in Claude class

                context_content += f"\nMessage {i} ({role.upper()}):\n"
                context_content += f"  Length: {content_length:,} characters\n"
                context_content += f"  Estimated tokens: {estimated_msg_tokens:,}\n"

                # Show cache control if present
                cache_control = message.get('cache_control')
                if cache_control:
                    context_content += f"  Cache Control: {cache_control}\n"

                # Show first 500 and last 500 characters of content for investigation
                if content_length > 1000:
                    context_content += f"  Content preview (first 500 chars):\n{content[:500]}\n"
                    context_content += f"  ...\n"
                    context_content += f"  Content preview (last 500 chars):\n{content[-500:]}\n"
                else:
                    context_content += f"  Full content:\n{content}\n"

                context_content += "-" * 80 + "\n"

            context_content += "\n=== END CONTEXT ===\n"

            # Write to error file
            success = write_file(error_path, context_content)
            if success:
                logger.info(f"Token limit error context dumped to: {error_filename}")
                logger.info(f"Full path: {error_path}")
                logger.info(f"This file contains {total_content_length:,} characters of context for investigation")
            else:
                logger.error(f"Failed to dump token limit error context to: {error_filename}")

        except Exception as e:
            logger.error(f"Error dumping token limit error context: {e}")


    def send_message(
        self,
        messages: List[Dict[str, str]],
        stream: bool = False,
        enable_system_cache: bool = True,
        cache_ttl: str = "1h"
    ) -> Optional[Dict[str, Any]]:
        """
        Send a message to the AWS Bedrock API with custom message history.

        Args:
            messages: List of message dictionaries with 'role' and 'content'
            stream: Whether to stream the response
            enable_system_cache: Whether to enable ephemeral cache for system messages (default: True)
            cache_ttl: TTL for cache control (default: "1h")

        Returns:
            Dict: API response or None on error
        """
        try:
            payload = self.provider.create_payload(
                messages,
                stream=stream,
                enable_system_cache=enable_system_cache,
                cache_ttl=cache_ttl
            )
        except ValueError as e:
            logger.error(f"Failed to create payload due to token limits: {e}")
            return {"error": "token_limit_exceeded", "message": str(e)}

        # Store messages and response for conversation logging
        self.conversation_messages.append(messages.copy())
        response = self.provider.make_request(payload)
        self.conversation_responses.append(response.copy() if response else {"error": "No response"})

        if response is None:
            return None

        # Handle error responses
        if "error" in response:
            error_type = response.get("error")
            status_code = response.get("status_code", "unknown")
            message = response.get("message", "")
            logger.error(f"Message failed with error {error_type} (status {status_code}): {message}")
            return None

        return response

    def send_message_with_system(
        self,
        system_prompt: str,
        messages: List[Dict[str, str]],
        stream: bool = False,
        enable_system_cache: bool = True,
        cache_ttl: str = "1h"
    ) -> Optional[Dict[str, Any]]:
        """
        Send a message to the AWS Bedrock API with separate system prompt.
        This method maintains complete conversation history while keeping system prompt separate.

        Args:
            system_prompt: System prompt content
            messages: List of conversation message dictionaries (no system messages)
            stream: Whether to stream the response
            enable_system_cache: Whether to enable ephemeral cache for system messages
            cache_ttl: TTL for cache control

        Returns:
            Dict: API response or None on error
        """
        try:
            full_messages = [
                {"role": "system", "content": system_prompt}
            ] + messages

            payload = self.provider.create_payload(
                full_messages,
                stream=stream,
                enable_system_cache=enable_system_cache,
                cache_ttl=cache_ttl
            )
        except ValueError as e:
            logger.error(f"Failed to create payload due to token limits: {e}")
            return {"error": "token_limit_exceeded", "message": str(e)}

        # Store messages and response for conversation logging
        # Store the complete messages array (including system prompt) to accurately represent what was sent
        self.conversation_messages.append(full_messages.copy())
        response = self.provider.make_request(payload)
        self.conversation_responses.append(response.copy() if response else {"error": "No response"})

        if response is None:
            return None

        # Handle error responses
        if "error" in response:
            error_type = response.get("error")
            status_code = response.get("status_code", "unknown")
            message = response.get("message", "")
            logger.error(f"Message failed with error {error_type} (status {status_code}): {message}")
            return None

        return response

    def validate_connection(self) -> bool:
        """
        Validate connection using the configured provider.

        Returns:
            bool: True if connection is valid
        """
        try:
            # Delegate validation to the provider
            return self.provider.validate_connection()
        except Exception as e:
            logger.error(f"Error validating connection: {e}")
            return False

    def estimate_tokens(self, text: str) -> int:
        """
        Rough estimation of token count for text.
        This is a simple approximation - actual tokenization may differ.
        Uses a more conservative estimate to avoid exceeding limits.

        Args:
            text: Text to estimate tokens for

        Returns:
            int: Estimated token count
        """
        # More conservative approximation: ~3 characters per token for English text
        # This accounts for the fact that technical content and code may have more tokens per character
        return len(text) // 3

    def check_token_limit(self, system_prompt: str, user_prompt: str) -> bool:
        """
        Check if the combined prompts are within token limits.
        Uses conservative token estimation to prevent API errors.

        Args:
            system_prompt: System prompt text
            user_prompt: User prompt text

        Returns:
            bool: True if within limits
        """
        estimated_tokens = self.estimate_tokens(system_prompt + user_prompt)
        # Leave some buffer for response tokens
        max_input_tokens = self.config.max_tokens - 5000

        if estimated_tokens > max_input_tokens:
            logger.warning(f"Estimated tokens ({estimated_tokens:,}) exceed limit ({max_input_tokens:,})")
            logger.warning(f"Total characters: {len(system_prompt + user_prompt):,}")
            logger.warning(f"Model max tokens: {self.config.max_tokens:,}")
            return False

        return True

    def _format_content_blocks(self, content_blocks: List[Dict[str, Any]]) -> str:
        """
        Format Claude's content blocks into a readable string for logging.
        
        Args:
            content_blocks: List of content block dictionaries
            
        Returns:
            str: Formatted string representation
        """
        if not content_blocks:
            return ""
        
        formatted_parts = []
        for block in content_blocks:
            # Check if block is dict-like
            if hasattr(block, 'get'):
                block_type = block.get("type", "unknown")
                
                if block_type == "text":
                    text_content = block.get("text", "")
                    formatted_parts.append(text_content)
                
                elif block_type == "tool_use":
                    tool_id = block.get("id", "unknown")
                    tool_name = block.get("name", "unknown")
                    tool_input = block.get("input", {})
                    
                    formatted_parts.append(f"[TOOL_USE: {tool_name} (id: {tool_id})]")
                    formatted_parts.append(f"Input: {tool_input}")
                
                elif block_type == "tool_result":
                    tool_id = block.get("tool_use_id", "unknown")
                    result_content = block.get("content", "")
                    
                    formatted_parts.append(f"[TOOL_RESULT: (id: {tool_id})]")
                    formatted_parts.append(f"Result: {result_content}")
                
                else:
                    # Unknown block type
                    formatted_parts.append(f"[{block_type.upper()}: {block}]")
            else:
                # Non-dict block
                formatted_parts.append(str(block))
        
        return "\n".join(formatted_parts)

    def run_iterative_analysis(
        self,
        system_prompt: str,
        user_prompt: str,
        tools_executor: Any = None,
        supported_tools: List[str] = None,
        context_guidance_template: str = None,
        response_processor: Callable[[str], str] = None,
        max_iterations: int = None,
        token_usage_callback: Callable[[Dict[str, Any], int], None] = None,
        fallback_json_guidance: str = None,
        json_validator: Callable[[Any], bool] = None
    ) -> Optional[str]:
        """
        DEPRECATED: Use stage-specific analyzers from hindsight.core.llm.iterative instead.
        
        This method uses clean_json_response() which returns the LAST valid JSON candidate,
        causing incorrect results when LLM responses contain multiple JSON structures
        (e.g., context bundle dict AND collection_notes array).
        
        Recommended replacements:
        - ContextCollectionAnalyzer: For Stage 4a (dict with 'primary_function')
        - CodeAnalysisAnalyzer: For Stage 4b (array of issue dicts)
        - DiffContextAnalyzer: For Stage Da (dict with 'changed_functions')
        - DiffAnalysisAnalyzer: For Stage Db (array of issue dicts)
        
        Example migration:
            # Old (deprecated):
            result = self.claude.run_iterative_analysis(system_prompt, user_prompt, ...)
            
            # New (recommended):
            from hindsight.core.llm.iterative import ContextCollectionAnalyzer
            analyzer = ContextCollectionAnalyzer(claude=self.claude, tools_executor=self, ...)
            result = analyzer.run_iterative_analysis(system_prompt, user_prompt)
        
        Unified iterative analysis method following Claude's MCP pattern with configurable tool support.
        Maintains complete conversation history and uses structured tool calls.
        
        This method consolidates the iterative analysis pattern used across multiple analyzers:
        - code_analysis.py
        - diff_analysis.py
        - trace_code_analysis.py
        - file_or_directory_summary_generator.py
        
        Args:
            system_prompt: System prompt for analysis
            user_prompt: Initial user prompt
            tools_executor: Object with tools attribute for tool execution (None for no tools)
            supported_tools: List of tool names to support (None/empty for no tools)
            context_guidance_template: Template for contextual guidance between iterations
            response_processor: Optional function to process final response
            max_iterations: Maximum iterations (defaults to MAX_TOOL_ITERATIONS)
            token_usage_callback: Optional callback for token usage logging
            fallback_json_guidance: Custom re-prompt injected when the LLM returns no valid JSON.
                Overrides the default jsonOutputGuidance.md (which requests a JSON array).
                Use this when the expected output is a JSON object rather than an array.
            json_validator: Optional callable that takes the parsed JSON value and returns
                True if it is acceptable. When provided, JSON that fails this check is
                treated as "not yet valid" and the loop continues with fallback_json_guidance,
                exactly as if no JSON had been found.  Example:
                    json_validator=lambda p: isinstance(p, dict)
                ensures the LLM must return a JSON object, not an array.
            
        Returns:
            str: Final analysis result or None on error
        """
        # DEPRECATION WARNING: Log error to alert developers to migrate to stage-specific analyzers
        logger.error(
            "DEPRECATED: Claude.run_iterative_analysis() is deprecated and may return incorrect JSON. "
            "Use stage-specific analyzers from hindsight.core.llm.iterative instead: "
            "ContextCollectionAnalyzer, CodeAnalysisAnalyzer, DiffContextAnalyzer, DiffAnalysisAnalyzer. "
            "See docstring for migration example."
        )
        # Import constants here to avoid circular imports
        from ..constants import MAX_TOOL_ITERATIONS
        
        if max_iterations is None:
            max_iterations = MAX_TOOL_ITERATIONS
            
        # Determine if tools are enabled
        tools_enabled = (tools_executor is not None and
                        supported_tools is not None and
                        len(supported_tools) > 0)
        
        logger.info(f"Starting iterative analysis (max {max_iterations} iterations, tools: {'enabled' if tools_enabled else 'disabled'})")
        if tools_enabled:
            logger.info(f"Supported tools: {supported_tools}")

        # Initialize conversation state to track full history
        conversation_state = ConversationState()
        conversation_state.set_system_prompt(system_prompt)
        conversation_state.set_original_request(user_prompt)
        conversation_state.add_user_message(user_prompt)

        # Initialize TTL manager for system prompt caching
        from .ttl_manager import TTLManager
        ttl_manager = TTLManager()

        iteration = 0
        system_prompt_sent = False
        
        while iteration < max_iterations:
            iteration += 1
            logger.info(f"Analysis iteration {iteration}/{max_iterations}")
            
            # Force conclusion on final iteration - standard agentic loop termination pattern
            # This prevents the iterator from expiring mid-investigation without a verdict
            is_final_iteration = (iteration == max_iterations)
            if is_final_iteration and tools_enabled:
                final_iteration_guidance = (
                    "CRITICAL: This is your FINAL iteration. You MUST produce your JSON verdict NOW "
                    "based on what you have gathered so far. Do NOT request any more tools. "
                    "Respond ONLY with your final JSON analysis result."
                )
                conversation_state.add_user_message(final_iteration_guidance)
                logger.info("Final iteration - injected guidance to force JSON conclusion")

            # Check if we need to send system prompt based on TTL logic
            should_send_system = ttl_manager.should_resend_system_prompt(system_prompt)
            
            # Send complete conversation history to maintain context across iterations
            full_conversation = conversation_state.get_full_conversation()
            
            # Add contextual guidance for tool results if this is not the first iteration
            if iteration > 1 and context_guidance_template:
                contextual_message = context_guidance_template.format(
                    user_prompt=user_prompt[:200] + "..." if len(user_prompt) > 200 else user_prompt
                )
                # Add contextual guidance as a separate user message
                conversation_state.add_user_message(contextual_message)
                full_conversation = conversation_state.get_full_conversation()
            
            if should_send_system or not system_prompt_sent:
                # Send with system prompt (first time or TTL expired)
                response = self.send_message_with_system(
                    system_prompt=system_prompt,
                    messages=full_conversation,
                    enable_system_cache=True,
                    cache_ttl="1h"
                )

                # Record that system prompt was sent
                ttl_manager.record_system_prompt_sent(system_prompt)
                system_prompt_sent = True
                logger.info(f"System prompt sent in iteration {iteration} with {len(full_conversation)} messages")
            else:
                # Send without system prompt (use cached version)
                response = self.send_message(
                    messages=full_conversation,
                    enable_system_cache=True,
                    cache_ttl="1h"
                )
                logger.info(f"Using cached system prompt in iteration {iteration} with {len(full_conversation)} messages")

            if not response or "error" in response:
                logger.error(f"API error in iteration {iteration}")
                return None

            # Extract and log token usage for this API call if callback provided
            if token_usage_callback:
                token_usage_callback(response, iteration)

            # Extract content from AWS Bedrock response format
            # Response format: {"choices": [{"message": {"content": "..."}}], "usage": {...}}
            choices = response.get("choices", [])
            if not choices:
                logger.error(f"No choices in AWS Bedrock API response for iteration {iteration}")
                return None

            assistant_content = choices[0].get("message", {}).get("content", "")
            if not isinstance(assistant_content, str):
                assistant_content = str(assistant_content) if assistant_content else ""

            logger.info(f"Received response: {len(assistant_content)} characters")

            # Extract JSON-embedded tool requests from response text
            json_tool_requests = []
            if tools_enabled:
                json_tool_requests = self._extract_json_tool_requests(assistant_content)
                if json_tool_requests:
                    logger.info(f"Found {len(json_tool_requests)} JSON-embedded tool requests")

            # Dispatch: tool requests → continue loop; no tools → final answer
            if json_tool_requests and tools_enabled:
                # Assistant made tool requests — add message and execute tools
                conversation_state.add_assistant_message(assistant_content)
                logger.info(f"Processing {len(json_tool_requests)} JSON-embedded tool requests in iteration {iteration}")
                for i, tool_request in enumerate(json_tool_requests):
                    tool_result = self._execute_json_tool_request(tool_request, tools_executor, supported_tools)
                    tool_id = f"json_tool_{iteration}_{i}"
                    conversation_state.add_tool_result(tool_id, tool_result)
                    logger.info(f"Added JSON tool result for {tool_request.get('tool', 'unknown')} (id: {tool_id})")

                logger.info(f"Tool results added to conversation history")
                # Continue to next iteration to let LLM analyze tool results
                continue
            else:
                # No tool requests - add regular assistant message to conversation state
                conversation_state.add_assistant_message(assistant_content)

                # Apply clean_json_response to check if we have valid structured output
                cleaned_response = clean_json_response(assistant_content)

                # Check if we have valid JSON content after cleaning
                has_valid_json = False
                if cleaned_response and cleaned_response.strip():
                    try:
                        import json
                        parsed_json = json.loads(cleaned_response)
                        
                        # Skip validation if this looks like a tool call JSON (not final output)
                        # Tool calls have a "tool" key at the top level
                        is_tool_call_json = isinstance(parsed_json, dict) and 'tool' in parsed_json
                        
                        if is_tool_call_json:
                            # This is a tool call JSON that wasn't caught by _extract_json_tool_requests
                            # (possibly malformed or edge case) - don't treat as final output
                            logger.info(f"Found tool-call JSON in iteration {iteration} (has 'tool' key) — not treating as final output")
                        elif json_validator is None or json_validator(parsed_json):
                            has_valid_json = True
                            logger.info(f"Found valid JSON in response after cleaning in iteration {iteration}")
                        else:
                            # Debug logging: show what structure was received vs what was expected
                            json_type = type(parsed_json).__name__
                            if isinstance(parsed_json, dict):
                                top_keys = list(parsed_json.keys())[:5]  # Show first 5 keys
                                logger.info(f"Found JSON but failed shape validator in iteration {iteration} — "
                                           f"got dict with keys: {top_keys} — continuing")
                            elif isinstance(parsed_json, list):
                                list_len = len(parsed_json)
                                first_item_type = type(parsed_json[0]).__name__ if parsed_json else 'empty'
                                logger.info(f"Found JSON but failed shape validator in iteration {iteration} — "
                                           f"got list with {list_len} items, first item type: {first_item_type} — continuing")
                            else:
                                logger.info(f"Found JSON but failed shape validator in iteration {iteration} — "
                                           f"got {json_type} — continuing")
                    except json.JSONDecodeError:
                        logger.info(f"No valid JSON found after cleaning in iteration {iteration}")

                # If we have valid JSON or this is the last possible iteration, complete analysis
                if has_valid_json or iteration >= max_iterations:
                    if has_valid_json:
                        logger.info(f"Analysis complete with valid JSON in iteration {iteration}")
                        final_response = cleaned_response
                    else:
                        logger.info(f"Analysis complete (max iterations reached) in iteration {iteration}")
                        final_response = assistant_content

                    # Apply response processor if provided
                    if response_processor:
                        try:
                            processed_response = response_processor(final_response)
                            return processed_response
                        except Exception as e:
                            logger.warning(f"Response processor failed: {e}, returning raw content")
                            return final_response
                    else:
                        return final_response
                else:
                    # No valid JSON found and not at max iterations - continue iteration
                    # Add a strict guidance message to enforce JSON output
                    if fallback_json_guidance:
                        guidance_message = fallback_json_guidance
                    else:
                        try:
                            from ..prompts.prompt_builder import PromptBuilder
                            guidance_message = PromptBuilder.build_json_output_guidance()
                        except Exception as e:
                            logger.warning(f"Could not load JSON output guidance from file: {e}")
                            guidance_message = "Please provide your analysis results as a valid JSON array starting with [ and ending with ]."
                    conversation_state.add_user_message(guidance_message)
                    logger.info(f"No structured output found, continuing iteration {iteration + 1}")
                    continue

        logger.warning(f"Reached maximum iterations ({max_iterations}), returning last response")
        # Retrieve the last assistant message from conversation state
        full_conversation = conversation_state.get_full_conversation()
        if full_conversation and full_conversation[-1]["role"] == "assistant":
            last_content = full_conversation[-1]["content"]
            # Check if content is list-like
            is_list_content = hasattr(last_content, '__iter__') and not hasattr(last_content, 'strip')
            if is_list_content:
                # Extract text from content blocks
                text_parts = []
                for block in last_content:
                    block_type = block.get("type") if hasattr(block, 'get') else None
                    if block_type == "text":
                        text_parts.append(block.get("text", ""))
                final_response = "\n".join(text_parts)
            else:
                final_response = str(last_content)
            
            # Apply response processor if provided
            if response_processor:
                try:
                    return response_processor(final_response)
                except Exception as e:
                    logger.warning(f"Response processor failed: {e}, returning raw content")
                    return final_response
            else:
                return final_response
        return None

    def _extract_json_tool_requests(self, content: str) -> List[Dict[str, Any]]:
        """
        Extract JSON-embedded tool requests from LLM response content.
        This is the PRIMARY tool invocation mechanism used across all providers.
        
        Supports patterns like:
        - ```json {"tool": "readFile", "path": "file.py", "reason": "..."} ```
        - {"tool": "readFile", "path": "file.py"}
        
        Works identically for Claude, AWS Bedrock, and other providers since tools
        are invoked via JSON in response text, not through provider-specific APIs.
        
        Args:
            content: LLM response content to search
            
        Returns:
            List of tool request dictionaries with "tool" key
        """
        tool_requests = []
        
        # Regex patterns
        TOOL_REQUEST_CAPTURE_MARKDOWN_PATTERN = r'```json\s*(\{[^}]*"tool"[^}]*\})\s*```'
        TOOL_REQUEST_CAPTURE_SIMPLE_PATTERN = r'(\{[^}]*"tool"[^}]*\})'
        
        try:
            # Look for JSON tool requests in markdown blocks
            markdown_matches = re.findall(TOOL_REQUEST_CAPTURE_MARKDOWN_PATTERN, content, re.DOTALL)
            
            # Look for simple JSON tool requests
            simple_matches = re.findall(TOOL_REQUEST_CAPTURE_SIMPLE_PATTERN, content, re.DOTALL)
            
            # Process all matches
            all_matches = markdown_matches + [m for m in simple_matches if m not in markdown_matches]
            
            for match in all_matches:
                try:
                    tool_request = json.loads(match)
                    if isinstance(tool_request, dict) and 'tool' in tool_request:
                        tool_requests.append(tool_request)
                except json.JSONDecodeError:
                    logger.debug(f"Invalid JSON in legacy tool request: {match}")
                    continue
                    
        except Exception as e:
            logger.debug(f"Error extracting legacy tool requests: {e}")
            
        return tool_requests

    def _execute_json_tool_request(self, tool_request: Dict[str, Any], tools_executor: Any, supported_tools: List[str]) -> str:
        """
        Execute a JSON-embedded tool request using the centralized orchestrator.

        Converts JSON format to tool_use format for execution:
        - Input: {"tool": "readFile", "path": "file.py", "reason": "..."}
        - Converts to: {"id": "...", "name": "readFile", "input": {"path": "file.py", "reason": "..."}}
        - Executes via tools_executor.tools.execute_tool_use()

        Args:
            tool_request: JSON tool request dictionary like {"tool": "readFile", "path": "file.py"}
            tools_executor: Object with tools attribute for tool execution
            supported_tools: List of supported tool names

        Returns:
            str: Tool execution result
        """
        try:
            tool_name = tool_request.get("tool", "unknown")

            # Check if this tool is supported
            if tool_name not in supported_tools:
                return f"Error: Tool '{tool_name}' is not supported. Available tools: {supported_tools}"

            # Convert JSON format to tool_use format for execution
            tool_use = {
                "id": f"json_{tool_name}_{int(time.time())}",
                "name": tool_name,
                "input": {k: v for k, v in tool_request.items() if k != "tool"}
            }

            return tools_executor.tools.execute_tool_use(tool_use)

        except Exception as e:
            logger.error(f"Error executing JSON tool request: {e}")
            return f"Error executing JSON tool request: {str(e)}"
