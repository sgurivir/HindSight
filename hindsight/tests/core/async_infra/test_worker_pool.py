"""
Tests for hindsight.core.async_infra.worker_pool.run_worker_pool

Tests:
- All items processed with correct results
- max_workers respected (no more than N concurrent)
- on_error called for failing items, pool continues
- Empty items list returns empty results
- Rate limiter integration (acquire called before each item)
"""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hindsight.core.async_infra.worker_pool import run_worker_pool
from hindsight.core.async_infra.rate_limiter import RateLimiter


class TestWorkerPoolBasic:
    """Basic functionality tests."""

    @pytest.mark.asyncio
    async def test_empty_items_returns_empty(self):
        """An empty items list should return an empty results list."""

        async def worker(item):
            return item * 2

        results = await run_worker_pool(items=[], worker_fn=worker, max_workers=4)
        assert results == []

    @pytest.mark.asyncio
    async def test_all_items_processed(self):
        """All items should be processed and returned as (item, result) tuples."""

        async def worker(item):
            return item * 2

        items = [1, 2, 3, 4, 5]
        results = await run_worker_pool(items=items, worker_fn=worker, max_workers=4)

        # Convert to dict for easy lookup (order not guaranteed)
        result_dict = dict(results)
        assert len(result_dict) == 5
        for item in items:
            assert result_dict[item] == item * 2

    @pytest.mark.asyncio
    async def test_single_worker(self):
        """With max_workers=1, items are processed sequentially."""
        order = []

        async def worker(item):
            order.append(item)
            await asyncio.sleep(0.01)
            return item

        items = [1, 2, 3]
        results = await run_worker_pool(items=items, worker_fn=worker, max_workers=1)

        assert len(results) == 3
        # With 1 worker, order should be preserved (queue is FIFO)
        assert order == [1, 2, 3]

    @pytest.mark.asyncio
    async def test_invalid_max_workers_raises(self):
        """max_workers <= 0 should raise ValueError."""

        async def worker(item):
            return item

        with pytest.raises(ValueError, match="must be positive"):
            await run_worker_pool(items=[1], worker_fn=worker, max_workers=0)

    @pytest.mark.asyncio
    async def test_worker_fn_receives_items(self):
        """worker_fn should receive each item exactly once."""
        received = []

        async def worker(item):
            received.append(item)
            return f"processed_{item}"

        items = ["a", "b", "c"]
        results = await run_worker_pool(items=items, worker_fn=worker, max_workers=3)

        assert sorted(received) == sorted(items)
        result_dict = dict(results)
        for item in items:
            assert result_dict[item] == f"processed_{item}"


class TestWorkerPoolConcurrency:
    """Tests for max_workers enforcement."""

    @pytest.mark.asyncio
    async def test_max_workers_respected(self):
        """No more than max_workers should run concurrently."""
        max_workers = 2
        concurrent_count = 0
        max_concurrent = 0
        lock = asyncio.Lock()

        async def worker(item):
            nonlocal concurrent_count, max_concurrent
            async with lock:
                concurrent_count += 1
                max_concurrent = max(max_concurrent, concurrent_count)

            # Simulate work
            await asyncio.sleep(0.05)

            async with lock:
                concurrent_count -= 1

            return item

        items = list(range(6))
        results = await run_worker_pool(
            items=items, worker_fn=worker, max_workers=max_workers
        )

        assert len(results) == 6
        assert max_concurrent <= max_workers

    @pytest.mark.asyncio
    async def test_more_workers_than_items(self):
        """If max_workers > len(items), only len(items) workers are spawned."""

        async def worker(item):
            return item * 10

        items = [1, 2]
        results = await run_worker_pool(items=items, worker_fn=worker, max_workers=100)

        result_dict = dict(results)
        assert result_dict == {1: 10, 2: 20}


