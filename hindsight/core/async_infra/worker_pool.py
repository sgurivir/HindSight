"""
Generic async worker pool for parallel task execution.

Extracted from the _batch_worker pattern in external_input_analyzer.py
and sink_analyzer.py. Provides a reusable function that processes a list
of items through N concurrent workers with optional rate limiting.

Usage:
    results = await run_worker_pool(
        items=file_paths,
        worker_fn=analyze_file,
        max_workers=4,
        rate_limiter=limiter,
    )
"""

import asyncio
from typing import (
    Awaitable,
    Callable,
    List,
    Optional,
    Tuple,
    TypeVar,
)

from ...utils.log_util import get_logger

logger = get_logger(__name__)

T = TypeVar("T")
R = TypeVar("R")


async def run_worker_pool(
    items: "List[T]",
    worker_fn: "Callable[[T], Awaitable[R]]",
    max_workers: int,
    rate_limiter=None,
    on_result: "Optional[Callable[[T, R], None]]" = None,
    on_error: "Optional[Callable[[T, Exception], None]]" = None,
) -> "List[Tuple[T, R]]":
    """
    Process a list of items through an async worker pool.

    Spawns up to max_workers concurrent asyncio tasks that pull items from
    a shared queue. Each worker optionally acquires the rate limiter before
    calling worker_fn. Results are collected and returned as (item, result)
    tuples.

    Args:
        items: List of items to process. Each item is passed to worker_fn.
        worker_fn: Async callable that takes a single item and returns a result.
        max_workers: Maximum number of concurrent worker tasks.
        rate_limiter: Optional RateLimiter instance. If provided, each worker
            calls rate_limiter.acquire() before invoking worker_fn.
        on_result: Optional callback(item, result) invoked after each
            successful worker_fn call. Called in the worker's task context.
        on_error: Optional callback(item, exception) invoked when worker_fn
            raises an exception. If provided, the pool continues processing
            remaining items. The failed item will NOT appear in the returned
            results list.

    Returns:
        List of (item, result) tuples for all successfully processed items.
        Order is not guaranteed to match the input order.

    Raises:
        No exceptions are raised from this function. Errors in worker_fn are
        either passed to on_error or logged and skipped.
    """
    if not items:
        return []

    if max_workers <= 0:
        raise ValueError("max_workers must be positive")

    # Use a queue to distribute work to workers
    queue: asyncio.Queue = asyncio.Queue()
    for item in items:
        queue.put_nowait(item)

    results: List[Tuple] = []
    results_lock = asyncio.Lock()

    async def _worker() -> None:
        while True:
            try:
                item = queue.get_nowait()
            except asyncio.QueueEmpty:
                break

            try:
                # Acquire rate limiter slot if provided
                if rate_limiter is not None:
                    await rate_limiter.acquire()

                # Execute the worker function
                result = await worker_fn(item)

                # Collect the result
                async with results_lock:
                    results.append((item, result))

                # Notify callback if provided
                if on_result is not None:
                    try:
                        on_result(item, result)
                    except Exception as cb_err:
                        logger.warning(
                            f"on_result callback raised: {cb_err}"
                        )

            except Exception as exc:
                # Handle errors gracefully
                if on_error is not None:
                    try:
                        on_error(item, exc)
                    except Exception as cb_err:
                        logger.warning(
                            f"on_error callback raised: {cb_err}"
                        )
                else:
                    logger.error(
                        f"Worker error processing item {item!r}: {exc}"
                    )
            finally:
                queue.task_done()

    # Spawn workers — no more than the number of items
    num_workers = min(max_workers, len(items))
    workers = [asyncio.create_task(_worker()) for _ in range(num_workers)]

    # Wait for all workers to finish
    await asyncio.gather(*workers)

    return results
