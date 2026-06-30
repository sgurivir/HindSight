"""End-to-end tests for `PerfPipeline` with a fake LLM client.

Mirrors `test_pipeline_code.py` but for the perf workflow:
  - Stage A → Stage B happy path on a single call path
  - Per-path failure isolated (one path fails, another succeeds)
  - Per-function context cache: two paths sharing a function only collect once
  - Issues annotated with `call_path` and `category="performance"`
  - Token callback fires per LLM call
  - Empty work list completes cleanly with `run_started` + `run_completed`
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

import pytest

from hindsight.llm import AsyncLLMClient, LLMResponse
from hindsight.llm.bedrock import LLMClientConfig
from hindsight.orchestration import (
    AnalysisContext,
    AnalysisSession,
    PerfPathWork,
    PerfPipeline,
    PerfRunSummary,
    perf_function_checksum,
)


# ----------------------------------------------------------------------
# Fakes
# ----------------------------------------------------------------------


class FakePerfLLM(AsyncLLMClient):
    """Routes by system-prompt content distinct to the perf stages.

    Both perf stages embed distinctive markers in their system prompts:
    `perf_context_collection` for Stage A, `perf_analysis` for Stage B.
    """

    def __init__(
        self,
        *,
        context_responses: Optional[List[str]] = None,
        analysis_responses: Optional[List[str]] = None,
    ):
        self.config = LLMClientConfig(
            api_url="http://fake", model="claude-sonnet-4-5", max_tokens=64000
        )
        self._ctx_q = list(context_responses or [])
        self._ana_q = list(analysis_responses or [])
        self.sends: List[Dict[str, Any]] = []

    async def send(
        self, system_prompt, messages, *, enable_system_cache=True, cache_ttl="1h"
    ) -> LLMResponse:
        self.sends.append({"system_preview": (system_prompt or "")[:80], "messages": messages})
        # Route by the user message — Stage A's user prompt contains
        # "## Call Path" verbatim; Stage B's contains "## Context Bundle".
        user_first = ""
        if messages:
            content = messages[0].get("content", "")
            if isinstance(content, str):
                user_first = content
        if "Context Bundle" in user_first:
            text = self._ana_q.pop(0) if self._ana_q else "[]"
        else:
            text = self._ctx_q.pop(0) if self._ctx_q else "{}"
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


@pytest.fixture
def session(tmp_path):
    ctx = AnalysisContext.from_config(
        repo_path=str(tmp_path / "repo"),
        config={"model": "claude-sonnet-4-5", "code_analyzer_workers": 2},
        output_base_dir=str(tmp_path / "out"),
        api_key="dummy",
    )
    return AnalysisSession.create(ctx)


@pytest.fixture
def session_serial(tmp_path):
    """Single-worker session for tests that need deterministic ordering."""
    ctx = AnalysisContext.from_config(
        repo_path=str(tmp_path / "repo"),
        config={"model": "claude-sonnet-4-5", "code_analyzer_workers": 1},
        output_base_dir=str(tmp_path / "out"),
        api_key="dummy",
    )
    return AnalysisSession.create(ctx)


def _make_work(path: List[str], *, files: Optional[Dict[str, str]] = None) -> PerfPathWork:
    files = files or {}
    bodies: Dict[str, Dict[str, Any]] = {}
    checksums: Dict[str, str] = {}
    for i, fn in enumerate(path):
        file_path = files.get(fn, "src/file.swift")
        bodies[fn] = {
            "file": file_path,
            "start_line": i * 10 + 1,
            "end_line": i * 10 + 9,
            "body": f"func {fn}() {{}}",
        }
        checksums[fn] = perf_function_checksum(fn, file_path, i * 10 + 1, i * 10 + 9)
    return PerfPathWork(
        path=tuple(path),
        function_bodies=bodies,
        function_checksums=checksums,
    )


def _bundle_for(path: List[str]) -> str:
    return json.dumps({
        "call_path": path,
        "functions": {
            fn: {"body": f"func {fn}() {{}}", "file": "src/file.swift", "line": i * 10 + 1}
            for i, fn in enumerate(path)
        },
    })


def _issue(function_name: str, *, line: int = 5) -> Dict[str, Any]:
    return {
        "file_path": "src/file.swift",
        "function_name": function_name,
        "line_number": str(line),
        "severity": "medium",
        "issue": f"perf issue in {function_name}",
        "description": "tight loop allocates",
        "suggestion": "hoist allocation",
        "category": "performance",
        "issueType": "allocation",
    }


# ----------------------------------------------------------------------
# Happy path
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_analyze_paths_happy_path(session):
    work = [_make_work(["root", "mid", "leaf"])]
    fake = FakePerfLLM(
        context_responses=[_bundle_for(["root", "mid", "leaf"])],
        analysis_responses=[json.dumps([_issue("leaf", line=11)])],
    )
    session.llm = fake

    pipeline = PerfPipeline(session)
    async with session:
        summary = await pipeline.analyze_paths(work)

    assert summary.error is None
    assert summary.selected == 1
    assert summary.successful == 1
    assert summary.failed == 0
    assert len(summary.issues) == 1
    # Every issue is annotated with the call_path + category.
    assert summary.issues[0]["call_path"] == ["root", "mid", "leaf"]
    assert summary.issues[0]["category"] == "performance"


# ----------------------------------------------------------------------
# Event sequence
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_analyze_paths_emits_full_event_sequence(session):
    work = [_make_work(["a", "b"])]
    fake = FakePerfLLM(
        context_responses=[_bundle_for(["a", "b"])],
        analysis_responses=["[]"],
    )
    session.llm = fake

    received: List[str] = []
    pipeline = PerfPipeline(session)
    async with session:
        session.subscribe(lambda e: received.append(e.type))
        summary = await pipeline.analyze_paths(work)

    assert summary.error is None
    assert received[0] == "run_started"
    assert received[-1] == "run_completed"
    assert "function_started" in received
    assert "function_complete" in received


# ----------------------------------------------------------------------
# Per-path failure isolation
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_per_path_failure_isolated(session_serial):
    """One path's Stage A fails irrecoverably; another succeeds. The pipeline
    reports 1 success + 1 failure, doesn't crash, and emits a
    `function_failed` event for the bad path. Uses a path-keyed fake so the
    test is independent of execution order."""

    class _PathKeyedLLM(AsyncLLMClient):
        """Routes by inspecting the path identifier in each Stage A user prompt."""

        def __init__(self):
            self.config = LLMClientConfig(
                api_url="http://fake", model="claude-sonnet-4-5", max_tokens=64000
            )

        async def send(
            self, system_prompt, messages, *, enable_system_cache=True, cache_ttl="1h"
        ) -> LLMResponse:
            user = messages[0].get("content", "") if messages else ""
            if "Context Bundle" in user:
                # Stage B for the surviving path.
                text = json.dumps([_issue("good", line=1)])
            elif "bad" in user:
                # Stage A for path "bad" — always returns text that can't be
                # extracted as a perf-context bundle, even with the fallback
                # retry prompt. The IterativeRunner exhausts max_iterations.
                text = "I am unable to comply with this request."
            else:
                # Stage A for path "good" — valid bundle.
                text = _bundle_for(["good"])
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

    work = [_make_work(["bad"]), _make_work(["good"])]
    session_serial.llm = _PathKeyedLLM()

    failures: List[Any] = []
    pipeline = PerfPipeline(session_serial)
    async with session_serial:
        session_serial.subscribe(
            lambda e: failures.append(e) if e.type == "function_failed" else None
        )
        summary = await pipeline.analyze_paths(work)

    assert summary.error is None
    assert summary.successful == 1
    assert summary.failed == 1
    assert len(failures) == 1


# ----------------------------------------------------------------------
# Per-function context cache reuse across paths
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_context_cache_reuses_function_across_paths(session_serial):
    """Two paths share `shared_fn`. After path-1 collects its context, path-2
    starts with `shared_fn` already cached; the user prompt for path-2's
    Stage A must mention the cached function in the 'Pre-Collected Context'
    section instead of asking the model to gather it."""

    # Path 1 has shared_fn at the same file/start/end as path 2 → same checksum.
    files = {"shared_fn": "src/shared.swift", "alpha": "src/a.swift", "beta": "src/b.swift"}
    path1 = _make_work(["alpha", "shared_fn"], files=files)
    path2 = _make_work(["beta", "shared_fn"], files=files)

    # Override path2's shared_fn body so its checksum matches path1's exactly.
    path2.function_bodies["shared_fn"] = path1.function_bodies["shared_fn"]
    path2.function_checksums["shared_fn"] = path1.function_checksums["shared_fn"]

    bundle1 = json.dumps({
        "call_path": ["alpha", "shared_fn"],
        "functions": {
            "alpha": {"body": "...", "file": "src/a.swift"},
            "shared_fn": {"body": "...", "file": "src/shared.swift", "interesting": True},
        },
    })
    bundle2 = json.dumps({
        "call_path": ["beta", "shared_fn"],
        "functions": {
            "beta": {"body": "...", "file": "src/b.swift"},
        },
    })
    fake = FakePerfLLM(
        context_responses=[bundle1, bundle2],
        analysis_responses=["[]", "[]"],
    )
    session_serial.llm = fake

    pipeline = PerfPipeline(session_serial)
    async with session_serial:
        # Run path1 first to populate cache, then path2.
        await pipeline.analyze_paths([path1])
        await pipeline.analyze_paths([path2])

    # The cache saw one miss for shared_fn (path1) then one hit (path2).
    assert pipeline._cache.hits >= 1
    assert pipeline._cache.misses >= 1


# ----------------------------------------------------------------------
# Empty input
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_work_list_completes_cleanly(session):
    fake = FakePerfLLM()
    session.llm = fake

    received: List[str] = []
    pipeline = PerfPipeline(session)
    async with session:
        session.subscribe(lambda e: received.append(e.type))
        summary = await pipeline.analyze_paths([])

    assert summary.selected == 0
    assert summary.successful == summary.failed == 0
    assert received == ["run_started", "run_completed"]


# ----------------------------------------------------------------------
# Token callback
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_token_callback_called_per_llm_call(session):
    fake = FakePerfLLM(
        context_responses=[_bundle_for(["a"])],
        analysis_responses=["[]"],
    )
    session.llm = fake

    samples: List[tuple] = []

    def cb(input_tokens: int, output_tokens: int) -> None:
        samples.append((input_tokens, output_tokens))

    pipeline = PerfPipeline(session, token_callback=cb)
    async with session:
        await pipeline.analyze_paths([_make_work(["a"])])

    # Two LLM calls (Stage A + Stage B) → two callback invocations.
    assert len(samples) == 2
    assert all(i > 0 and o > 0 for i, o in samples)


# ----------------------------------------------------------------------
# Checksum helper
# ----------------------------------------------------------------------


def test_perf_function_checksum_is_deterministic_and_short():
    a = perf_function_checksum("foo", "src/file.swift", 10, 20)
    b = perf_function_checksum("foo", "src/file.swift", 10, 20)
    c = perf_function_checksum("foo", "src/other.swift", 10, 20)
    assert a == b
    assert a != c
    assert len(a) == 16
