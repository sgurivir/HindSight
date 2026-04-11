#!/usr/bin/env python3
# Created by Sridhar Gurivireddy
"""
API Key Utility for Hindsight Analysis
Provides fallback API key retrieval using Apple Connect token
with automatic token refresh support for long-running analyses.
"""

import logging
import subprocess
import time
from typing import Optional, Callable

def get_apple_connect_token() -> Optional[str]:
    """
    Get Apple Connect OAuth token as fallback API key.

    Returns:
        str: The OAuth token if successful, None otherwise
    """
    logger = logging.getLogger(__name__)

    try:
        # Command to get Apple Connect token
        cmd = [
            '/usr/local/bin/appleconnect', 'getToken',
            '-C', 'hvys3fcwcteqrvw3qzkvtk86viuoqv',
            '--token-type=oauth',
            '--interactivity-type=none',
            '-E', 'prod',
            '-G', 'pkce',
            '-o', 'openid,dsid,accountname,profile,groups'
        ]

        logger.info("Attempting to retrieve Apple Connect token...")

        # Execute the command and capture output
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30  # 30 second timeout
        )

        if result.returncode == 0:
            # Extract the token (last part of the output)
            token = result.stdout.strip().split()[-1]
            if token:
                logger.info("Successfully retrieved Apple Connect token")
                return token
            else:
                logger.warning("Apple Connect command succeeded but no token found in output")
                return None
        else:
            logger.warning(f"Apple Connect command failed with return code {result.returncode}")
            if result.stderr:
                logger.warning(f"Error output: {result.stderr.strip()}")
            return None

    except subprocess.TimeoutExpired:
        logger.warning("Apple Connect token retrieval timed out")
        return None
    except FileNotFoundError:
        logger.warning("Apple Connect tool not found at /usr/local/bin/appleconnect")
        return None
    except Exception as e:
        logger.warning(f"Error retrieving Apple Connect token: {e}")
        return None

# Default token refresh interval: 25 minutes (tokens expire in ~30 minutes)
DEFAULT_TOKEN_REFRESH_INTERVAL_SECONDS = 25 * 60  # 1500 seconds


