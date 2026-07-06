"""Cross-callstack knowledge-store integration test for `CodePipeline`.

Scenario: stack A records a summary; stack B's Stage 4a system prompt then
contains a "Prior knowledge" block referencing it. A stale-checksum learning
is also injected (with a "may be stale" marker) so the LLM can disregard it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from hindsight.llm import AsyncLLMClient, LLMResponse
from hindsight.llm.bedrock import LLMClientConfig
from hindsight.orchestration import (
    AnalysisContext,
    AnalysisSession,
    AsyncResultSink,
    CodePipeline,
    FunctionFilters,
)


# ----------------------------------------------------------------------
# Fakes (mirrors the style of test_pipeline_code.py)
# ----------------------------------------------------------------------


class CapturingFakeLLM(AsyncLLMClient):
    """Captures every (system_prompt, messages) pair the pipeline sends."""

    def __init__(self, context_text: str, analysis_text: str = "[]"):
        self.config = LLMClientConfig(api_url="http://fake", model="claude-sonnet-4-5", max_tokens=64000)
        self._ctx_text = context_text
        self._ana_text = analysis_text
        self.captured_system_prompts: List[str] = []

    async def send(self, system_prompt, messages, *, enable_system_cache=True, cache_ttl="1h") -> LLMResponse:
        self.captured_system_prompts.append(system_prompt or "")
        sp = (system_prompt or "").lower()
        if "context" in sp and "diff" not in sp and "trace" not in sp and "perf" not in sp:
            text = self._ctx_text
        else:
            text = self._ana_text
        return LLMResponse(
            text=text,
            input_tokens=100,
            output_tokens=50,
            raw={"choices": [{"message": {"content": text}}], "usage": {"input_tokens": 100, "output_tokens": 50}},
        )

    async def aclose(self) -> None:
        pass


class StubPublisher:
    def __init__(self):
        self.added: List[Dict[str, Any]] = []

    def add_result(self, *, repo_name, file_path, function, function_checksum, results) -> str:
        self.added.append({
            "file_path": file_path,
            "function": function,
            "function_checksum": function_checksum,
            "results": list(results),
        })
        return f"id-{len(self.added)}"

    def check_existing_result(self, file_name, function_name, checksum):
        return None


def _build_session(tmp_path):
    """Build a real session over a real (tiny) on-disk repo + a real KnowledgeStore.

    Returns (session, sink, pub). MUST be called from inside an asyncio loop
    so the session's `asyncio.Lock()` instances bind to the running loop
    (otherwise the rate limiter raises "no current event loop").
    """
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True, exist_ok=True)
    (repo / "src" / "a.swift").write_text(
        "\n".join([f"line {i}" for i in range(40)]), encoding="utf-8"
    )

    ctx = AnalysisContext.from_config(
        repo_path=str(repo),
        config={"model": "claude-sonnet-4-5", "code_analyzer_workers": 1},
        output_base_dir=str(tmp_path / "out"),
        api_key="dummy",
    )
    session = AnalysisSession.create(ctx)
    assert session.knowledge_store is not None
    pub = StubPublisher()
    sink = AsyncResultSink(pub, repo_name=ctx.repo_name)
    return session, sink, pub


def _bundle_for(function: str) -> str:
    return json.dumps({
        "schema_version": "1.0",
        "primary_function": {
            "function_name": function,
            "file_path": "src/a.swift",
            "source": f"func {function}() {{}}",
        },
        "callees": [],
        "callers": [],
    })


def _single_function_call_graph() -> List[Dict[str, Any]]:
    return [{
        "file": "src/a.swift",
        "functions": [{
            "function": "longFoo",
            "context": {"file": "src/a.swift", "start": 1, "end": 30},
            "functions_invoked": [],
        }],
    }]


# ----------------------------------------------------------------------
# Tests
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_prior_summary_appears_in_stage_4a_prompt(tmp_path):
    """Stack A records a summary; stack B's next Stage 4a prompt contains it."""
    session, sink, _pub = _build_session(tmp_path)

    # Stack A: record a summary directly through the store.
    session.knowledge_store.record_learning(
        subject="code",
        kind="summary",
        entity_key="src/a.swift::longFoo",
        summary="Parses the input and dispatches to one of three handlers.",
        confidence=0.9,
        file_path="src/a.swift",
        function_name="longFoo",
    )

    fake = CapturingFakeLLM(context_text=_bundle_for("longFoo"))
    session.llm = fake

    pipeline = CodePipeline(session, sink)
    async with session:
        await pipeline.analyze_repo(_single_function_call_graph(), FunctionFilters())

    # The Stage 4a system prompt for the second-stack run must mention the
    # prior knowledge block and the recorded summary.
    ctx_prompts = [p for p in fake.captured_system_prompts if "context" in (p or "").lower()]
    assert ctx_prompts, "no Stage 4a prompt captured"
    assert any("Prior knowledge" in p for p in ctx_prompts)
    assert any("dispatches to one of three handlers" in p for p in ctx_prompts)


