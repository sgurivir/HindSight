#!/usr/bin/env python3
# Created by Sridhar Gurivireddy
"""
Centralized token tracking for analysis operations.
Provides a single source of truth for token usage across all analyzers.
"""

from typing import Tuple, Optional
from ..utils.log_util import get_logger


class TokenTracker:
    """
    Centralized token tracking for analysis operations.
    
    This class provides a single source of truth for token usage across all analyzers,
    eliminating the duplicate tracking systems that were causing discrepancies.
    """
    
    def __init__(self, llm_provider_type: str = "aws_bedrock"):
        """
        Initialize token tracker.

        Args:
            llm_provider_type: Type of LLM provider ("aws_bedrock")
        """
        self.logger = get_logger(__name__)
        self.llm_provider_type = llm_provider_type
        
        # Token counters
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        
        # Call tree section token tracking
        self.call_tree_section_tokens = 0
        
        # Analytics helper for session tracking
        self.analytics_helper = None
        
        self.logger.debug(f"TokenTracker initialized for provider: {llm_provider_type}")
    
    def set_analytics_helper(self, analytics_helper) -> None:
        """
        Set the analytics helper for session tracking.
        
        Args:
            analytics_helper: AnalyticsHelper instance for session tracking
        """
        self.analytics_helper = analytics_helper
        self.logger.debug("Analytics helper set for token tracking")
    
    def add_token_usage(self, input_tokens: int, output_tokens: int) -> None:
        """
        Add token usage to the tracker.
        
        Args:
            input_tokens: Number of input tokens used
            output_tokens: Number of output tokens used
        """
        self.total_input_tokens += input_tokens
        self.total_output_tokens += output_tokens
        
        # Record to analytics if available
        if self.analytics_helper:
            total_tokens = input_tokens + output_tokens
            self.analytics_helper.record_token_usage(
                tokens_used=total_tokens,
                retry_errors=0,
                cost_usd=0.0,
                duration_seconds=0.0
            )
        
        self.logger.debug(f"Added tokens - Input: {input_tokens:,}, Output: {output_tokens:,}")
    
    def get_total_token_usage(self) -> Tuple[int, int]:
        """
        Get total token usage for the session.
        
        Returns:
            Tuple[int, int]: (total_input_tokens, total_output_tokens)
        """
        return self.total_input_tokens, self.total_output_tokens
    
    def record_tokens_from_analysis(self, code_analysis_instance) -> Tuple[int, int]:
        """
        Extract and record token usage from a CodeAnalysis instance.
        
        Args:
            code_analysis_instance: CodeAnalysis instance with token tracking
            
        Returns:
            Tuple[int, int]: (input_tokens, output_tokens) for this analysis
        """
        try:
            # Get real token counts from the analysis instance
            input_tokens, output_tokens = code_analysis_instance.get_token_totals()
            
            # Use the new API to add tokens
            self.add_token_usage(input_tokens, output_tokens)
            
            self.logger.debug(f"Recorded tokens from analysis - Input: {input_tokens:,}, Output: {output_tokens:,}")
            return input_tokens, output_tokens
            
        except Exception as e:
            self.logger.error(f"Error recording tokens from analysis: {e}")
            return 0, 0
    
    def record_dummy_tokens(self) -> Tuple[int, int]:
        """
        Record dummy token usage for dummy analyzer.
        
        Returns:
            Tuple[int, int]: (0, 0) for dummy analyzer
        """
        # Dummy analyzer uses 0 tokens
        input_tokens, output_tokens = 0, 0
        
        # Use the new API to add tokens (0, 0)
        self.add_token_usage(input_tokens, output_tokens)
        
        self.logger.debug("Recorded dummy tokens (0, 0)")
        return input_tokens, output_tokens
    
    def get_totals(self) -> Tuple[int, int, int]:
        """
        Get total token usage (legacy method for backward compatibility).
        
        Returns:
            Tuple[int, int, int]: (total_input_tokens, total_output_tokens, total_tokens)
        """
        total_tokens = self.total_input_tokens + self.total_output_tokens
        return self.total_input_tokens, self.total_output_tokens, total_tokens
    
    def add_call_tree_tokens(self, tokens: int) -> None:
        """
        Track tokens used by call tree sections.
        
        Args:
            tokens: Number of tokens used by call tree section
        """
        self.call_tree_section_tokens += tokens
        self.logger.debug(f"Call tree section tokens: {tokens}")
    
    def get_call_tree_tokens(self) -> int:
        """
        Get total tokens used by call tree sections.
        
        Returns:
            int: Total call tree section tokens
        """
        return self.call_tree_section_tokens
    
    def log_summary(self) -> None:
        """Log a summary of token usage."""
        input_tokens, output_tokens, total_tokens = self.get_totals()
        
        if total_tokens > 0:
            self.logger.info("=" * 80)
            self.logger.info("TOKEN USAGE SUMMARY")
            self.logger.info("=" * 80)
            self.logger.info(f"Total Input Tokens:  {input_tokens:,}")
            self.logger.info(f"Total Output Tokens: {output_tokens:,}")
            self.logger.info(f"Total Tokens Used:   {total_tokens:,}")
            self.logger.info(f"Provider Type:       {self.llm_provider_type}")
            
            # Log call tree section tokens if any were used
            if self.call_tree_section_tokens > 0:
                self.logger.info(f"Call Tree Section Tokens: {self.call_tree_section_tokens:,}")
            
            self.logger.info("=" * 80)
        else:
            self.logger.info("No tokens used in this analysis session")
    
    def reset(self) -> None:
        """Reset token counters."""
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.call_tree_section_tokens = 0
        self.logger.debug("Token counters reset")