class AppleConnectTokenManager:
    """
    Manages AppleConnect OAuth tokens with automatic refresh support.
    
    This class tracks token age and automatically refreshes tokens before they expire.
    Tokens typically expire in ~30 minutes, so we refresh at 25 minutes by default.
    
    Usage:
        # Create a token manager (singleton pattern recommended)
        token_manager = AppleConnectTokenManager()
        
        # Get a fresh token (will refresh if needed)
        token = token_manager.get_token()
        
        # Check if token needs refresh
        if token_manager.needs_refresh():
            token_manager.refresh_token()
    """
    
    _instance = None  # Singleton instance
    
    def __new__(cls, *args, **kwargs):
        """Singleton pattern to ensure only one token manager exists."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(
        self,
        config_api_key: Optional[str] = None,
        refresh_interval_seconds: int = DEFAULT_TOKEN_REFRESH_INTERVAL_SECONDS
    ):
        """
        Initialize the token manager.
        
        Args:
            config_api_key: Static API key from configuration (if provided, no refresh needed)
            refresh_interval_seconds: How often to refresh tokens (default: 25 minutes)
        """
        # Only initialize once (singleton pattern)
        if self._initialized:
            return
            
        self._logger = logging.getLogger(__name__)
        self._config_api_key = config_api_key
        self._refresh_interval = refresh_interval_seconds
        self._current_token: Optional[str] = None
        self._token_acquired_at: Optional[float] = None
        self._is_apple_connect_token = False
        self._initialized = True
        
        self._logger.info(
            f"AppleConnectTokenManager initialized with refresh interval: "
            f"{refresh_interval_seconds} seconds ({refresh_interval_seconds // 60} minutes)"
        )
    
    @classmethod
    def reset_instance(cls):
        """Reset the singleton instance (useful for testing)."""
        cls._instance = None
    
    def _is_using_static_api_key(self) -> bool:
        """Check if using a static API key from config (no refresh needed)."""
        return self._config_api_key is not None and len(self._config_api_key) > 0
    
    def needs_refresh(self) -> bool:
        """
        Check if the token needs to be refreshed.
        
        Returns:
            bool: True if token should be refreshed, False otherwise
        """
        # Static API keys never need refresh
        if self._is_using_static_api_key():
            return False
        
        # No token yet - need to acquire one
        if self._current_token is None or self._token_acquired_at is None:
            return True
        
        # Check if token has exceeded refresh interval
        elapsed = time.time() - self._token_acquired_at
        needs_refresh = elapsed >= self._refresh_interval
        
        if needs_refresh:
            self._logger.info(
                f"Token refresh needed: {elapsed:.0f}s elapsed "
                f"(threshold: {self._refresh_interval}s)"
            )
        
        return needs_refresh
    
    def refresh_token(self) -> Optional[str]:
        """
        Force refresh the AppleConnect token.
        
        Returns:
            str: New token if successful, None otherwise
        """
        if self._is_using_static_api_key():
            self._logger.debug("Using static API key, no refresh needed")
            return self._config_api_key
        
        self._logger.info("Refreshing AppleConnect token...")
        new_token = get_apple_connect_token()
        
        if new_token:
            self._current_token = new_token
            self._token_acquired_at = time.time()
            self._is_apple_connect_token = True
            self._logger.info("AppleConnect token refreshed successfully")
            return new_token
        else:
            self._logger.warning("Failed to refresh AppleConnect token")
            return self._current_token  # Return old token if refresh fails
    
    def get_token(self) -> Optional[str]:
        """
        Get the current token, refreshing if necessary.
        
        This is the main method to use for getting tokens. It automatically
        handles refresh logic based on token age.
        
        Returns:
            str: Current valid token, or None if unavailable
        """
        # Static API key - return directly
        if self._is_using_static_api_key():
            return self._config_api_key
        
        # Check if we need to refresh
        if self.needs_refresh():
            return self.refresh_token()
        
        return self._current_token
    
    def get_token_age_seconds(self) -> Optional[float]:
        """
        Get the age of the current token in seconds.
        
        Returns:
            float: Token age in seconds, or None if no token
        """
        if self._token_acquired_at is None:
            return None
        return time.time() - self._token_acquired_at
    
    def get_time_until_refresh_seconds(self) -> Optional[float]:
        """
        Get time remaining until next refresh.
        
        Returns:
            float: Seconds until refresh needed, or None if using static key
        """
        if self._is_using_static_api_key():
            return None
        
        if self._token_acquired_at is None:
            return 0  # Need refresh now
        
        elapsed = time.time() - self._token_acquired_at
        remaining = self._refresh_interval - elapsed
        return max(0, remaining)
    
    def is_apple_connect_token(self) -> bool:
        """Check if the current token is from AppleConnect (vs static config)."""
        return self._is_apple_connect_token


# Global token manager instance (lazy initialization)
_global_token_manager: Optional[AppleConnectTokenManager] = None


def get_token_manager(
    config_api_key: Optional[str] = None,
    refresh_interval_seconds: int = DEFAULT_TOKEN_REFRESH_INTERVAL_SECONDS
) -> AppleConnectTokenManager:
    """
    Get or create the global token manager instance.
    
    Args:
        config_api_key: Static API key from configuration
        refresh_interval_seconds: Token refresh interval
        
    Returns:
        AppleConnectTokenManager: The global token manager instance
    """
    global _global_token_manager
    
    if _global_token_manager is None:
        _global_token_manager = AppleConnectTokenManager(
            config_api_key=config_api_key,
            refresh_interval_seconds=refresh_interval_seconds
        )
    
    return _global_token_manager


def get_api_key(config_api_key: Optional[str] = None) -> Optional[str]:
    """
    Get API key with fallback to Apple Connect token.

    Args:
        config_api_key: API key from configuration file

    Returns:
        str: API key if available, None otherwise
    """
    logger = logging.getLogger(__name__)

    # First, try the API key from config
    if config_api_key:
        logger.info("Using API key from configuration")
        return config_api_key

    # Fallback to Apple Connect token
    logger.info("No API key in config, attempting Apple Connect token fallback...")
    apple_token = get_apple_connect_token()

    if apple_token:
        logger.info("Using Apple Connect token as API key")
        return apple_token
    else:
        logger.warning("No API key available from config or Apple Connect")
        return None