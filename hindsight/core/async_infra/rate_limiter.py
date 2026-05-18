"""
Token-bucket rate limiter for async contexts.

Extracted from hindsight/analyzers/external_input_analyzer.py and
hindsight/analyzers/sink_analyzer.py. Uses a sliding window of timestamps
to enforce a maximum number of requests per configurable window.

Usage:
    limiter = RateLimiter(max_requests_per_minute=30)
    await limiter.acquire()  # blocks until a slot is available

    # Or with a custom window:
    limiter = RateLimiter(max_requests=40, window_seconds=240)
    await limiter.acquire()
"""

import asyncio
import time
from collections import deque

from ...utils.log_util import get_logger

logger = get_logger(__name__)


class RateLimiter:
    """
    Sliding-window rate limiter for async contexts.

    Tracks timestamps of recent requests in a deque. When acquire() is called,
    it purges timestamps older than the configured window. If the window is at
    capacity, it sleeps until the oldest timestamp exits the window.

    Thread-safe across multiple async workers via asyncio.Lock.
    """

    def __init__(self, max_requests_per_minute: int = None, *,
                 max_requests: int = None, window_seconds: float = None):
        """
        Args:
            max_requests_per_minute: Maximum requests in a 60s window (legacy).
            max_requests: Maximum requests allowed in the rolling window.
            window_seconds: Size of the sliding window in seconds.

        Either provide max_requests_per_minute (legacy, implies window=60s),
        or provide both max_requests and window_seconds.
        """
        if max_requests is not None and window_seconds is not None:
            if max_requests <= 0:
                raise ValueError("max_requests must be positive")
            if window_seconds <= 0:
                raise ValueError("window_seconds must be positive")
            self._max_requests = max_requests
            self._window_seconds = float(window_seconds)
        elif max_requests_per_minute is not None:
            if max_requests_per_minute <= 0:
                raise ValueError("max_requests_per_minute must be positive")
            self._max_requests = max_requests_per_minute
            self._window_seconds = 60.0
        else:
            raise ValueError(
                "Provide either max_requests_per_minute, or both max_requests and window_seconds"
            )

        self._timestamps: deque = deque()
        self._lock = asyncio.Lock()

    @property
    def max_requests_per_minute(self) -> int:
        """The configured rate limit (legacy property)."""
        return self._max_requests

    @property
    def window_seconds(self) -> float:
        """The configured window size in seconds."""
        return self._window_seconds

    async def acquire(self) -> None:
        """
        Wait until a request slot is available within the rate limit window.

        This method is safe to call from multiple concurrent coroutines.
        It uses an async lock to ensure the sliding window is updated atomically.
        """
        async with self._lock:
            now = time.monotonic()

            # Purge timestamps older than the window
            while self._timestamps and (now - self._timestamps[0]) >= self._window_seconds:
                self._timestamps.popleft()

            # If at capacity, wait until the oldest request exits the window
            if len(self._timestamps) >= self._max_requests:
                oldest = self._timestamps[0]
                wait_time = self._window_seconds - (now - oldest)
                if wait_time > 0:
                    logger.debug(f"Rate limit: {len(self._timestamps)}/{self._max_requests} in "
                                 f"{self._window_seconds}s window, waiting {wait_time:.1f}s")
                    # Release the lock while sleeping so other coroutines
                    # don't deadlock waiting to acquire
                    self._lock.release()
                    try:
                        await asyncio.sleep(wait_time)
                    finally:
                        await self._lock.acquire()
                    # After sleeping, re-purge stale timestamps
                    now = time.monotonic()
                    while self._timestamps and (now - self._timestamps[0]) >= self._window_seconds:
                        self._timestamps.popleft()

            # Record this request's timestamp
            self._timestamps.append(time.monotonic())
