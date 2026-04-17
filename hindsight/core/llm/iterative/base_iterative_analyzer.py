#!/usr/bin/env python3
"""
Base Iterative Analyzer

Abstract base class for stage-specific iterative analyzers.
Provides shared utilities but NO default run_iterative_analysis().
Each subclass MUST implement its own JSON extraction and validation logic.

ARCHITECTURAL PRINCIPLE:
========================
Each analyzer stage expects a DIFFERENT JSON output structure:
- Context Collection (Stage 4a): dict with 'primary_function' key
- Code Analysis (Stage 4b): array of issue dicts
- Diff Context (Stage Da): dict with 'changed_functions' key
- Diff Analysis (Stage Db): array of issue dicts

The shared clean_json_response() function returns the LAST valid JSON candidate,
which may be a nested array (like collection_notes) instead of the expected output.
By implementing stage-specific extract_json() methods, each analyzer can search
for the CORRECT structure first, avoiding this problem.
"""

import json
import re
import time
from abc import ABC, abstractmethod
from typing import Optional, Dict, Any, List, Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from ..llm import Claude

from ....utils.log_util import get_logger

logger = get_logger(__name__)


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

    def clear(self):
        """Clear the conversation state."""
        self.messages = []
        self.system_prompt = None
        self.original_request = None


