"""
Tests for hindsight.core.async_infra.rate_limiter.RateLimiter

Tests:
- Allows requests under limit without delay
- Enforces delay when limit reached
- Sliding window expiry allows new requests
- Concurrent acquire() calls are safe
"""

import asyncio
import time

import pytest

from hindsight.core.async_infra.rate_limiter import RateLimiter


@pytest.fixture
def limiter():
    """A rate limiter allowing 5 requests per minute."""
    return RateLimiter(max_requests_per_minute=5)


class TestRateLimiterBasic:
    """Test basic construction and validation."""

    def test_constructor_sets_max_rpm(self):
        rl = RateLimiter(max_requests_per_minute=30)
        assert rl.max_requests_per_minute == 30

    def test_constructor_rejects_zero(self):
        with pytest.raises(ValueError, match="must be positive"):
            RateLimiter(max_requests_per_minute=0)

    def test_constructor_rejects_negative(self):
        with pytest.raises(ValueError, match="must be positive"):
            RateLimiter(max_requests_per_minute=-1)


class TestRateLimiterAllowsUnderLimit:
    """Requests under the limit should pass without delay."""

    @pytest.mark.asyncio
    async def test_allows_requests_under_limit(self, limiter):
        """5 requests should complete nearly instantly with limit=5."""
        start = time.monotonic()
        for _ in range(5):
            await limiter.acquire()
        elapsed = time.monotonic() - start

        # Should complete in well under 1 second (no waiting)
        assert elapsed < 1.0

    @pytest.mark.asyncio
    async def test_single_request_no_delay(self):
        """A single request should never block."""
        rl = RateLimiter(max_requests_per_minute=1)
        start = time.monotonic()
        await rl.acquire()
        elapsed = time.monotonic() - start
        assert elapsed < 0.1


class TestRateLimiterEnforcesLimit:
    """When limit is reached, acquire() should block."""

    @pytest.mark.asyncio
    async def test_sixth_request_blocks(self):
        """With limit=5, the 6th request in <60s should block."""
        rl = RateLimiter(max_requests_per_minute=5)

        # Exhaust the limit
        for _ in range(5):
            await rl.acquire()

        # The 6th should block. We use wait_for with a timeout to verify
        # it would have waited.
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(rl.acquire(), timeout=0.2)

    @pytest.mark.asyncio
    async def test_enforces_with_limit_1(self):
        """With limit=1, second request within window should block."""
        rl = RateLimiter(max_requests_per_minute=1)
        await rl.acquire()

        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(rl.acquire(), timeout=0.2)


class TestRateLimiterSlidingWindow:
    """Sliding window expiry should free up slots."""

    @pytest.mark.asyncio
    async def test_slot_freed_after_window_expires(self):
        """After the oldest timestamp expires (60s), a new request should pass.

        We simulate this by manipulating the internal timestamp deque.
        """
        rl = RateLimiter(max_requests_per_minute=2)

        # Manually insert old timestamps to simulate requests from >60s ago
        old_time = time.monotonic() - 61.0
        rl._timestamps.append(old_time)
        rl._timestamps.append(old_time + 0.1)

        # Even though there are 2 timestamps (at capacity), they are old
        # so acquire should succeed immediately after purging
        start = time.monotonic()
        await rl.acquire()
        elapsed = time.monotonic() - start
        assert elapsed < 0.5

    @pytest.mark.asyncio
    async def test_mixed_old_and_new_timestamps(self):
        """Old timestamps get purged but recent ones still count."""
        rl = RateLimiter(max_requests_per_minute=2)

        # One old (expired) and one recent
        old_time = time.monotonic() - 61.0
        recent_time = time.monotonic() - 1.0
        rl._timestamps.append(old_time)
        rl._timestamps.append(recent_time)

        # After purging the old one, we have 1 slot used, 1 free
        start = time.monotonic()
        await rl.acquire()
        elapsed = time.monotonic() - start
        assert elapsed < 0.5

        # Now at capacity (recent + the one we just acquired)
        # Third should block
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(rl.acquire(), timeout=0.2)


class TestRateLimiterConcurrency:
    """Multiple concurrent coroutines should be handled safely."""

    @pytest.mark.asyncio
    async def test_concurrent_acquires_respect_limit(self):
        """Launch 10 concurrent acquire() calls with limit=3.

        Only 3 should complete quickly; others should be waiting.
        """
        rl = RateLimiter(max_requests_per_minute=3)
        completed = []

        async def _try_acquire(idx):
            await rl.acquire()
            completed.append(idx)

        # Create 10 tasks, give them 0.3s to run
        tasks = [asyncio.create_task(_try_acquire(i)) for i in range(10)]
        done, pending = await asyncio.wait(tasks, timeout=0.3)

        # At most 3 should have completed (the limit)
        assert len(completed) <= 3

        # Clean up pending tasks
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
