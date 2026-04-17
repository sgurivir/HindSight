#!/usr/bin/env python3
# Created by Sridhar Gurivireddy

"""
Base LLM Provider Interface
Defines the common interface for all LLM providers

TOOL INVOCATION STRATEGY:
==========================
This system uses JSON-embedded tool requests in system prompts, making it provider-agnostic:

1. Tool Request Format (JSON-Embedded in Response):
   - LLM returns JSON objects in markdown code blocks: ```json {"tool": "readFile", "path": "file.py", "reason": "..."} ```
   - System prompts describe tools as JSON objects, not structured API tool definitions
   - llm.py extracts these JSON requests using regex patterns and executes them

2. Structured/native tool calls are NEVER used (enable_tools=False always):
   - Avoids provider-specific tool_use format mismatches
   - Works identically across all providers

3. Response Format Handling:
   - AWS Bedrock: {"choices": [{"message": {"content": "...", "tool_calls": [...]}}]}
   - Detection uses duck-typing (hasattr checks) instead of isinstance()

Example JSON-Embedded Tool Request:

System Prompt:
"To read a file, return: ```json {\"tool\": \"readFile\", \"path\": \"file.py\", \"reason\": \"...\"} ```"

LLM Response:
"I need to check the implementation. ```json {\"tool\": \"readFile\", \"path\": \"src/main.py\", \"reason\": \"Need to see the main function\"} ```"

Code extracts JSON, executes tool, returns result to LLM in next iteration.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional, Dict, Any, List

# Constants
INPUT_TOO_LONG_ERROR = "input is too long"
DEFAULT_MAX_RETRIES = 3
DEFAULT_RETRY_DELAYS = [30, 60, 90]  # Wait times in seconds for each retry



@dataclass
class LLMConfig:
    """
    Configuration for LLM providers.
    
    Supports multiple authentication modes:
    1. project_credentials (FloodGate token) - highest priority for Apple endpoints
    2. credentials (OAuth/OIDC token) - standard authentication
    3. api_key (legacy) - backward compatibility
    4. AppleConnect auto-refresh - fallback when no credentials provided
    """
    api_url: str
    model: str
    max_tokens: int = 64000
    temperature: float = 0.05
    timeout: int = 300
    
    # Authentication fields (in priority order for Apple endpoints)
    api_key: str = ""  # Legacy field for backward compatibility
    credentials: str = ""  # OAuth/OIDC token
    project_credentials: str = ""  # FloodGate project token (highest priority)


@dataclass
class LLMResponse:
    """Standardized response from LLM providers"""
    success: bool
    content: Optional[str] = None
    error: Optional[str] = None
    status_code: Optional[int] = None
    usage: Optional[Dict[str, int]] = None
    raw_response: Optional[Dict[str, Any]] = None


class BaseLLMProvider(ABC):
    """
    Abstract base class for LLM providers.
    All LLM providers must implement this interface.
    
    ARCHITECTURAL PRINCIPLE: Use structured tools and ensure compatibility with both
    Claude API and AWS Bedrock providers.
    """

    def __init__(self, config: LLMConfig):
        """
        Initialize the provider with configuration.

        Args:
            config: LLM configuration
        """
        self.config = config

    @abstractmethod
    def make_request(self, payload: Dict[str, Any], max_retries: int = 3) -> Optional[Dict[str, Any]]:
        """
        Make a request to the LLM provider.

        Args:
            payload: Request payload
            max_retries: Maximum number of retries for retriable errors

        Returns:
            Dict: Provider response or None on error
        """
        pass

    @abstractmethod
    def create_payload(
        self,
        messages: List[Dict[str, str]],
        stream: bool = False,
        enable_system_cache: bool = False,
        cache_ttl: str = "1h"
    ) -> Dict[str, Any]:
        """
        Create request payload for the LLM provider.

        Args:
            messages: List of message dictionaries
            stream: Whether to stream the response
            enable_system_cache: Whether to enable caching for system messages
            cache_ttl: TTL for cache control

        Returns:
            Dict: Request payload
        """
        pass

    @abstractmethod
    def validate_connection(self) -> bool:
        """
        Validate connection to the LLM provider.

        Returns:
            bool: True if connection is valid
        """
        pass

    def estimate_tokens(self, text: str) -> int:
        """
        Rough estimation of token count for text.
        This is a simple approximation - actual tokenization may differ.

        Args:
            text: Text to estimate tokens for

        Returns:
            int: Estimated token count
        """
        # Conservative approximation: ~3 characters per token for English text
        return len(text) // 3

    def check_token_limit(self, system_prompt: str, user_prompt: str) -> bool:
        """
        Check if the combined prompts are within token limits.

        Args:
            system_prompt: System prompt text
            user_prompt: User prompt text

        Returns:
            bool: True if within limits
        """
        estimated_tokens = self.estimate_tokens(system_prompt + user_prompt)
        # Leave some buffer for response tokens
        max_input_tokens = self.config.max_tokens - 5000

        return estimated_tokens <= max_input_tokens

