"""Tests for `AnalysisSession` — event fan-out, partial-stream guarantees,
fault tolerance, and lifecycle cleanup.
"""

from __future__ import annotations

import asyncio
from typing import List

import pytest

from hindsight.orchestration import (
    AnalysisContext,
    AnalysisEvent,
    AnalysisSession,
    FunctionCompleteEvent,
    FunctionFailedEvent,
    FunctionStartEvent,
    RunCompletedEvent,
    RunFailedEvent,
    RunStartedEvent,
)


def _ctx(tmp_path) -> AnalysisContext:
    return AnalysisContext.from_config(
        repo_path=str(tmp_path / "repo"),
        config={"model": "claude-sonnet-4-5"},
        output_base_dir=str(tmp_path / "out"),
        api_key="dummy",
    )


# ----------------------------------------------------------------------
# Event serialization
# ----------------------------------------------------------------------


def test_event_to_dict_carries_type_discriminator():
    e = FunctionStartEvent(
        pipeline="code",
        function_name="foo",
        file_path="a.swift",
        index=1,
        total=10,
    )
    d = e.to_dict()
    assert d["type"] == "function_started"
    assert d["function_name"] == "foo"
    assert d["index"] == 1


def test_run_failed_event_distinct_from_completed():
    completed = RunCompletedEvent(
        pipeline="code", successful=5, failed=0, cached=2, duration_seconds=1.0
    )
    failed = RunFailedEvent(
        pipeline="code", error="boom", successful=3, failed=1, cached=0, duration_seconds=0.5
    )
    assert completed.to_dict()["type"] == "run_completed"
    assert failed.to_dict()["type"] == "run_failed"
    assert "error" in failed.to_dict()


# ----------------------------------------------------------------------
# Push subscribers
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_session_emit_fans_out_to_sync_subscriber(tmp_path):
    received: List[AnalysisEvent] = []
    async with AnalysisSession.create(_ctx(tmp_path)) as session:
        session.subscribe(received.append)
        await session.emit(RunStartedEvent(pipeline="code", total_units=3))
        await session.emit(FunctionCompleteEvent(
            pipeline="code", function_name="foo", file_path="a.swift",
            issues=[], duration_seconds=0.1, cached=False, index=1, total=3,
        ))
    assert [e.type for e in received] == ["run_started", "function_complete"]


@pytest.mark.asyncio
async def test_session_emit_awaits_async_subscriber(tmp_path):
    received: List[AnalysisEvent] = []

    async def callback(event):
        await asyncio.sleep(0)
        received.append(event)

    async with AnalysisSession.create(_ctx(tmp_path)) as session:
        session.subscribe(callback)
        await session.emit(RunStartedEvent(pipeline="code", total_units=1))
    assert len(received) == 1


@pytest.mark.asyncio
async def test_subscriber_exception_does_not_break_other_subscribers(tmp_path):
    """A misbehaving subscriber must not starve other subscribers or break emit."""
    good: List[AnalysisEvent] = []

    def bad(event):
        raise RuntimeError("subscriber bug")

    async with AnalysisSession.create(_ctx(tmp_path)) as session:
        session.subscribe(bad)
        session.subscribe(good.append)
        await session.emit(RunStartedEvent(pipeline="code", total_units=1))
        await session.emit(RunCompletedEvent(
            pipeline="code", successful=1, failed=0, cached=0, duration_seconds=0.1
        ))
    assert [e.type for e in good] == ["run_started", "run_completed"]


@pytest.mark.asyncio
async def test_unsubscribe(tmp_path):
    received: List[AnalysisEvent] = []
    async with AnalysisSession.create(_ctx(tmp_path)) as session:
        unsub = session.subscribe(received.append)
        await session.emit(RunStartedEvent(pipeline="code", total_units=1))
        unsub()
        await session.emit(RunCompletedEvent(
            pipeline="code", successful=1, failed=0, cached=0, duration_seconds=0.1
        ))
    assert [e.type for e in received] == ["run_started"]


