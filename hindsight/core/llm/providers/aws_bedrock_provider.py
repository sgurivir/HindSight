#!/usr/bin/env python3
# Created by Sridhar Gurivireddy
"""
AWS Bedrock Provider
Handles communication with Claude models running on AWS Bedrock

PROVIDER TYPE: llm_provider_type='aws_bedrock'
===============================================

PRIMARY TOOL MECHANISM (JSON-Embedded):
- System prompts describe tools as JSON objects
- LLM returns: ```json {"tool": "readFile", "path": "file.py", "reason": "..."} ```
- llm.py extracts and executes these JSON requests using regex patterns
- Works identically to Claude provider - no provider-specific tool handling needed

TOOL RESULT FORMAT (Unified Body Requests):
- Tool results use plain text format for both Claude and AWS Bedrock providers
- Format: {"role": "user", "content": "[TOOL_RESULT: tool_id]\nresult"}
- This ensures consistent behavior across all providers
- Same implementation as Claude provider

LEGACY STRUCTURED TOOL SUPPORT:
- Still supports structured tool calls as fallback
- Converts OpenAI format to Claude format for AWS Bedrock
- Tools are described in system prompts and invoked via JSON in response text

RESPONSE FORMAT (OpenAI-compatible):
- Returns: {"choices": [{"message": {"content": "...", "tool_calls": [...]}}], "usage": {...}}
- Text content contains JSON tool requests that are parsed by llm.py
- Downstream code detects format using duck-typing (hasattr checks)

AUTHENTICATION:
- Uses Bearer token authentication
- Special handling for Apple GenAI endpoints with OIDC tokens

FORMAT DETECTION STRATEGY:
- Uses hasattr(obj, 'attribute') instead of isinstance()
- Response format detected by presence of "choices" key
- No runtime type checking based on provider class
"""

import os
import requests
import time
import urllib3
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