class BaseIterativeAnalyzer(ABC):
    """
    Abstract base class for stage-specific iterative analyzers.
    
    Each subclass MUST implement:
    - extract_json() - Stage-specific JSON extraction
    - validate_json() - Stage-specific JSON validation
    - get_fallback_guidance() - Stage-specific guidance message for JSON output
    
    The run_iterative_analysis() method is implemented here with the common
    iteration loop, but uses the subclass's extract_json() and validate_json()
    methods for stage-specific behavior.
    """
    
    def __init__(self, claude: 'Claude'):
        """
        Initialize the analyzer with a Claude client.
        
        Args:
            claude: Claude client instance for LLM communication
        """
        self.claude = claude
        self.conversation_state = ConversationState()
    
    @abstractmethod
    def extract_json(self, content: str) -> Optional[str]:
        """
        Extract JSON from LLM response. Stage-specific logic.
        
        Each subclass implements this to search for the CORRECT JSON structure
        for its stage, rather than relying on the generic clean_json_response()
        which returns the LAST valid JSON candidate.
        
        Args:
            content: Raw LLM response content
            
        Returns:
            Extracted JSON string or None if not found
        """
        pass
    
    @abstractmethod
    def validate_json(self, parsed_json: Any) -> bool:
        """
        Validate that parsed JSON has the expected shape for this stage.
        
        Args:
            parsed_json: Parsed JSON value (dict, list, etc.)
            
        Returns:
            True if the JSON has the expected structure, False otherwise
        """
        pass
    
    @abstractmethod
    def get_fallback_guidance(self) -> str:
        """
        Get stage-specific guidance message for JSON output.
        
        This message is sent to the LLM when no valid JSON is found,
        to guide it toward producing the correct output format.
        
        Returns:
            Guidance message string
        """
        pass
    
    def run_iterative_analysis(
        self,
        system_prompt: str,
        user_prompt: str,
        tools_executor: Any = None,
        supported_tools: List[str] = None,
        context_guidance_template: str = None,
        response_processor: Callable[[str], str] = None,
        max_iterations: int = None,
        token_usage_callback: Callable[[Dict[str, Any], int], None] = None
    ) -> Optional[str]:
        """
        Run iterative analysis with stage-specific JSON handling.
        
        This method implements the common iteration loop but uses the subclass's
        extract_json() and validate_json() methods for stage-specific behavior.
        
        Args:
            system_prompt: System prompt for analysis
            user_prompt: Initial user prompt
            tools_executor: Object with tools attribute for tool execution (None for no tools)
            supported_tools: List of tool names to support (None/empty for no tools)
            context_guidance_template: Template for contextual guidance between iterations
            response_processor: Optional function to process final response
            max_iterations: Maximum iterations (defaults to MAX_TOOL_ITERATIONS)
            token_usage_callback: Optional callback for token usage logging
            
        Returns:
            str: Final analysis result or None on error
        """
        # Import constants here to avoid circular imports
        from ...constants import MAX_TOOL_ITERATIONS, SOFT_REMINDER_ITERATION
        
        if max_iterations is None:
            max_iterations = MAX_TOOL_ITERATIONS
        
        # Calculate soft reminder iteration (default to 16 if not set, or 80% of max_iterations)
        soft_reminder_at = SOFT_REMINDER_ITERATION if max_iterations >= SOFT_REMINDER_ITERATION else int(max_iterations * 0.8)
            
        # Determine if tools are enabled
        tools_enabled = (tools_executor is not None and
                        supported_tools is not None and
                        len(supported_tools) > 0)
        
        stage_name = self.__class__.__name__
        logger.info(f"[{stage_name}] Starting iterative analysis (max {max_iterations} iterations, tools: {'enabled' if tools_enabled else 'disabled'})")
        if tools_enabled:
            logger.info(f"[{stage_name}] Supported tools: {supported_tools}")

        # Initialize conversation state to track full history
        self.conversation_state.clear()
        self.conversation_state.set_system_prompt(system_prompt)
        self.conversation_state.set_original_request(user_prompt)
        self.conversation_state.add_user_message(user_prompt)

        # Initialize TTL manager for system prompt caching
        from ..ttl_manager import TTLManager
        ttl_manager = TTLManager()

        iteration = 0
        system_prompt_sent = False
        
        while iteration < max_iterations:
            iteration += 1
            logger.info(f"[{stage_name}] Iteration {iteration}/{max_iterations}")
            
            # Force conclusion on final iteration
            is_final_iteration = (iteration == max_iterations)
            if is_final_iteration and tools_enabled:
                final_iteration_guidance = (
                    "CRITICAL: This is your FINAL iteration. You MUST produce your JSON verdict NOW "
                    "based on what you have gathered so far. Do NOT request any more tools. "
                    "Respond ONLY with your final JSON analysis result."
                )
                self.conversation_state.add_user_message(final_iteration_guidance)
                logger.info(f"[{stage_name}] Final iteration - injected guidance to force JSON conclusion")
            
            # Soft reminder at iteration 16 (or configured soft_reminder_at) to encourage output generation
            elif iteration == soft_reminder_at and tools_enabled:
                soft_reminder_guidance = (
                    "REMINDER: You are approaching the iteration limit. You have a few more iterations remaining. "
                    "Please start wrapping up your context collection and prepare to generate your final JSON output. "
                    "If you have gathered sufficient context, you may produce your JSON result now. "
                    "Otherwise, make only essential tool calls and then produce your final output."
                )
                self.conversation_state.add_user_message(soft_reminder_guidance)
                logger.info(f"[{stage_name}] Soft reminder at iteration {iteration} - encouraging output generation")

            # Check if we need to send system prompt based on TTL logic
            should_send_system = ttl_manager.should_resend_system_prompt(system_prompt)
            
            # Send complete conversation history to maintain context across iterations
            full_conversation = self.conversation_state.get_full_conversation()
            
            # Add contextual guidance for tool results if this is not the first iteration
            if iteration > 1 and context_guidance_template:
                contextual_message = context_guidance_template.format(
                    user_prompt=user_prompt[:200] + "..." if len(user_prompt) > 200 else user_prompt
                )
                self.conversation_state.add_user_message(contextual_message)
                full_conversation = self.conversation_state.get_full_conversation()
            
            if should_send_system or not system_prompt_sent:
                # Send with system prompt (first time or TTL expired)
                response = self.claude.send_message_with_system(
                    system_prompt=system_prompt,
                    messages=full_conversation,
                    enable_system_cache=True,
                    cache_ttl="1h"
                )

                # Record that system prompt was sent
                ttl_manager.record_system_prompt_sent(system_prompt)
                system_prompt_sent = True
                logger.info(f"[{stage_name}] System prompt sent in iteration {iteration} with {len(full_conversation)} messages")
            else:
                # Send without system prompt (use cached version)
                response = self.claude.send_message(
                    messages=full_conversation,
                    enable_system_cache=True,
                    cache_ttl="1h"
                )
                logger.info(f"[{stage_name}] Using cached system prompt in iteration {iteration} with {len(full_conversation)} messages")

            if not response or "error" in response:
                logger.error(f"[{stage_name}] API error in iteration {iteration}")
                return None

            # Extract and log token usage for this API call if callback provided
            if token_usage_callback:
                token_usage_callback(response, iteration)

            # Extract content from AWS Bedrock response format
            choices = response.get("choices", [])
            if not choices:
                logger.error(f"[{stage_name}] No choices in API response for iteration {iteration}")
                return None

            assistant_content = choices[0].get("message", {}).get("content", "")
            if not isinstance(assistant_content, str):
                assistant_content = str(assistant_content) if assistant_content else ""

            logger.info(f"[{stage_name}] Received response: {len(assistant_content)} characters")

            # Extract JSON-embedded tool requests from response text
            json_tool_requests = []
            if tools_enabled:
                json_tool_requests = self._extract_json_tool_requests(assistant_content)
                if json_tool_requests:
                    logger.info(f"[{stage_name}] Found {len(json_tool_requests)} JSON-embedded tool requests")

            # Dispatch: tool requests → continue loop; no tools → final answer
            if json_tool_requests and tools_enabled:
                # Assistant made tool requests — add message and execute tools
                self.conversation_state.add_assistant_message(assistant_content)
                logger.info(f"[{stage_name}] Processing {len(json_tool_requests)} JSON-embedded tool requests in iteration {iteration}")
                for i, tool_request in enumerate(json_tool_requests):
                    tool_result = self._execute_json_tool_request(tool_request, tools_executor, supported_tools)
                    tool_id = f"json_tool_{iteration}_{i}"
                    self.conversation_state.add_tool_result(tool_id, tool_result)
                    logger.info(f"[{stage_name}] Added JSON tool result for {tool_request.get('tool', 'unknown')} (id: {tool_id})")

                logger.info(f"[{stage_name}] Tool results added to conversation history")
                # Continue to next iteration to let LLM analyze tool results
                continue
            else:
                # No tool requests - add regular assistant message to conversation state
                self.conversation_state.add_assistant_message(assistant_content)

                # Use stage-specific extract_json() instead of generic clean_json_response()
                cleaned_response = self.extract_json(assistant_content)

                # Check if we have valid JSON content after extraction
                has_valid_json = False
                if cleaned_response and cleaned_response.strip():
                    try:
                        parsed_json = json.loads(cleaned_response)
                        
                        # Skip validation if this looks like a tool call JSON (not final output)
                        is_tool_call_json = isinstance(parsed_json, dict) and 'tool' in parsed_json
                        
                        if is_tool_call_json:
                            logger.info(f"[{stage_name}] Found tool-call JSON in iteration {iteration} (has 'tool' key) — not treating as final output")
                        elif self.validate_json(parsed_json):
                            has_valid_json = True
                            logger.info(f"[{stage_name}] Found valid JSON in response after extraction in iteration {iteration}")
                        else:
                            # Debug logging: show what structure was received vs what was expected
                            self._log_json_validation_failure(parsed_json, iteration)
                    except json.JSONDecodeError:
                        logger.info(f"[{stage_name}] No valid JSON found after extraction in iteration {iteration}")

                # If we have valid JSON or this is the last possible iteration, complete analysis
                if has_valid_json or iteration >= max_iterations:
                    if has_valid_json:
                        logger.info(f"[{stage_name}] Analysis complete with valid JSON in iteration {iteration}")
                        final_response = cleaned_response
                    else:
                        logger.info(f"[{stage_name}] Analysis complete (max iterations reached) in iteration {iteration}")
                        final_response = assistant_content

                    # Apply response processor if provided
                    if response_processor:
                        try:
                            processed_response = response_processor(final_response)
                            return processed_response
                        except Exception as e:
                            logger.warning(f"[{stage_name}] Response processor failed: {e}, returning raw content")
                            return final_response
                    else:
                        return final_response
                else:
                    # No valid JSON found and not at max iterations - continue iteration
                    # Use stage-specific fallback guidance
                    guidance_message = self.get_fallback_guidance()
                    self.conversation_state.add_user_message(guidance_message)
                    logger.info(f"[{stage_name}] No structured output found, continuing iteration {iteration + 1}")
                    continue

        logger.warning(f"[{stage_name}] Reached maximum iterations ({max_iterations}), returning last response")
        # Retrieve the last assistant message from conversation state
        full_conversation = self.conversation_state.get_full_conversation()
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
                    logger.warning(f"[{stage_name}] Response processor failed: {e}, returning raw content")
                    return final_response
            else:
                return final_response
        return None
    
    def _log_json_validation_failure(self, parsed_json: Any, iteration: int) -> None:
        """Log details about JSON validation failure for debugging."""
        stage_name = self.__class__.__name__
        json_type = type(parsed_json).__name__
        
        if isinstance(parsed_json, dict):
            top_keys = list(parsed_json.keys())[:5]
            logger.info(f"[{stage_name}] Found JSON but failed shape validator in iteration {iteration} — "
                       f"got dict with keys: {top_keys} — continuing")
        elif isinstance(parsed_json, list):
            list_len = len(parsed_json)
            first_item_type = type(parsed_json[0]).__name__ if parsed_json else 'empty'
            logger.info(f"[{stage_name}] Found JSON but failed shape validator in iteration {iteration} — "
                       f"got list with {list_len} items, first item type: {first_item_type} — continuing")
        else:
            logger.info(f"[{stage_name}] Found JSON but failed shape validator in iteration {iteration} — "
                       f"got {json_type} — continuing")
    
    def _extract_json_tool_requests(self, content: str) -> List[Dict[str, Any]]:
        """
        Extract JSON-embedded tool requests from LLM response content.
        
        Supports patterns like:
        - ```json {"tool": "readFile", "path": "file.py", "reason": "..."} ```
        - {"tool": "readFile", "path": "file.py"}
        
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
                    logger.debug(f"Invalid JSON in tool request: {match}")
                    continue
                    
        except Exception as e:
            logger.debug(f"Error extracting tool requests: {e}")
            
        return tool_requests

    def _execute_json_tool_request(self, tool_request: Dict[str, Any], tools_executor: Any, supported_tools: List[str]) -> str:
        """
        Execute a JSON-embedded tool request using the centralized orchestrator.

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
    
    # Shared JSON extraction utilities for subclasses
    
    def _find_all_json_objects(self, content: str) -> List[str]:
        """
        Find all valid JSON objects in content, sorted by size (largest first).
        
        Args:
            content: Content to search for JSON objects
            
        Returns:
            List of valid JSON object strings, sorted by size (largest first)
        """
        candidates = []
        for i, char in enumerate(content):
            if char == '{':
                brace_count = 1
                for j in range(i + 1, len(content)):
                    if content[j] == '{':
                        brace_count += 1
                    elif content[j] == '}':
                        brace_count -= 1
                        if brace_count == 0:
                            potential = content[i:j + 1]
                            try:
                                json.loads(potential)
                                candidates.append(potential)
                            except json.JSONDecodeError:
                                pass
                            break
        candidates.sort(key=len, reverse=True)
        return candidates
    
    def _find_all_json_arrays(self, content: str) -> List[str]:
        """
        Find all valid JSON arrays in content, sorted by size (largest first).
        
        Args:
            content: Content to search for JSON arrays
            
        Returns:
            List of valid JSON array strings, sorted by size (largest first)
        """
        candidates = []
        for i, char in enumerate(content):
            if char == '[':
                bracket_count = 1
                for j in range(i + 1, len(content)):
                    if content[j] == '[':
                        bracket_count += 1
                    elif content[j] == ']':
                        bracket_count -= 1
                        if bracket_count == 0:
                            potential = content[i:j + 1]
                            try:
                                json.loads(potential)
                                candidates.append(potential)
                            except json.JSONDecodeError:
                                pass
                            break
        candidates.sort(key=len, reverse=True)
        return candidates
