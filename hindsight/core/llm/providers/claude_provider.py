#!/usr/bin/env python3
# Created by Sridhar Gurivireddy
"""
Claude API Provider
Handles direct communication with Claude API

PROVIDER TYPE: llm_provider_type='claude'
==========================================

PRIMARY TOOL MECHANISM (JSON-Embedded):
- System prompts describe tools as JSON objects
- LLM returns: ```json {"tool": "readFile", "path": "file.py", "reason": "..."} ```
- llm.py extracts and executes these JSON requests using regex patterns
- Works identically to AWS Bedrock provider - no provider-specific tool handling needed

TOOL RESULT FORMAT (Unified Body Requests):
- Tool results use plain text format for both Claude and AWS Bedrock providers
- Format: {"role": "user", "content": "[TOOL_RESULT: tool_id]\nresult"}
- This ensures consistent behavior across all providers
- No Claude-specific tool_result content blocks are used

LEGACY STRUCTURED TOOL SUPPORT:
- Still supports Claude's native tool_use blocks as fallback
- Tools are described in system prompts and invoked via JSON in response text

RESPONSE FORMAT (Claude Native):
- Returns: {"content": [{"type": "text", "text": "..."}, {"type": "tool_use", ...}], "usage": {...}}
- Text content contains JSON tool requests that are parsed by llm.py
- Downstream code detects format using duck-typing (hasattr checks)

SYSTEM PROMPT HANDLING:
- Claude expects system prompt as top-level "system" parameter
- Not included in messages array like other providers

FORMAT DETECTION STRATEGY:
- Uses hasattr(obj, 'attribute') instead of isinstance()
- Example: hasattr(content, 'strip') to detect strings
- Example: hasattr(content, '__iter__') and not hasattr(content, 'strip') to detect lists
"""

import json
import orjson
import requests
import time
from typing import Optional, Dict, Any, List


from .base_provider import (
    BaseLLMProvider,
    LLMConfig,
    INPUT_TOO_LONG_ERROR,
    DEFAULT_MAX_RETRIES,
    DEFAULT_RETRY_DELAYS
)
from ....utils.log_util import get_logger

logger = get_logger(__name__)





