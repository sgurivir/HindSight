"""Tests for `bounded_gather`, `AsyncResultSink`, and the function/affected
selectors. All run without network or the real LLM stack.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

import pytest

from hindsight.llm import AsyncRateLimiter
from hindsight.orchestration import (
    AsyncResultSink,
    FunctionFilters,
    FunctionWorkItem,
    bounded_gather,
    get_function_line_count,
    select_functions,
)
from hindsight.orchestration.worker import summarize


# ----------------------------------------------------------------------
# bounded_gather — concurrency, rate limiting, fault isolation
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bounded_gather_returns_results_in_input_order():
    async def fn(i: int) -> int:
        await asyncio.sleep(0.01 * (5 - i))  # later items finish first
        return i * 10

    outcomes = await bounded_gather(list(range(5)), fn, max_concurrency=5)
    assert [o.index for o in outcomes] == [0, 1, 2, 3, 4]
    assert [o.result for o in outcomes] == [0, 10, 20, 30, 40]
    assert all(o.ok for o in outcomes)


@pytest.mark.asyncio
async def test_bounded_gather_isolates_one_failure():
    async def fn(i: int) -> int:
        if i == 2:
            raise RuntimeError("boom")
        return i

    outcomes = await bounded_gather([0, 1, 2, 3], fn, max_concurrency=2)
    assert [o.ok for o in outcomes] == [True, True, False, True]
    assert outcomes[2].error is not None
    assert "RuntimeError: boom" in outcomes[2].error
    ok, failed = summarize(outcomes)
    assert (ok, failed) == (3, 1)


@pytest.mark.asyncio
async def test_bounded_gather_respects_max_concurrency():
    in_flight = 0
    peak = 0

    async def fn(i: int) -> int:
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.02)
        in_flight -= 1
        return i

    await bounded_gather(list(range(10)), fn, max_concurrency=3)
    assert peak <= 3


@pytest.mark.asyncio
async def test_bounded_gather_handles_rate_limiter():
    rl = AsyncRateLimiter(max_requests=2, window_seconds=0.3)

    async def fn(i: int) -> int:
        return i

    import time
    t0 = time.monotonic()
    await bounded_gather([0, 1, 2, 3], fn, max_concurrency=4, rate_limiter=rl)
    elapsed = time.monotonic() - t0
    # 3rd and 4th calls must wait for the window — at least ~0.3s.
    assert elapsed >= 0.25


@pytest.mark.asyncio
async def test_bounded_gather_cancel_check_skips_remaining():
    seen_indices: list[int] = []
    cancel_after = 2

    async def fn(i: int) -> int:
        seen_indices.append(i)
        await asyncio.sleep(0.01)
        return i

    def cancel_check() -> bool:
        return len(seen_indices) >= cancel_after

    outcomes = await bounded_gather(
        list(range(10)), fn, max_concurrency=1, cancel_check=cancel_check
    )
    cancelled = [o for o in outcomes if o.error == "cancelled"]
    # At least some were cancelled.
    assert len(cancelled) >= 1


@pytest.mark.asyncio
async def test_bounded_gather_handles_empty_list():
    async def fn(i: int) -> int:
        return i

    outcomes = await bounded_gather([], fn, max_concurrency=4)
    assert outcomes == []


@pytest.mark.asyncio
async def test_bounded_gather_coerces_zero_concurrency_to_one():
    """max_concurrency=0 would deadlock a semaphore; ensure we coerce to 1."""

    async def fn(i: int) -> int:
        return i

    outcomes = await bounded_gather([1, 2], fn, max_concurrency=0)
    assert [o.result for o in outcomes] == [1, 2]


# ----------------------------------------------------------------------
# AsyncResultSink — write-through + sanitization
# ----------------------------------------------------------------------


class _StubPublisher:
    """Minimal publisher that records every add_result/check_existing call."""

    def __init__(self, *, existing: Optional[Dict[str, Any]] = None, raise_on_add: bool = False):
        self.added: List[Dict[str, Any]] = []
        self.existing = existing
        self.raise_on_add = raise_on_add

    def add_result(self, **kwargs: Any) -> str:
        if self.raise_on_add:
            raise OSError("disk full")
        self.added.append(kwargs)
        return "id-" + str(len(self.added))

    def check_existing_result(self, file_name: str, function_name: str, checksum: str):
        return self.existing


@pytest.mark.asyncio
async def test_sink_sanitizes_missing_issue_fields():
    pub = _StubPublisher()
    sink = AsyncResultSink(pub, repo_name="repo")
    out = await sink.publish(
        file_path="x.swift",
        function="foo",
        checksum="abc",
        issues=[{"description": "only desc, no issue"}],  # missing 'issue', 'severity'
    )
    assert out.ok
    written = pub.added[0]["results"][0]
    assert written["issue"] == "only desc, no issue"  # falls back to description
    assert written["severity"] == "medium"            # default
    assert written["category"] == "general"           # default


@pytest.mark.asyncio
async def test_sink_drops_non_dict_issues_silently():
    """The LLM sometimes emits strings inside the array. Don't crash on that."""
    pub = _StubPublisher()
    sink = AsyncResultSink(pub, repo_name="repo")
    out = await sink.publish(
        file_path="x.swift",
        function="foo",
        checksum="abc",
        issues=[{"issue": "real"}, "stray string", 42, {"issue": "real2"}],
    )
    assert out.ok and out.issue_count == 2
    titles = [i["issue"] for i in pub.added[0]["results"]]
    assert titles == ["real", "real2"]


