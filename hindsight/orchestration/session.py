"""AnalysisSession — long-lived, FastAPI-facing handle for one repo.

What it owns:
  - `AsyncLLMClient` (httpx pool, reused across every stage call)
  - `ConversationLogger` (per-session, no class-statics → safe under concurrency)
  - `ToolRegistry` + `ToolContext` (one per repo, all tools pre-registered)
  - `AsyncRateLimiter`
  - The event fan-out machinery for subscribers + async iterator consumers

What it does NOT own:
  - Pipeline logic. Pipelines (CodePipeline / DiffPipeline / TracePipeline)
    are constructed on demand against the session and run independently.
  - Result store. Pipelines pass an `AsyncResultSink` into themselves; the
    session is agnostic to which publisher is in use.

Streaming model:

    async with AnalysisSession.create(ctx) as session:
        # Push-based: subscribe a callback.
        unsub = session.subscribe(my_callback)

        # Pull-based: async iterator, FastAPI/WS-friendly.
        async for event in session.events():
            await ws.send_json(event.to_dict())

Both modes can be active simultaneously — events fan out to every consumer.
A consumer that disconnects (stops iterating) is reaped silently.

If a pipeline crashes mid-run, the session emits `RunFailedEvent` and the
stream ends cleanly. Per-function failures emit `FunctionFailedEvent` but
the run continues — partial results stay visible in the event stream and on
disk (because `AsyncResultSink.publish` is write-through).
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, AsyncIterator, Callable, List, Optional

from hindsight.llm import (
    AsyncLLMClient,
    AsyncRateLimiter,
    ConversationLogger,
    LLMClientConfig,
)
from hindsight.llm.tools import ToolContext, ToolRegistry, build_default_registry
from hindsight.utils.log_util import get_logger

from .context import AnalysisContext
from .events import AnalysisEvent, EventCallback

logger = get_logger(__name__)


_QUEUE_SENTINEL = object()  # signals "stream done" to async iterator consumers


class AnalysisSession:
    """Per-repo runtime handle. Construct via `AnalysisSession.create(ctx)`.

    Lifecycle:
        async with AnalysisSession.create(ctx) as session:
            ...  # pipelines call `session.emit(...)`; consumers receive events

    The async-context-manager form is the recommended path because it
    guarantees the httpx pool gets closed. Calling `aclose()` manually also
    works for FastAPI handlers that manage lifecycle elsewhere.
    """

    def __init__(
        self,
        ctx: AnalysisContext,
        *,
        llm: AsyncLLMClient,
        logger_: ConversationLogger,
        tools: ToolRegistry,
        rate_limiter: AsyncRateLimiter,
    ):
        self.ctx = ctx
        self.llm = llm
        self.conversation_logger = logger_
        self.tools = tools
        self.rate_limiter = rate_limiter

        # Fan-out machinery.
        self._subscribers: List[EventCallback] = []
        self._queues: List[asyncio.Queue[Any]] = []
        self._emit_lock = asyncio.Lock()
        self._closed = False

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    @classmethod
    def create(
        cls,
        ctx: AnalysisContext,
        *,
        file_content_provider: Any = None,
        directory_tree_util: Any = None,
        analyzer_name: str = "code_analysis",
    ) -> "AnalysisSession":
        """Build a session and all its long-lived components.

        `file_content_provider` and `directory_tree_util` are passed straight
        through to the tool context — they're shared with the rest of the
        codebase. They can be None in tests; tools that need them will
        return a helpful error in that case (preserved from legacy behavior).

        `analyzer_name` selects the conversation logger's subdirectory under
        `{artifacts}/prompts_sent/`. Defaults to "code_analysis" so the most
        common entry point (CodePipeline) needs no extra config; the diff and
        trace pipelines pass their own values to preserve the legacy
        on-disk layout.
        """
        ctx.ensure_directories()

        llm = AsyncLLMClient(
            LLMClientConfig(
                api_url=ctx.api_url,
                model=ctx.model,
                max_tokens=ctx.max_tokens,
                api_key=ctx.api_key,
            )
        )
        conv_logger = ConversationLogger(ctx.artifacts_dir, analyzer=analyzer_name)
        tool_ctx = ToolContext(
            repo_path=ctx.repo_path,
            file_content_provider=file_content_provider,
            artifacts_dir=ctx.code_insights_dir,
            directory_tree_util=directory_tree_util,
            ignore_dirs=set(ctx.exclude_directories),
        )
        tools = build_default_registry(tool_ctx)
        rate_limiter = AsyncRateLimiter(
            max_requests=ctx.rate_limit,
            window_seconds=ctx.rate_window_seconds,
        )
        return cls(ctx, llm=llm, logger_=conv_logger, tools=tools, rate_limiter=rate_limiter)

    # ------------------------------------------------------------------
    # Async-context-manager lifecycle
    # ------------------------------------------------------------------

    async def __aenter__(self) -> "AnalysisSession":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Release the httpx pool and signal all iterator consumers to stop.

        Safe to call more than once. After this, subsequent `emit()` calls
        are no-ops (so a misbehaving pipeline can't reanimate the session).
        """
        if self._closed:
            return
        self._closed = True
        # Wake every iterator with the sentinel so they exit cleanly.
        for q in self._queues:
            try:
                q.put_nowait(_QUEUE_SENTINEL)
            except Exception as exc:  # noqa: BLE001
                logger.debug(f"queue sentinel put failed: {exc}")
        try:
            await self.llm.aclose()
        except Exception as exc:  # noqa: BLE001 — closing pool must never raise
            logger.warning(f"AsyncLLMClient.aclose failed: {exc}")

    # ------------------------------------------------------------------
    # Event fan-out
    # ------------------------------------------------------------------

    def subscribe(self, callback: EventCallback) -> Callable[[], None]:
        """Register a push-based event callback.

        The callback can be sync or async. It receives every event the
        session emits. Exceptions in the callback are caught — they will not
        break the pipeline or other subscribers. Returns an unsubscribe fn.
        """
        self._subscribers.append(callback)

        def _unsubscribe() -> None:
            try:
                self._subscribers.remove(callback)
            except ValueError:
                pass

        return _unsubscribe

    async def events(self, *, maxsize: int = 0) -> AsyncIterator[AnalysisEvent]:
        """Pull-based event stream.

        Returns an async iterator. The consumer applies natural back-pressure
        (events queue up until consumed); set `maxsize` to bound the queue
        if you want emit to wait on a slow consumer instead.

        FastAPI usage::

            @app.websocket("/run")
            async def run(ws):
                async with AnalysisSession.create(ctx) as session:
                    asyncio.create_task(session.pipeline_code().analyze_repo())
                    async for event in session.events():
                        await ws.send_json(event.to_dict())

        The iterator terminates after the session is closed (either via
        `aclose()` or by exiting the async-with block). Mid-run pipeline
        crashes emit `RunFailedEvent`; consumers see that, then the stream
        ends.
        """
        q: asyncio.Queue[Any] = asyncio.Queue(maxsize=maxsize)
        self._queues.append(q)
        try:
            while True:
                item = await q.get()
                if item is _QUEUE_SENTINEL:
                    return
                yield item
        finally:
            try:
                self._queues.remove(q)
            except ValueError:
                pass

    async def emit(self, event: AnalysisEvent) -> None:
        """Fan an event out to every subscriber and iterator queue.

        Sync subscribers run inline; async ones are awaited in order.
        Subscriber exceptions are caught and logged so a buggy callback
        cannot break the pipeline or starve other consumers. After the
        session is closed, this is a no-op.
        """
        if self._closed:
            return

        # Serialize emit() across concurrent tasks so subscribers see events
        # in the order they were emitted (not interleaved by event loop turns).
        async with self._emit_lock:
            for q in list(self._queues):
                try:
                    # Use put_nowait when queue is unbounded; otherwise await
                    # so a slow consumer back-pressures the pipeline.
                    if q.maxsize == 0:
                        q.put_nowait(event)
                    else:
                        await q.put(event)
                except asyncio.QueueFull:
                    # Should be unreachable when we await put; defensive log.
                    logger.warning("Event queue full; dropping event")
                except Exception as exc:  # noqa: BLE001
                    logger.warning(f"Event queue put failed: {exc}")

            for callback in list(self._subscribers):
                try:
                    res = callback(event)
                    if asyncio.iscoroutine(res):
                        await res
                except Exception as exc:  # noqa: BLE001 — subscriber bug must not crash pipeline
                    logger.warning(
                        f"Subscriber {callback!r} raised on {event.type}: "
                        f"{type(exc).__name__}: {exc}"
                    )
