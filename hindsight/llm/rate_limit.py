"""Async sliding-window rate limiter.

One instance per pipeline. `acquire()` blocks until a request can be issued
under the `max_requests / window_seconds` ceiling. Internally a deque of
timestamps; the oldest are expired lazily on each acquire.

Equivalent to the legacy `hindsight.core.async_infra.rate_limiter.RateLimiter`
but with monotonic time so it is robust to wall-clock jumps and slightly
tighter locking (only the deque mutation is held).
"""

from __future__ import annotations

import asyncio
import collections
import time
from typing import Deque


class AsyncRateLimiter:
    """Token-bucket-style sliding window rate limiter.

    Example:
        limiter = AsyncRateLimiter(max_requests=40, window_seconds=240)
        await limiter.acquire()  # gates the next request
    """

    def __init__(self, max_requests: int, window_seconds: float):
        if max_requests <= 0:
            raise ValueError("max_requests must be positive")
        if window_seconds <= 0:
            raise ValueError("window_seconds must be positive")
        self._max = max_requests
        self._window = float(window_seconds)
        self._timestamps: Deque[float] = collections.deque()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        """Block until a slot is available in the current window."""
        while True:
            async with self._lock:
                now = time.monotonic()
                cutoff = now - self._window
                while self._timestamps and self._timestamps[0] <= cutoff:
                    self._timestamps.popleft()
                if len(self._timestamps) < self._max:
                    self._timestamps.append(now)
                    return
                # Wait just long enough for the oldest entry to age out.
                wait_for = self._timestamps[0] + self._window - now
            # Release the lock while sleeping so other waiters can re-check.
            await asyncio.sleep(max(wait_for, 0.01))