@pytest.mark.asyncio
async def test_stale_checksum_marked_in_prompt(tmp_path):
    """A learning whose checksum doesn't match is annotated as stale.

    When ONLY stale-checksum entries exist for a function, the block still
    includes them (better than nothing) but each is tagged so the LLM can
    treat it with appropriate skepticism. When fresh entries exist alongside
    stale ones, the fresh entries are preferred.
    """
    session, sink, _pub = _build_session(tmp_path)

    # Record a learning tied to a deliberately-wrong checksum.
    session.knowledge_store.record_learning(
        subject="code",
        kind="summary",
        entity_key="src/a.swift::longFoo",
        summary="OUTDATED behavior — different source revision.",
        confidence=0.4,
        file_path="src/a.swift",
        function_name="longFoo",
        checksum="ZZZ_DEFINITELY_NOT_THE_CURRENT_CHECKSUM",
    )

    fake = CapturingFakeLLM(context_text=_bundle_for("longFoo"))
    session.llm = fake

    pipeline = CodePipeline(session, sink)
    async with session:
        await pipeline.analyze_repo(_single_function_call_graph(), FunctionFilters())

    ctx_prompts = [p for p in fake.captured_system_prompts if "context" in (p or "").lower()]
    assert ctx_prompts
    # Either the entry is filtered out OR it's included with a stale marker —
    # both are valid. What's NOT valid is silently injecting a stale summary.
    leaked = [p for p in ctx_prompts if "OUTDATED behavior" in p]
    for p in leaked:
        assert "may be stale" in p, (
            "Stale-checksum learning was injected without the 'may be stale' marker"
        )


@pytest.mark.asyncio
async def test_no_store_no_prior_block(tmp_path):
    """When `knowledge_store` is None, Stage 4a runs unchanged — no crash, no block."""
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "a.swift").write_text(
        "\n".join([f"line {i}" for i in range(40)]), encoding="utf-8"
    )
    ctx = AnalysisContext.from_config(
        repo_path=str(repo),
        config={"model": "claude-sonnet-4-5", "code_analyzer_workers": 1},
        output_base_dir=str(tmp_path / "out"),
        api_key="dummy",
    )
    session = AnalysisSession.create(ctx)
    session.knowledge_store = None  # simulate failed store init

    fake = CapturingFakeLLM(context_text=_bundle_for("longFoo"))
    session.llm = fake

    pub = StubPublisher()
    sink = AsyncResultSink(pub, repo_name=ctx.repo_name)
    pipeline = CodePipeline(session, sink)
    async with session:
        summary = await pipeline.analyze_repo(_single_function_call_graph(), FunctionFilters())

    assert summary.error is None
    ctx_prompts = [p for p in fake.captured_system_prompts if "context" in (p or "").lower()]
    assert ctx_prompts
    assert not any("Prior knowledge" in p for p in ctx_prompts)