class AWSBedrockProvider(BaseLLMProvider):
    """
    AWS Bedrock provider for Claude models running on AWS Bedrock.
    Handles communication with AWS Bedrock REST API endpoints.
    
    Authentication Priority (for Apple GenAI endpoints):
    1. project_credentials (FloodGate token) - uses X-Floodgate-Project-Token header
    2. credentials (OAuth/OIDC token) - uses X-Apple-OIDC-Token header
    3. AppleConnect auto-refresh - automatically fetches and refreshes tokens
    """

    def __init__(self, config: LLMConfig):
        """
        Initialize AWS Bedrock provider with security hardening.

        Args:
            config: LLM configuration
        """
        super().__init__(config)
        
        # Track whether to use AppleConnect auto-refresh
        self._use_apple_connect_auto_refresh = False

        # Handle different authentication methods based on the API URL
        if self._is_apple_genai_endpoint(config.api_url):
            # Determine which token to use based on priority:
            # 1. project_credentials (FloodGate token) - highest priority
            # 2. credentials (OAuth token)
            # 3. api_key (legacy field)
            # 4. AppleConnect auto-refresh (fallback)
            
            project_creds_provided = config.project_credentials and config.project_credentials.strip()
            creds_provided = config.credentials and config.credentials.strip()
            api_key_provided = config.api_key and config.api_key.strip()
            
            if project_creds_provided:
                # Case 1: project_credentials provided - use FloodGate token
                bearer_token = config.project_credentials.strip()
                self.headers = {
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {bearer_token}",
                    "X-Floodgate-Project-Token": bearer_token
                }
                # Note: X-Apple-OIDC-Token is NOT added when using FloodGate token
                logger.info("Using project_credentials for Authorization and X-Floodgate-Project-Token headers")
                logger.info("X-Apple-OIDC-Token header skipped (FloodGate mode)")
                self._use_apple_connect_auto_refresh = False
                    
            elif creds_provided:
                # Case 2: credentials provided - use OAuth/OIDC token
                bearer_token = config.credentials.strip()
                self.headers = {
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {bearer_token}",
                    "X-Apple-OIDC-Token": bearer_token
                }
                logger.info("Using credentials for Authorization and X-Apple-OIDC-Token headers")
                self._use_apple_connect_auto_refresh = False
                
            elif api_key_provided:
                # Case 3: Legacy api_key provided - use as OIDC token
                # Check if this token was obtained via AppleConnect (needs refresh)
                # or is a static token (no refresh needed)
                bearer_token = config.api_key.strip()
                self.headers = {
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {bearer_token}",
                    "X-Apple-OIDC-Token": bearer_token
                }
                # AppleConnect tokens expire in ~30 minutes and need refresh
                # Static tokens (like service account tokens) don't expire
                # Since we can't easily distinguish, always enable auto-refresh
                # when using Apple GenAI endpoints without FloodGate token
                self._use_apple_connect_auto_refresh = True
                logger.info("Using legacy api_key for Authorization and X-Apple-OIDC-Token headers")
                logger.info("AppleConnect auto-refresh enabled (tokens expire in ~30 minutes)")
                
            else:
                # Case 4: Neither provided - use AppleConnect auto-refresh
                # Token will be fetched dynamically before each request
                self._use_apple_connect_auto_refresh = True
                self.headers = {
                    "Content-Type": "application/json",
                }
                logger.info("No credentials provided - will use AppleConnect auto-refresh")
            
            logger.debug(f"project_credentials provided: {project_creds_provided}")
            logger.debug(f"credentials provided: {creds_provided}")
            logger.debug(f"api_key provided: {api_key_provided}")
            logger.debug(f"Headers configured: {list(self.headers.keys())}")
        else:
            # Standard AWS Bedrock authentication (non-Apple endpoint)
            # Use credentials or api_key for Authorization header
            token = (config.credentials or config.api_key or "").strip()
            self.headers = {
                "Content-Type": "application/json",
                'Authorization': f'Bearer {token}'
            }
            self._use_apple_connect_auto_refresh = False
            logger.debug("Configured headers for standard AWS Bedrock endpoint")

        # Security hardening: Create a session with explicit security settings
        self.session = requests.Session()

        # Disable .netrc usage to prevent credential leakage (CVE-2024-35195)
        self.session.trust_env = False

        # Handle SSL verification for Apple internal endpoints
        if self._is_apple_genai_endpoint(config.api_url):
            # For Apple internal endpoints, check for custom certificate bundle
            custom_ca_bundle = os.getenv("REQUESTS_CA_BUNDLE") or os.getenv("CURL_CA_BUNDLE")
            # Allow disabling SSL verification only when explicitly requested
            disable_ssl = os.getenv("DISABLE_SSL_VERIFICATION", "false").lower() == "true"

            if custom_ca_bundle and os.path.exists(custom_ca_bundle):
                self.session.verify = custom_ca_bundle
                logger.info(f"Using custom CA bundle for Apple internal endpoint: {custom_ca_bundle}")
            elif disable_ssl:
                self.session.verify = False
                # Suppress InsecureRequestWarning when SSL verification is intentionally disabled
                urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
                logger.warning("SSL verification disabled for Apple internal endpoint (DISABLE_SSL_VERIFICATION=true)")
            else:
                # Default: Enable SSL verification
                self.session.verify = True
                logger.info("SSL verification enabled for Apple internal endpoint (default)")
        else:
            # For external endpoints, always verify certificates
            self.session.verify = True

        # Set secure SSL context
        self.session.mount('https://', requests.adapters.HTTPAdapter())

        logger.debug(f"Initialized secure AWS Bedrock provider for {config.api_url}")
        logger.debug(f"Security features enabled: trust_env=False, verify={self.session.verify}")

    def _is_apple_genai_endpoint(self, api_url: str) -> bool:
        """
        Check if the API URL is Apple's GenAI endpoint that requires OIDC authentication.

        Args:
            api_url: The API endpoint URL

        Returns:
            bool: True if this is Apple's GenAI endpoint
        """
        return "genai.apple.com" in api_url.lower() or "floodgate.g.apple.com" in api_url.lower()

    def _refresh_headers_if_needed(self) -> None:
        """
        Refresh headers with AppleConnect token if using auto-refresh mode.
        
        This method is called before each request when no static credentials
        are provided. It fetches a fresh token from AppleConnect and updates
        the Authorization and X-Apple-OIDC-Token headers.
        
        Log lines to look for:
        - INFO: "AppleConnect token refreshed successfully" - token was refreshed
        - WARNING: "Failed to get AppleConnect token" - token refresh failed
        - DEBUG: "Token refresh skipped" - using static credentials (not auto-refresh mode)
        """
        if not getattr(self, '_use_apple_connect_auto_refresh', False):
            logger.debug("Token refresh skipped - using static credentials (not auto-refresh mode)")
            return
        
        logger.info("Attempting AppleConnect token refresh...")
        
        try:
            from ....utils.api_key_util import get_token_manager
            token_manager = get_token_manager()
            token = token_manager.get_token()
            
            if token:
                self.headers["Authorization"] = f"Bearer {token}"
                self.headers["X-Apple-OIDC-Token"] = token
                # Log token refresh at INFO level for visibility
                # Only show first/last 4 chars of token for security
                token_preview = f"{token[:4]}...{token[-4:]}" if len(token) > 8 else "****"
                logger.info(f"AppleConnect token refreshed successfully (token: {token_preview})")
            else:
                logger.warning("Failed to get AppleConnect token - headers not updated")
        except ImportError as e:
            logger.error(f"Failed to import token manager: {e}")
        except Exception as e:
            logger.error(f"Error refreshing AppleConnect token: {e}")

    def make_request(self, payload: Dict[str, Any], max_retries: int = DEFAULT_MAX_RETRIES) -> Optional[Dict[str, Any]]:
        """
        Make a POST request to AWS Bedrock API with retry logic.
        
        Automatically refreshes AppleConnect tokens if using auto-refresh mode.

        Args:
            payload: Request payload
            max_retries: Maximum number of retries for retriable errors

        Returns:
            Dict: API response or None on error
        """
        # Refresh headers if using AppleConnect auto-refresh
        self._refresh_headers_if_needed()
        
        retry_delays = DEFAULT_RETRY_DELAYS

        for attempt in range(max_retries + 1):  # +1 for initial attempt
            try:
                start_time = time.time()
                # Use session with security hardening instead of direct requests.post
                response = self.session.post(
                    self.config.api_url,
                    headers=self.headers,
                    json=payload,
                    timeout=self.config.timeout,
                    verify=self.session.verify,  # Use session's verify setting
                    allow_redirects=False
                )

                request_duration = time.time() - start_time
                logger.debug(f"AWS Bedrock API request completed in {request_duration:.2f}s")

                if response.status_code == 200:
                    if attempt > 0:
                        logger.info(f"AWS Bedrock API request succeeded after {attempt} retries")
                    return response.json()
                else:
                    error_text = response.text
                    logger.error(f"AWS Bedrock API request failed with status {response.status_code}: {error_text}")

                    # Handle specific error cases
                    if response.status_code == 400 and INPUT_TOO_LONG_ERROR in error_text.lower():
                        logger.warning("Input too long for model")
                        return {"error": "input_too_long", "status_code": response.status_code}

                    # Handle server errors (500, 502, 503, 504) - these are often server-side issues
                    if response.status_code in [500, 502, 503, 504]:
                        logger.error(f"Server error {response.status_code} - this indicates a problem with the AWS Bedrock API server")
                        if response.status_code == 500:
                            logger.error("HTTP 500 Internal Server Error - the AWS Bedrock API server encountered an internal error")
                            logger.error("This could be due to:")
                            logger.error("  1. AWS Bedrock service configuration issues")
                            logger.error("  2. Invalid AWS credentials or authentication problems")
                            logger.error("  3. Model loading or initialization failures")
                            logger.error("  4. AWS service resource constraints")
                        return {"error": "server_error", "status_code": response.status_code, "message": error_text}

                    # Check for rate limiting - this is retriable
                    if response.status_code == 429:
                        if attempt < max_retries:
                            wait_time = retry_delays[attempt]
                            logger.warning(f"Rate limit exceeded (attempt {attempt + 1}/{max_retries + 1}), waiting {wait_time}s before retry...")
                            time.sleep(wait_time)
                            continue
                        else:
                            logger.error(f"Rate limit exceeded - max retries ({max_retries}) reached")
                            return {"error": "rate_limit_exceeded", "status_code": response.status_code}

                    return {"error": "api_error", "status_code": response.status_code, "message": error_text}

            except requests.exceptions.Timeout:
                if attempt < max_retries:
                    wait_time = retry_delays[attempt]
                    logger.warning(f"AWS Bedrock API request timed out (attempt {attempt + 1}/{max_retries + 1}), waiting {wait_time}s before retry...")
                    time.sleep(wait_time)
                    continue
                else:
                    logger.error(f"AWS Bedrock API request timed out - max retries ({max_retries}) reached")
                    return {"error": "timeout"}
            except requests.exceptions.RequestException as e:
                if attempt < max_retries:
                    wait_time = retry_delays[attempt]
                    logger.warning(f"AWS Bedrock API request failed: {e} (attempt {attempt + 1}/{max_retries + 1}), waiting {wait_time}s before retry...")
                    time.sleep(wait_time)
                    continue
                else:
                    logger.error(f"AWS Bedrock API request failed - max retries ({max_retries}) reached: {e}")
                    return {"error": "request_exception", "message": str(e)}
            except Exception as e:
                logger.error(f"Unexpected error during AWS Bedrock API call: {e}")
                return {"error": "unexpected", "message": str(e)}

        # This should never be reached, but just in case
        return {"error": "max_retries_exceeded"}

    def create_payload(
        self,
        messages: List[Dict[str, str]],
        stream: bool = False,
        enable_system_cache: bool = False,
        cache_ttl: str = "1h",
        enable_tools: bool = True
    ) -> Dict[str, Any]:
        """
        Create request payload for AWS Bedrock API.
        Note: AWS Bedrock may have different caching mechanisms than direct Claude API.

        Args:
            messages: List of message dictionaries
            stream: Whether to stream the response
            enable_system_cache: Whether to enable caching for system messages
            cache_ttl: TTL for cache control (default: "1h")

        Returns:
            Dict: Request payload

        Raises:
            ValueError: If payload exceeds token limits
        """
        # Process messages - AWS Bedrock may handle caching differently
        processed_messages = []
        total_content_length = 0

        for message in messages:
            processed_message = message.copy()
            content = message.get("content", "")
            total_content_length += len(content)

            # AWS Bedrock caching - may need different format than direct Claude API
            # For now, we'll use the same format but this might need adjustment
            if enable_system_cache and message.get("role") == "system":
                # AWS Bedrock might use different cache control format
                processed_message["cache_control"] = {
                    "type": "ephemeral",
                    "ttl": cache_ttl
                }

            processed_messages.append(processed_message)

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

        # AWS Bedrock payload format with tool support
        payload = {
            "model": self.config.model,
            "max_tokens": self.config.max_tokens,
            "messages": processed_messages,
            "stream": stream,
            "temperature": self.config.temperature
        }

        return payload

    def validate_connection(self) -> bool:
        """
        Validate connection to AWS Bedrock API.

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

            if response and response.get("choices"):
                content = response["choices"][0].get("message", {}).get("content", "")
                if "OK" in content.upper():
                    logger.info("AWS Bedrock API connection validated successfully")
                    return True

            logger.warning("AWS Bedrock API connection validation failed")
            return False

        except Exception as e:
            logger.error(f"Error validating AWS Bedrock API connection: {e}")
            return False