@pytest.mark.asyncio
async def test_sink_publish_failure_returns_error_not_raises():
    pub = _StubPublisher(raise_on_add=True)
    sink = AsyncResultSink(pub, repo_name="repo")
    out = await sink.publish(file_path="x.swift", function="foo", checksum="abc", issues=[])
    assert not out.ok
    assert "disk full" in out.error


@pytest.mark.asyncio
async def test_sink_check_existing_treats_lookup_failure_as_miss():
    class _FailingPub:
        def check_existing_result(self, *_a, **_k):
            raise RuntimeError("cache backend down")

        def add_result(self, **_k):
            return ""

    sink = AsyncResultSink(_FailingPub(), repo_name="repo")
    result = await sink.check_existing(file_path="x", function="y", checksum="z")
    assert result is None  # soft-fail


@pytest.mark.asyncio
async def test_sink_check_existing_returns_hit():
    pub = _StubPublisher(existing={"results": [{"issue": "cached"}]})
    sink = AsyncResultSink(pub, repo_name="repo")
    hit = await sink.check_existing(file_path="x", function="y", checksum="z")
    assert hit == {"results": [{"issue": "cached"}]}


# ----------------------------------------------------------------------
# function_selector — precedence rules
# ----------------------------------------------------------------------


def _make_func(name: str, file: str, start: int = 1, end: int = 10, **extra) -> Dict[str, Any]:
    func: Dict[str, Any] = {
        "function": name,
        "context": {"file": file, "start": start, "end": end},
    }
    func.update(extra)
    return func


def test_select_functions_applies_length_filter():
    call_graph = [
        {
            "file": "a.swift",
            "functions": [
                _make_func("tiny", "a.swift", 1, 3),   # 3 lines → below min 7
                _make_func("ok", "a.swift", 1, 20),    # 20 lines → keep
                _make_func("huge", "a.swift", 1, 2000),  # > max 1000 → drop
            ],
        }
    ]
    filters = FunctionFilters()  # defaults: min=7, max=1000
    out = select_functions(call_graph, filters)
    assert [w.function_name for w in out] == ["ok"]


def test_select_functions_sorts_longest_first():
    call_graph = [
        {
            "file": "a.swift",
            "functions": [
                _make_func("short", "a.swift", 1, 10),
                _make_func("long", "a.swift", 1, 100),
                _make_func("medium", "a.swift", 1, 50),
            ],
        }
    ]
    out = select_functions(call_graph, FunctionFilters())
    assert [w.function_name for w in out] == ["long", "medium", "short"]


def test_select_functions_verified_filter_wins():
    """function_filter overrides file_filter and directory filters."""
    call_graph = [
        {
            "file": "a.swift",
            "functions": [
                _make_func("foo", "a.swift", 1, 20),
                _make_func("bar", "a.swift", 1, 20),
            ],
        }
    ]
    filters = FunctionFilters(
        verified_functions=frozenset({"foo"}),
        file_filter=("b.swift",),                # would normally exclude a.swift
        exclude_directories=("/",),              # would normally exclude everything
    )
    out = select_functions(call_graph, filters)
    assert [w.function_name for w in out] == ["foo"]


def test_select_functions_file_filter_matches_basename_suffix():
    call_graph = [
        {
            "file": "src/sub/a.swift",
            "functions": [_make_func("foo", "src/sub/a.swift", 1, 20)],
        },
        {
            "file": "src/sub/b.swift",
            "functions": [_make_func("bar", "src/sub/b.swift", 1, 20)],
        },
    ]
    filters = FunctionFilters(file_filter=("a.swift",))
    out = select_functions(call_graph, filters)
    assert [w.function_name for w in out] == ["foo"]


def test_select_functions_directory_exclude():
    call_graph = [
        {
            "file": "Tests/a.swift",
            "functions": [_make_func("foo", "Tests/a.swift", 1, 20)],
        },
        {
            "file": "src/b.swift",
            "functions": [_make_func("bar", "src/b.swift", 1, 20)],
        },
    ]
    filters = FunctionFilters(exclude_directories=("Tests",))
    out = select_functions(call_graph, filters)
    assert [w.function_name for w in out] == ["bar"]


def test_select_functions_tolerates_malformed_call_graph():
    """Non-dict entries are silently skipped (the AST artifact may be partial)."""
    call_graph = [
        "not a dict",
        {"file": "ok.swift", "functions": [_make_func("foo", "ok.swift", 1, 20)]},
        42,
        {"file": "x.swift", "functions": ["not a dict func"]},
    ]
    out = select_functions(call_graph, FunctionFilters())
    assert [w.function_name for w in out] == ["foo"]


def test_select_functions_returns_empty_on_non_list_input():
    assert select_functions({}, FunctionFilters()) == []
    assert select_functions(None, FunctionFilters()) == []  # type: ignore[arg-type]


def test_get_function_line_count_fallbacks():
    # Direct field.
    assert get_function_line_count({"line_count": 42}) == 42
    # Nested context start/end.
    assert get_function_line_count({"context": {"start": 10, "end": 19}}) == 10
    # Top-level start/end.
    assert get_function_line_count({"start": 5, "end": 7}) == 3
    # Nothing usable.
    assert get_function_line_count({}) == 0
    # Malformed (negative range) — returns 0, not negative.
    assert get_function_line_count({"start": 10, "end": 5}) == 0