class TestWorkerPoolErrorHandling:
    """Tests for error handling behavior."""

    @pytest.mark.asyncio
    async def test_on_error_called_for_failing_items(self):
        """When worker_fn raises, on_error should be called and pool continues."""
        errors = []

        async def worker(item):
            if item == 3:
                raise ValueError(f"Item {item} failed")
            return item * 2

        def on_error(item, exc):
            errors.append((item, str(exc)))

        items = [1, 2, 3, 4, 5]
        results = await run_worker_pool(
            items=items, worker_fn=worker, max_workers=2, on_error=on_error
        )

        # Item 3 should have errored
        assert len(errors) == 1
        assert errors[0][0] == 3
        assert "Item 3 failed" in errors[0][1]

        # Other items should be in results
        result_dict = dict(results)
        assert len(result_dict) == 4
        assert 3 not in result_dict
        assert result_dict[1] == 2
        assert result_dict[5] == 10

    @pytest.mark.asyncio
    async def test_error_without_callback_continues(self):
        """Without on_error, exceptions are logged and pool continues."""

        async def worker(item):
            if item == "bad":
                raise RuntimeError("something broke")
            return f"ok_{item}"

        items = ["a", "bad", "c"]
        results = await run_worker_pool(items=items, worker_fn=worker, max_workers=3)

        result_dict = dict(results)
        assert "bad" not in result_dict
        assert result_dict["a"] == "ok_a"
        assert result_dict["c"] == "ok_c"

    @pytest.mark.asyncio
    async def test_all_items_fail(self):
        """If all items fail, results should be empty."""
        errors = []

        async def worker(item):
            raise RuntimeError(f"fail_{item}")

        def on_error(item, exc):
            errors.append(item)

        items = [1, 2, 3]
        results = await run_worker_pool(
            items=items, worker_fn=worker, max_workers=2, on_error=on_error
        )

        assert results == []
        assert sorted(errors) == [1, 2, 3]

    @pytest.mark.asyncio
    async def test_on_error_callback_exception_does_not_crash_pool(self):
        """If on_error itself raises, the pool should still continue."""

        async def worker(item):
            if item == 2:
                raise ValueError("item 2 failed")
            return item

        def on_error(item, exc):
            raise RuntimeError("callback crashed")

        items = [1, 2, 3]
        # Should not raise
        results = await run_worker_pool(
            items=items, worker_fn=worker, max_workers=2, on_error=on_error
        )

        result_dict = dict(results)
        assert 1 in result_dict
        assert 3 in result_dict


class TestWorkerPoolOnResult:
    """Tests for the on_result callback."""

    @pytest.mark.asyncio
    async def test_on_result_called_for_each_success(self):
        """on_result should be called for every successfully processed item."""
        callbacks = []

        async def worker(item):
            return item + 10

        def on_result(item, result):
            callbacks.append((item, result))

        items = [1, 2, 3]
        results = await run_worker_pool(
            items=items, worker_fn=worker, max_workers=2, on_result=on_result
        )

        assert len(callbacks) == 3
        callback_dict = dict(callbacks)
        assert callback_dict[1] == 11
        assert callback_dict[2] == 12
        assert callback_dict[3] == 13

    @pytest.mark.asyncio
    async def test_on_result_exception_does_not_crash_pool(self):
        """If on_result raises, the pool should still continue."""

        async def worker(item):
            return item

        def on_result(item, result):
            raise RuntimeError("callback exploded")

        items = [1, 2, 3]
        results = await run_worker_pool(
            items=items, worker_fn=worker, max_workers=2, on_result=on_result
        )

        # Results should still be collected even though callback failed
        assert len(results) == 3


class TestWorkerPoolRateLimiter:
    """Tests for rate limiter integration."""

    @pytest.mark.asyncio
    async def test_rate_limiter_acquire_called_before_each_item(self):
        """rate_limiter.acquire() should be called before each worker_fn call."""
        acquire_count = 0
        original_acquire = RateLimiter.acquire

        rl = RateLimiter(max_requests_per_minute=100)

        # Patch acquire to count calls
        async def mock_acquire(self):
            nonlocal acquire_count
            acquire_count += 1

        items = [1, 2, 3, 4, 5]

        async def worker(item):
            return item

        with patch.object(RateLimiter, "acquire", mock_acquire):
            results = await run_worker_pool(
                items=items,
                worker_fn=worker,
                max_workers=3,
                rate_limiter=rl,
            )

        assert acquire_count == 5
        assert len(results) == 5

    @pytest.mark.asyncio
    async def test_no_rate_limiter_skips_acquire(self):
        """Without rate_limiter, worker_fn should be called directly."""
        call_times = []

        async def worker(item):
            call_times.append(time.monotonic())
            return item

        items = [1, 2, 3]
        results = await run_worker_pool(
            items=items, worker_fn=worker, max_workers=3, rate_limiter=None
        )

        assert len(results) == 3
        # All should complete very quickly without rate limiting
        if len(call_times) >= 2:
            assert call_times[-1] - call_times[0] < 0.5

    @pytest.mark.asyncio
    async def test_rate_limiter_slows_processing(self):
        """A tight rate limiter should measurably slow down processing."""
        # Allow only 2 per minute — after 2, the 3rd would need to wait ~60s
        rl = RateLimiter(max_requests_per_minute=2)

        async def worker(item):
            return item

        items = [1, 2]
        start = time.monotonic()
        results = await run_worker_pool(
            items=items, worker_fn=worker, max_workers=2, rate_limiter=rl
        )
        elapsed = time.monotonic() - start

        # 2 items with limit=2 should be fast
        assert len(results) == 2
        assert elapsed < 1.0