# ----------------------------------------------------------------------
# Pull-based async iterator (the FastAPI-streaming path)
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_events_iterator_receives_in_order(tmp_path):
    """The async iterator path is the FastAPI WebSocket/SSE consumer's mode."""
    session = AnalysisSession.create(_ctx(tmp_path))
    received: List[AnalysisEvent] = []

    async def consumer():
        async for event in session.events():
            received.append(event)

    consumer_task = asyncio.create_task(consumer())
    await asyncio.sleep(0)  # let consumer subscribe
    await session.emit(RunStartedEvent(pipeline="code", total_units=2))
    await session.emit(FunctionStartEvent(
        pipeline="code", function_name="foo", file_path="a.swift", index=1, total=2,
    ))
    await session.emit(FunctionCompleteEvent(
        pipeline="code", function_name="foo", file_path="a.swift",
        issues=[{"issue": "x"}], duration_seconds=0.1, cached=False, index=1, total=2,
    ))
    await session.aclose()
    await consumer_task
    assert [e.type for e in received] == [
        "run_started",
        "function_started",
        "function_complete",
    ]


@pytest.mark.asyncio
async def test_partial_stream_visible_after_failure_event(tmp_path):
    """Mid-run failure → consumers see partial events + RunFailedEvent.

    This is the FastAPI partial-result guarantee. The consumer sees
    function_complete events for the work that succeeded, then a run_failed
    event for the abort. Nothing is silently lost.
    """
    session = AnalysisSession.create(_ctx(tmp_path))
    received: List[AnalysisEvent] = []

    async def consumer():
        async for event in session.events():
            received.append(event)

    consumer_task = asyncio.create_task(consumer())
    await asyncio.sleep(0)

    # Simulate a pipeline that processed 2 of 3 functions then crashed.
    await session.emit(RunStartedEvent(pipeline="code", total_units=3))
    await session.emit(FunctionCompleteEvent(
        pipeline="code", function_name="f1", file_path="a.swift",
        issues=[{"issue": "found one"}], duration_seconds=0.1, cached=False, index=1, total=3,
    ))
    await session.emit(FunctionCompleteEvent(
        pipeline="code", function_name="f2", file_path="b.swift",
        issues=[], duration_seconds=0.1, cached=False, index=2, total=3,
    ))
    await session.emit(RunFailedEvent(
        pipeline="code", error="LLM provider timed out repeatedly",
        successful=2, failed=0, cached=0, duration_seconds=5.0,
    ))
    await session.aclose()
    await consumer_task

    types = [e.type for e in received]
    assert types == ["run_started", "function_complete", "function_complete", "run_failed"]
    # The first function's issues were delivered before the crash.
    assert received[1].issues == [{"issue": "found one"}]


@pytest.mark.asyncio
async def test_two_iterator_consumers_both_receive_events(tmp_path):
    """Multiple WebSocket connections can subscribe simultaneously."""
    session = AnalysisSession.create(_ctx(tmp_path))
    a: List[str] = []
    b: List[str] = []

    async def reader(into: List[str]):
        async for event in session.events():
            into.append(event.type)

    ta = asyncio.create_task(reader(a))
    tb = asyncio.create_task(reader(b))
    await asyncio.sleep(0)
    await session.emit(RunStartedEvent(pipeline="code", total_units=1))
    await session.emit(RunCompletedEvent(
        pipeline="code", successful=1, failed=0, cached=0, duration_seconds=0.1,
    ))
    await session.aclose()
    await asyncio.gather(ta, tb)
    assert a == ["run_started", "run_completed"]
    assert b == ["run_started", "run_completed"]


@pytest.mark.asyncio
async def test_emit_after_close_is_noop(tmp_path):
    """Closing the session terminates emit so a stale task can't reanimate it."""
    session = AnalysisSession.create(_ctx(tmp_path))
    received: List[AnalysisEvent] = []
    session.subscribe(received.append)
    await session.aclose()
    await session.emit(RunStartedEvent(pipeline="code", total_units=1))
    assert received == []


@pytest.mark.asyncio
async def test_aclose_is_idempotent(tmp_path):
    session = AnalysisSession.create(_ctx(tmp_path))
    await session.aclose()
    await session.aclose()  # must not raise
