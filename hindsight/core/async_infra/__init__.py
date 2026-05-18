"""
Shared async infrastructure for parallel LLM-based analysis.

This package provides reusable components for running async worker pools
with rate limiting, extracted from the patterns in external_input_analyzer
and sink_analyzer.

Public API:
    - RateLimiter: Token-bucket rate limiter for async contexts
    - run_worker_pool: Generic async worker pool function
    - create_async_llm_fn: Wraps a synchronous LLM provider in run_in_executor
"""

from .rate_limiter import RateLimiter
from .worker_pool import run_worker_pool
from .llm_async_wrapper import create_async_llm_fn

__all__ = [
    "RateLimiter",
    "run_worker_pool",
    "create_async_llm_fn",
]
