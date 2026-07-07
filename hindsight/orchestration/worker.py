"""Bounded async fan-out with rate limiting and per-item fault isolation.

Replaces `hindsight.core.async_infra.worker_pool.run_worker_pool`. The new
contract is stricter:

  - Concurrency is gated by a semaphore (not a queue).
  - An optional `AsyncRateLimiter` gates LLM calls.
  - Each item runs in its own try/except — a thrown exception becomes a
    `WorkerOutcome` with `error` set, never a cancellation of siblings.
  - The function returns a list of outcomes in input order; callers can map
    them back to inputs without an `on_error` callback.

This means: one runaway function can never abort the whole repo analysis.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Generic, List, Optional, TypeVar

from hindsight.utils.log_util import get_logger

from hindsight.llm import AsyncRateLimiter

logger = get_logger(__name__)


T = TypeVar("T")
R = TypeVar("R")


@dataclass
class WorkerOutcome(Generic[T, R]):
    """Result of one worker invocation.

    Exactly one of `result` and `error` is set. `duration_seconds` is measured
    from just after the semaphore is acquired, so it covers the rate-limiter
    wait plus the `fn` call itself but NOT time spent queued on the semaphore
    (still a useful telemetry signal for throughput tuning).
    """

    item: T
    index: int                       # 0-based index in the input list
    result: Optional[R] = None
    error: Optional[str] = None
    duration_seconds: float = 0.0

    @property
    def ok(self) -> bool:
        return self.error is None

async def bounded_gather(
    items: List[T],
    fn: Callable[[T], Awaitable[R]],
    *,
    max_concurrency: int,
    rate_limiter: Optional[AsyncRateLimiter] = None,
    cancel_check: Optional[Callable[[], bool]] = None,
) -> List[WorkerOutcome[T, R]]:
    """Run `fn` against every item with bounded concurrency.

    Args:
        items: Input list. Outcomes come back in the same order.
        fn:    Per-item async worker. Must be a coroutine function.
        max_concurrency: Max number of `fn` invocations in flight at once.
            Coerced to >= 1 to avoid deadlocking on bad config.
        rate_limiter: Optional limiter awaited before each `fn` call. Keeps
            the LLM provider from getting hammered when concurrency is high.
        cancel_check: Optional sync predicate polled before each item. If it
            returns True, the remaining items are skipped (their outcomes
            still appear in the result with `error="cancelled"`).

    Returns:
        A list of `WorkerOutcome` in input order. Errors do NOT propagate.
    """
    if not items:
        return []
    bound = max(1, int(max_concurrency))
    sem = asyncio.Semaphore(bound)

    async def _run_one(index: int, item: T) -> WorkerOutcome[T, R]:
        if cancel_check is not None and cancel_check():
            return WorkerOutcome(item=item, index=index, error="cancelled")

        async with sem:
            # Re-check after acquiring — between scheduling and now the run
            # may have been cancelled.
            if cancel_check is not None and cancel_check():
                return WorkerOutcome(item=item, index=index, error="cancelled")

            t0 = time.monotonic()
            try:
                if rate_limiter is not None:
                    await rate_limiter.acquire()
                result = await fn(item)
                return WorkerOutcome(
                    item=item,
                    index=index,
                    result=result,
                    duration_seconds=time.monotonic() - t0,
                )
            except asyncio.CancelledError:
                # Let cancellation propagate so the surrounding task can finish.
                raise
            except Exception as exc:  # noqa: BLE001 — intentional: catch ALL per-item errors
                logger.error(
                    f"bounded_gather item {index} raised {type(exc).__name__}: {exc}"
                )
                return WorkerOutcome(
                    item=item,
                    index=index,
                    error=f"{type(exc).__name__}: {exc}",
                    duration_seconds=time.monotonic() - t0,
                )

    # gather() collects outcomes; since _run_one never raises (except for
    # CancelledError), we get a clean ordered list back.
    return await asyncio.gather(*[_run_one(i, item) for i, item in enumerate(items)])


def summarize(outcomes: List[WorkerOutcome[Any, Any]]) -> tuple[int, int]:
    """Return `(successful, failed)` counts."""
    ok = sum(1 for o in outcomes if o.ok)
    return ok, len(outcomes) - ok