class ClaudeProvider(BaseLLMProvider):
    """
    Claude API provider for direct communication with Claude API.
    Handles HTTP requests to Claude API endpoints.
    """

    def __init__(self, config: LLMConfig):
        """
        Initialize Claude API provider with security hardening.

        Args:
            config: LLM configuration
        """
        super().__init__(config)

        self.headers = {
            "Content-Type": "application/json",
            "x-api-key": config.api_key,
            "anthropic-version": "2023-06-01"
        }

        # Security hardening: Create a session with explicit security settings
        self.session = requests.Session()

        # Disable .netrc usage to prevent credential leakage (CVE-2024-35195)
        self.session.trust_env = False

        # Ensure certificate verification is always enabled
        self.session.verify = True

        # Set secure SSL context
        self.session.mount('https://', requests.adapters.HTTPAdapter())

        logger.debug(f"Initialized secure Claude API provider for {config.api_url}")
        logger.debug("Security features enabled: trust_env=False, verify=True")

    def _process_claude_response(self, claude_response: Dict[str, Any]) -> Dict[str, Any]:
        """
        Process Claude API response in native format without transformation.
        
        Claude API returns:
        {
            "content": [
                {"type": "text", "text": "response text"},
                {"type": "tool_use", "id": "call_123", "name": "readFile", "input": {"path": "file.py"}}
            ],
            "usage": {"input_tokens": 123, "output_tokens": 456},
            ...
        }

        Args:
            claude_response: Raw response from Claude API

        Returns:
            Dict: Claude response in native format
        """
        # Return Claude response as-is in native format
        return claude_response

    def make_request(self, payload: Dict[str, Any], max_retries: int = DEFAULT_MAX_RETRIES) -> Optional[Dict[str, Any]]:
        """
        Make a POST request to Claude API with retry logic.

        Args:
            payload: Request payload
            max_retries: Maximum number of retries for retriable errors

        Returns:
            Dict: API response or None on error
        """
        retry_delays = DEFAULT_RETRY_DELAYS

        for attempt in range(max_retries + 1):  # +1 for initial attempt
            try:
                start_time = time.time()
                logger.info(f"headers: {self.headers}")
                # Use session with security hardening instead of direct requests.post
                response = self.session.post(
                    self.config.api_url,
                    headers=self.headers,
                    json=payload,
                    timeout=self.config.timeout,
                    verify=True,  # Explicit certificate verification
                    allow_redirects=False
                )

                request_duration = time.time() - start_time
                logger.debug(f"Claude API request completed in {request_duration:.2f}s")

                # Handle response using the dedicated method
                result = self._handle_response(response, attempt, max_retries)
                if result is not None:
                    return result
                # If result is None, it means we should retry (for rate limiting)
                continue

            except requests.exceptions.Timeout:
                if self._should_retry(attempt, max_retries):
                    self._wait_and_retry(attempt, max_retries, "Claude API request timed out")
                    continue
                else:
                    logger.error(f"Claude API request timed out - max retries ({max_retries}) reached")
                    return {"error": "timeout"}
            except requests.exceptions.RequestException as e:
                if self._should_retry(attempt, max_retries):
                    self._wait_and_retry(attempt, max_retries, f"Claude API request failed: {e}")
                    continue
                else:
                    logger.error(f"Claude API request failed - max retries ({max_retries}) reached: {e}")
                    return {"error": "request_exception", "message": str(e)}
            except Exception as e:
                logger.error(f"Unexpected error during Claude API call: {e}")
                return {"error": "unexpected", "message": str(e)}

        # This should never be reached, but just in case
        return {"error": "max_retries_exceeded"}


    def _handle_response(self, response: requests.Response, attempt: int, max_retries: int) -> Optional[Dict[str, Any]]:
        """
        Handle API response based on status code.

        Args:
            response: The HTTP response
            attempt: Current attempt number (0-indexed)
            max_retries: Maximum number of retries allowed

        Returns:
            Optional[Dict]: Response dict, or None if should retry
        """
        if response.status_code == 200:
            return self._handle_success_response(response, attempt)

        error_text = response.text
        logger.error(f"Claude API request failed with status {response.status_code}: {error_text}")

        # Handle specific error cases
        if response.status_code == 400:
            return self._handle_bad_request(response)

        # Handle server errors (500, 502, 503, 504)
        if response.status_code in [500, 502, 503, 504]:
            return self._handle_server_error(response)

        # Check for rate limiting - this is retriable
        if response.status_code == 429:
            if self._should_retry(attempt, max_retries):
                self._wait_and_retry(attempt, max_retries, "Rate limit exceeded")
                return None  # Signal to retry
            else:
                logger.error(f"Rate limit exceeded - max retries ({max_retries}) reached")
                return {"error": "rate_limit_exceeded", "status_code": response.status_code}

        return {"error": "api_error", "status_code": response.status_code, "message": error_text}

    def _handle_success_response(self, response: requests.Response, attempt: int) -> Dict[str, Any]:
        """
        Handle successful API response.

        Args:
            response: The HTTP response
            attempt: Current attempt number (0-indexed)

        Returns:
            Dict: Transformed response
        """
        if attempt > 0:
            logger.info(f"Claude API request succeeded after {attempt} retries")

        # Get the raw Claude API response using orjson with proper encoding handling
        response_content = response.content
        # Check if response_content is string-like
        if hasattr(response_content, 'encode'):
            response_content = response_content.encode('utf-8')

        claude_response = orjson.loads(response_content)

        # Process Claude API response in native format
        return self._process_claude_response(claude_response)

    def _handle_bad_request(self, response: requests.Response) -> Dict[str, Any]:
        """
        Handle 400 Bad Request errors.

        Args:
            response: The HTTP response

        Returns:
            Dict: Error response
        """
        error_text = response.text
        if INPUT_TOO_LONG_ERROR in error_text.lower():
            logger.warning("Input too long for model")
            return {"error": "input_too_long", "status_code": response.status_code}

        return {"error": "bad_request", "status_code": response.status_code, "message": error_text}

    def _handle_server_error(self, response: requests.Response) -> Dict[str, Any]:
        """
        Handle server errors (500, 502, 503, 504).

        Args:
            response: The HTTP response

        Returns:
            Dict: Error response
        """
        error_text = response.text
        logger.error(f"Server error {response.status_code} - this indicates a problem with the API server")

        if response.status_code == 500:
            logger.error("HTTP 500 Internal Server Error - the API server encountered an internal error")
            logger.error("This could be due to:")
            logger.error("  1. Server configuration issues")
            logger.error("  2. Invalid API key or authentication problems")
            logger.error("  3. Model loading or initialization failures")
            logger.error("  4. Server resource constraints")

        return {"error": "server_error", "status_code": response.status_code, "message": error_text}

    def _should_retry(self, attempt: int, max_retries: int) -> bool:
        """
        Check if we should retry the request.

        Args:
            attempt: Current attempt number (0-indexed)
            max_retries: Maximum number of retries allowed

        Returns:
            bool: True if should retry
        """
        return attempt < max_retries

    def _wait_and_retry(self, attempt: int, max_retries: int, reason: str) -> None:
        """
        Wait before retrying the request.

        Args:
            attempt: Current attempt number (0-indexed)
            max_retries: Maximum number of retries allowed
            reason: Reason for retry
        """
        retry_delays = DEFAULT_RETRY_DELAYS
        wait_time = retry_delays[attempt] if attempt < len(retry_delays) else retry_delays[-1]
        logger.warning(f"{reason} (attempt {attempt + 1}/{max_retries + 1}), waiting {wait_time}s before retry...")
        time.sleep(wait_time)


    def create_payload(
        self,
        messages: List[Dict[str, str]],
        stream: bool = False,
        enable_system_cache: bool = False,
        cache_ttl: str = "1h",
        enable_tools: bool = True
    ) -> Dict[str, Any]:
        """
        Create request payload for Claude API with proper prompt caching and tool support.
        Claude API expects system messages as a top-level 'system' parameter,
        not as messages with role 'system'.

        Args:
            messages: List of message dictionaries
            stream: Whether to stream the response
            enable_system_cache: Whether to enable ephemeral cache for system messages
            cache_ttl: TTL for cache control (default: "1h")

        Returns:
            Dict: Request payload

        Raises:
            ValueError: If payload exceeds token limits
        """
        # Separate system messages from other messages and transform tool messages
        system_message = None
        processed_messages = []
        total_content_length = 0

        for message in messages:
            content = message.get("content", "")
            total_content_length += len(content)

            if message.get("role") == "system":
                # Claude API expects system message as top-level parameter
                # Always format as a list of content blocks
                if enable_system_cache:
                    # Add cache control for system message
                    system_message = [{
                        "type": "text",
                        "text": content,
                        "cache_control": {
                            "type": "ephemeral",
                            "ttl": cache_ttl
                        }
                    }]
                else:
                    # Format as list of content blocks without cache control
                    system_message = [{
                        "type": "text",
                        "text": content
                    }]
            elif message.get("role") == "assistant" and message.get("tool_calls"):
                # Transform assistant message with tool calls to Claude format
                assistant_content = message.get("content", "")
                tool_calls = message.get("tool_calls", [])

                # Build content blocks for Claude
                content_blocks = []
                if assistant_content:
                    content_blocks.append({
                        "type": "text",
                        "text": assistant_content
                    })

                # Add tool use blocks
                for tool_call in tool_calls:
                    function_info = tool_call.get("function", {})
                    arguments_str = function_info.get("arguments", "{}")

                    try:
                        arguments = json.loads(arguments_str)
                    except json.JSONDecodeError:
                        arguments = {}

                    content_blocks.append({
                        "type": "tool_use",
                        "id": tool_call.get("id", ""),
                        "name": function_info.get("name", ""),
                        "input": arguments
                    })

                processed_messages.append({
                    "role": "assistant",
                    "content": content_blocks
                })
            else:
                # Keep other messages as-is but ensure content is properly formatted
                message_copy = message.copy()
                # Check if content is string-like
                if hasattr(content, 'strip'):
                    # Convert string content to Claude content block format
                    message_copy["content"] = content
                processed_messages.append(message_copy)

        # Final safety check: validate token limits before creating payload
        estimated_tokens = total_content_length // 3  # Conservative estimate
        max_input_tokens = self.config.max_tokens - 5000  # Leave buffer for response

        if estimated_tokens > max_input_tokens:
            error_msg = (
                f"CRITICAL: Payload exceeds token limits in create_payload() - this should have been caught earlier!\n"
                f"Total content: {total_content_length:,} characters\n"
                f"Estimated tokens: {estimated_tokens:,}\n"
                f"Max input tokens: {max_input_tokens:,}\n"
                f"Model limit: {self.config.max_tokens:,}"
            )
            logger.error(error_msg)
            raise ValueError(error_msg)

        # Build payload with system message as top-level parameter
        payload = {
            "model": self.config.model,
            "max_tokens": self.config.max_tokens,
            "messages": processed_messages,
            "stream": stream,
            "temperature": self.config.temperature
        }

        # Add system message as top-level parameter if present
        if system_message is not None:
            payload["system"] = system_message

        return payload

    def validate_connection(self) -> bool:
        """
        Validate connection to Claude API.

        Returns:
            bool: True if connection is valid
        """
        try:
            test_messages = [
                {
                    "role": "user",
                    "content": "Hello, please respond with 'OK' to confirm connection."
                }
            ]

            payload = self.create_payload(test_messages, enable_system_cache=False)
            response = self.make_request(payload)

            if response and response.get("content"):
                # Response is in Claude's native format
                content_blocks = response.get("content", [])
                for block in content_blocks:
                    # Check if block is dict-like
                    if hasattr(block, 'get') and block.get("type") == "text":
                        text_content = block.get("text", "")
                        if "OK" in text_content.upper():
                            logger.info("Claude API connection validated successfully")
                            return True

            logger.warning("Claude API connection validation failed")
            return False

        except Exception as e:
            logger.error(f"Error validating Claude API connection: {e}")
            return False