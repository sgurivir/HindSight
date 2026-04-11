#!/usr/bin/env python3
# Created by Sridhar Gurivireddy
"""
LLM-Based Analyzer
Base class for analyzers that use real LLM providers with HTTP retry logic
"""

import sys
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional
from threading import Lock
import time

from .base_analyzer import BaseAnalyzer
from ..core.constants import (DEFAULT_MAX_TOKENS, DEFAULT_TEMPERATURE, DEFAULT_API_RATE_LIMIT,
                              DEFAULT_RATE_LIMIT_WINDOW)
from ..utils.log_util import get_logger

# Add the project root to Python path for imports
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

logger = get_logger(__name__)


class LLMBasedAnalyzer(BaseAnalyzer):
    """
    Base analyzer class for LLM-based analysis with HTTP retry logic.
    Handles rate limiting and HTTP retries for real LLM providers (aws_bedrock, claude).
    """

    def __init__(self):
        super().__init__()
        self.config = None
        self.api_key = None
        self.repo_path = None
        self.file_content_provider = None

        # Rate limiting for HTTP-based LLM providers
        self.api_requests = []  # List of request timestamps
        self.rate_limit_lock = Lock()

        # Initialize logger
        self.logger = get_logger(__name__)

    def initialize(self, config: Mapping[str, Any]) -> None:
        """Setup, load models, and prepare for analysis."""
        super().initialize(config)
        self.config = dict(config)  # Convert to dict for compatibility
        self.api_key = config.get('api_key')
        self.repo_path = config.get('repo_path')
        self.file_content_provider = config.get('file_content_provider')

    def _wait_for_rate_limit(self) -> float:
        """
        Implement rate limiting - wait if necessary to stay within API limits.
        This method should only be called by LLM-based analyzers that make HTTP requests.

        Returns:
            float: Time waited in seconds
        """
        with self.rate_limit_lock:
            current_time = time.time()

            # Remove requests older than the rate limit window
            self.api_requests = [req_time for req_time in self.api_requests
                               if current_time - req_time < DEFAULT_RATE_LIMIT_WINDOW]

            # Check if we need to wait
            if len(self.api_requests) >= DEFAULT_API_RATE_LIMIT:
                # Find the oldest request in the current window
                oldest_request = min(self.api_requests)
                # Wait for exactly the rate limit window from the first request
                wait_time = DEFAULT_RATE_LIMIT_WINDOW - (current_time - oldest_request)

                if wait_time > 0:
                    self.logger.info(f"Rate limit reached ({len(self.api_requests)}/{DEFAULT_API_RATE_LIMIT}), "
                                   f"waiting {wait_time:.1f} seconds...")
                    time.sleep(wait_time)

                    # After waiting, clean up old requests again
                    current_time = time.time()
                    self.api_requests = [req_time for req_time in self.api_requests
                                       if current_time - req_time < DEFAULT_RATE_LIMIT_WINDOW]

                    return wait_time

            # Record this request
            self.api_requests.append(current_time)
            return 0.0

    def analyze_function(self, func_record: Mapping[str, Any]) -> Optional[Mapping[str, Any]]:
        """
        Analyze a single function record using LLM with HTTP retry logic.
        This method should be overridden by concrete LLM-based analyzer implementations.
        """
        raise NotImplementedError("Subclasses must implement analyze_function method")

    def finalize(self) -> None:
        """Cleanup after analysis."""
        pass

    def set_publisher(self, publisher) -> None:
        """
        Set the publisher for result checking.

        Args:
            publisher: The publisher instance
        """
        # Store publisher for potential future use
        self.publisher = publisher
        # The actual caching logic is handled at the runner level
        self.logger.debug(f"Publisher set on {self.__class__.__name__}")