"""End-to-end tests for `TracePipeline` with a fake LLM client.

Mirror of `test_pipeline_perf.py` adapted to the trace workflow:
  - Stage Ta → Tb → Tc happy path on a single callstack
  - Cache callback short-circuits Ta/Tb/Tc
  - Tc rejects an issue with `valid=false, low_confidence=false`
  - Tc keeps an issue with `low_confidence=true` (annotated)
  - Tc verdict failure keeps the issue with `low_confidence=true`
  - Per-trace failure isolation
  - publish_callback invoked once per successful trace
  - Token callback fires per LLM call
  - Empty work list completes cleanly
  - Annotation adds `trace_id` + `Callstack` to every issue
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

import pytest

from hindsight.llm import AsyncLLMClient, LLMResponse
from hindsight.llm.bedrock import LLMClientConfig
from hindsight.orchestration import (
    AnalysisContext,
    AnalysisSession,
    TracePipeline,
    TraceRunSummary,
    TraceWork,
)


# ----------------------------------------------------------------------
# Fakes
# ----------------------------------------------------------------------


class FakeTraceLLM(AsyncLLMClient):
    """Routes by user-message content. Ta, Tb, and Tc each have a
    distinctive marker in their user prompt:

        Ta: "## Callstack Trace"
        Tb: "## Context Bundle"
        Tc: "## Issue to Validate"
    """

    def __init__(
        self,
        *,
        ta_responses: Optional[List[str]] = None,
        tb_responses: Optional[List[str]] = None,
        tc_responses: Optional[List[str]] = None,
    ):
        self.config = LLMClientConfig(
            api_url="http://fake", model="claude-sonnet-4-5", max_tokens=64000
        )
        self._ta = list(ta_responses or [])
        self._tb = list(tb_responses or [])
        self._tc = list(tc_responses or [])
        self.calls: List[str] = []  # "ta" | "tb" | "tc"

    async def send(
        self, system_prompt, messages, *, enable_system_cache=True, cache_ttl="1h"
    ) -> LLMResponse:
        user_first = ""
        if messages:
            content = messages[0].get("content", "")
            if isinstance(content, str):
                user_first = content

        if "## Issue to Validate" in user_first:
            stage = "tc"
            text = self._tc.pop(0) if self._tc else '{"valid": true}'
        elif "## Context Bundle" in user_first:
            stage = "tb"
            text = self._tb.pop(0) if self._tb else "[]"
        else:
            stage = "ta"
            text = self._ta.pop(0) if self._ta else "{}"
        self.calls.append(stage)

        return LLMResponse(
            text=text,
            input_tokens=100,
            output_tokens=50,
            raw={
                "choices": [{"message": {"content": text}}],
                "usage": {"input_tokens": 100, "output_tokens": 50},
            },
        )

    async def aclose(self) -> None:
        pass


# ----------------------------------------------------------------------
# Fixtures + helpers
# ----------------------------------------------------------------------


@pytest.fixture
def session(tmp_path):
    ctx = AnalysisContext.from_config(
        repo_path=str(tmp_path / "repo"),
        config={"model": "claude-sonnet-4-5", "max_analysis_workers": 2},
        output_base_dir=str(tmp_path / "out"),
        api_key="dummy",
    )
    return AnalysisSession.create(ctx, analyzer_name="trace_analysis")


@pytest.fixture
def session_serial(tmp_path):
    """Single-worker session for tests that need deterministic ordering."""
    ctx = AnalysisContext.from_config(
        repo_path=str(tmp_path / "repo"),
        config={"model": "claude-sonnet-4-5", "max_analysis_workers": 1},
        output_base_dir=str(tmp_path / "out"),
        api_key="dummy",
    )
    return AnalysisSession.create(ctx, analyzer_name="trace_analysis")


def _make_work(
    index: int,
    *,
    call_path: Optional[List[str]] = None,
    file_paths: Optional[List[str]] = None,
) -> TraceWork:
    call_path = call_path or ["Top.run", "Mid.work", "Leaf.tick"]
    file_paths = file_paths or ["src/Top.swift", "src/Mid.swift", "src/Leaf.swift"]
    callstack_text = "\n".join(call_path)
    prompt_content = (
        callstack_text
        + "\n====Use this additional context if needed===\n"
        + "Some additional code context.\n"
    )
    return TraceWork(
        callstack_index=index,
        callstack=tuple({"function_name": fn, "file_path": fp} for fn, fp in zip(call_path, file_paths)),
        prompt_content=prompt_content,
        callstack_data={"sample_count": 42, "call_path": call_path},
        extracted_file_paths=tuple(file_paths),
        callstack_text=callstack_text,
        trace_id=f"trace_{index + 1:04d}",
    )


def _bundle_for(call_path: List[str]) -> str:
    return json.dumps(
        {
            "call_path": call_path,
            "functions": {
                fn: {"body": f"func {fn}() {{}}", "file": "src/f.swift", "line": 1}
                for fn in call_path
            },
        }
    )


def _issue(*, function_name: str = "Leaf.tick", line: int = 7) -> Dict[str, Any]:
    return {
        "file_path": "src/Leaf.swift",
        "function_name": function_name,
        "functionName": function_name,
        "line_number": str(line),
        "severity": "high",
        "issue": f"hot loop in {function_name}",
        "description": "tight loop allocates each tick",
        "suggestion": "hoist allocation out of loop",
        "category": "performance",
        "issueType": "performance",
    }


# ----------------------------------------------------------------------
# Happy path: Ta → Tb → Tc-pass for every issue
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_analyze_traces_happy_path(session):
    work = [_make_work(0, call_path=["A", "B"])]
    fake = FakeTraceLLM(
        ta_responses=[_bundle_for(["A", "B"])],
        tb_responses=[json.dumps([_issue(function_name="B", line=3)])],
        tc_responses=['{"valid": true}'],
    )
    session.llm = fake

    pipeline = TracePipeline(session)
    async with session:
        summary = await pipeline.analyze_traces(work)

    assert summary.error is None
    assert summary.selected == 1
    assert summary.successful == 1
    assert summary.failed == 0
    assert summary.cached == 0
    # Three LLM calls: Ta, Tb, Tc.
    assert fake.calls == ["ta", "tb", "tc"]


# ----------------------------------------------------------------------
# Event sequence
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_analyze_traces_emits_full_event_sequence(session):
    work = [_make_work(0, call_path=["A", "B"])]
    fake = FakeTraceLLM(
        ta_responses=[_bundle_for(["A", "B"])],
        tb_responses=["[]"],
    )
    session.llm = fake

    received: List[str] = []
    pipeline = TracePipeline(session)
    async with session:
        session.subscribe(lambda e: received.append(e.type))
        summary = await pipeline.analyze_traces(work)

    assert summary.error is None
    assert received[0] == "run_started"
    assert received[-1] == "run_completed"
    assert "function_started" in received
    assert "function_complete" in received


# ----------------------------------------------------------------------
# Cache callback short-circuits Ta/Tb/Tc
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cache_callback_short_circuits(session):
    fake = FakeTraceLLM()  # no canned responses needed
    session.llm = fake

    async def _hit(_work: TraceWork) -> bool:
        return True

    pipeline = TracePipeline(session, cache_check_callback=_hit)
    async with session:
        summary = await pipeline.analyze_traces([_make_work(0)])

    assert summary.selected == 1
    assert summary.cached == 1
    assert summary.successful == 0
    assert summary.failed == 0
    # No LLM calls at all when the cache hits.
    assert fake.calls == []


# ----------------------------------------------------------------------
# Tc rejects with confidence → issue dropped
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tc_confident_rejection_drops_issue(session):
    work = [_make_work(0, call_path=["A"])]
    fake = FakeTraceLLM(
        ta_responses=[_bundle_for(["A"])],
        tb_responses=[json.dumps([_issue(function_name="A")])],
        tc_responses=['{"valid": false, "low_confidence": false, "reason": "wrong"}'],
    )
    session.llm = fake

    published: List[List[Dict[str, Any]]] = []

    async def _publish(_w: TraceWork, issues: List[Dict[str, Any]]) -> bool:
        published.append(list(issues))
        return True

    pipeline = TracePipeline(session, publish_callback=_publish)
    async with session:
        summary = await pipeline.analyze_traces(work)

    assert summary.successful == 1
    # The single issue was confidently rejected → publish called with [].
    assert published == [[]]


# ----------------------------------------------------------------------
# Tc low_confidence → issue kept with annotation
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tc_low_confidence_annotates_issue(session):
    work = [_make_work(0, call_path=["A"])]
    fake = FakeTraceLLM(
        ta_responses=[_bundle_for(["A"])],
        tb_responses=[json.dumps([_issue(function_name="A")])],
        tc_responses=[
            '{"valid": false, "low_confidence": true, "reason": "unsure"}'
        ],
    )
    session.llm = fake

    published: List[List[Dict[str, Any]]] = []

    async def _publish(_w: TraceWork, issues: List[Dict[str, Any]]) -> bool:
        published.append(list(issues))
        return True

    pipeline = TracePipeline(session, publish_callback=_publish)
    async with session:
        await pipeline.analyze_traces(work)

    assert len(published) == 1 and len(published[0]) == 1
    kept = published[0][0]
    assert "validation" in kept
    assert kept["validation"]["low_confidence"] is True
    assert kept["validation"]["reason"] == "unsure"


# ----------------------------------------------------------------------
# Tc unparseable verdict → keep issue with low_confidence
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tc_unparseable_keeps_issue_with_low_confidence(session):
    work = [_make_work(0, call_path=["A"])]
    # Use 19 garbage responses: the iterative runner exhausts max_iterations.
    fake = FakeTraceLLM(
        ta_responses=[_bundle_for(["A"])],
        tb_responses=[json.dumps([_issue(function_name="A")])],
        tc_responses=["I can't help with that."] * 20,
    )
    session.llm = fake

    published: List[List[Dict[str, Any]]] = []

    async def _publish(_w: TraceWork, issues: List[Dict[str, Any]]) -> bool:
        published.append(list(issues))
        return True

    pipeline = TracePipeline(session, publish_callback=_publish)
    async with session:
        await pipeline.analyze_traces(work)

    assert len(published) == 1 and len(published[0]) == 1
    kept = published[0][0]
    assert kept.get("validation", {}).get("low_confidence") is True


# ----------------------------------------------------------------------
# Per-trace failure isolation
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_per_trace_failure_isolated(session_serial):
    """One trace's Stage Ta fails irrecoverably; another succeeds. The
    pipeline reports 1 success + 1 failure, doesn't crash, and emits a
    `function_failed` event for the bad trace. Uses a trace_id-keyed fake
    so the test is independent of execution order."""

    class _TraceKeyedLLM(AsyncLLMClient):
        def __init__(self):
            self.config = LLMClientConfig(
                api_url="http://fake", model="claude-sonnet-4-5", max_tokens=64000
            )

        async def send(
            self, system_prompt, messages, *, enable_system_cache=True, cache_ttl="1h"
        ) -> LLMResponse:
            user = messages[0].get("content", "") if messages else ""
            if "## Context Bundle" in user:
                text = "[]"  # Tb for the good trace
            elif "## Issue to Validate" in user:
                text = '{"valid": true}'
            elif "BAD-TRACE-FN" in user:
                # Ta for the bad trace — unparseable until max_iterations exhaust.
                text = "I cannot help with that."
            else:
                text = _bundle_for(["good_fn"])
            return LLMResponse(
                text=text,
                input_tokens=100,
                output_tokens=50,
                raw={
                    "choices": [{"message": {"content": text}}],
                    "usage": {"input_tokens": 100, "output_tokens": 50},
                },
            )

        async def aclose(self) -> None:
            pass

    bad = _make_work(0, call_path=["BAD-TRACE-FN"], file_paths=["src/Bad.swift"])
    good = _make_work(1, call_path=["good_fn"], file_paths=["src/Good.swift"])
    session_serial.llm = _TraceKeyedLLM()

    failures: List[Any] = []
    pipeline = TracePipeline(session_serial)
    async with session_serial:
        session_serial.subscribe(
            lambda e: failures.append(e) if e.type == "function_failed" else None
        )
        summary = await pipeline.analyze_traces([bad, good])

    assert summary.error is None
    assert summary.successful == 1
    assert summary.failed == 1
    assert len(failures) == 1


# ----------------------------------------------------------------------
# publish_callback called per successful trace; publish failure → function_failed
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_publish_callback_invoked_per_trace(session):
    work = [_make_work(0, call_path=["A"]), _make_work(1, call_path=["B"])]
    fake = FakeTraceLLM(
        ta_responses=[_bundle_for(["A"]), _bundle_for(["B"])],
        tb_responses=["[]", "[]"],
    )
    session.llm = fake

    seen_trace_ids: List[str] = []

    async def _publish(w: TraceWork, _issues: List[Dict[str, Any]]) -> bool:
        seen_trace_ids.append(w.trace_id)
        return True

    pipeline = TracePipeline(session, publish_callback=_publish)
    async with session:
        summary = await pipeline.analyze_traces(work)

    assert summary.successful == 2
    assert sorted(seen_trace_ids) == ["trace_0001", "trace_0002"]


@pytest.mark.asyncio
async def test_publish_failure_marks_function_failed(session):
    work = [_make_work(0, call_path=["A"])]
    fake = FakeTraceLLM(
        ta_responses=[_bundle_for(["A"])],
        tb_responses=["[]"],
    )
    session.llm = fake

    async def _publish(_w: TraceWork, _issues: List[Dict[str, Any]]) -> bool:
        return False

    failed_events: List[Any] = []
    pipeline = TracePipeline(session, publish_callback=_publish)
    async with session:
        session.subscribe(
            lambda e: failed_events.append(e) if e.type == "function_failed" else None
        )
        summary = await pipeline.analyze_traces(work)

    assert summary.successful == 0
    assert summary.failed == 1
    assert len(failed_events) == 1
    assert failed_events[0].stage == "publish"


# ----------------------------------------------------------------------
# Token callback
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_token_callback_called_per_llm_call(session):
    fake = FakeTraceLLM(
        ta_responses=[_bundle_for(["A"])],
        tb_responses=[json.dumps([_issue(function_name="A")])],
        tc_responses=['{"valid": true}'],
    )
    session.llm = fake

    samples: List[Tuple[int, int]] = []

    def cb(input_tokens: int, output_tokens: int) -> None:
        samples.append((input_tokens, output_tokens))

    pipeline = TracePipeline(session, token_callback=cb)
    async with session:
        await pipeline.analyze_traces([_make_work(0, call_path=["A"])])

    # Three LLM calls (Ta + Tb + Tc) → three callback invocations.
    assert len(samples) == 3
    assert all(i > 0 and o > 0 for i, o in samples)


# ----------------------------------------------------------------------
# Empty input
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_work_list_completes_cleanly(session):
    fake = FakeTraceLLM()
    session.llm = fake

    received: List[str] = []
    pipeline = TracePipeline(session)
    async with session:
        session.subscribe(lambda e: received.append(e.type))
        summary = await pipeline.analyze_traces([])

    assert summary.selected == 0
    assert summary.successful == 0
    assert summary.failed == 0
    assert received == ["run_started", "run_completed"]


# ----------------------------------------------------------------------
# Issue annotation
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_issues_annotated_with_trace_id_and_callstack(session):
    work = [_make_work(0, call_path=["Top.run", "Mid.work"])]
    fake = FakeTraceLLM(
        ta_responses=[_bundle_for(["Top.run", "Mid.work"])],
        tb_responses=[json.dumps([_issue(function_name="Mid.work")])],
        tc_responses=['{"valid": true}'],
    )
    session.llm = fake

    received_issues: List[Dict[str, Any]] = []

    async def _publish(_w: TraceWork, issues: List[Dict[str, Any]]) -> bool:
        received_issues.extend(issues)
        return True

    pipeline = TracePipeline(session, publish_callback=_publish)
    async with session:
        await pipeline.analyze_traces(work)

    assert len(received_issues) == 1
    issue = received_issues[0]
    assert issue["trace_id"] == "trace_0001"
    assert issue["Callstack"] == "Top.run\nMid.work"
    assert issue["original_callstack"]["sample_count"] == 42
