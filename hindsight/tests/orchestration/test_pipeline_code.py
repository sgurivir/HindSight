"""End-to-end tests for `CodePipeline` with a fake LLM client.

Covers:
  - Stage 4a → 4b happy path
  - Cache hit (republish without LLM call)
  - Per-function failure isolation (one fails, others succeed)
  - Call-tree mode (single LLM run per root with issue grouping)
  - Event sequence + partial-stream-after-failure behavior
  - Stage 4a bundle disk-cache (write then read on second run)
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from hindsight.llm import AsyncLLMClient, LLMResponse, ToolCall
from hindsight.llm.bedrock import LLMClientConfig
from hindsight.orchestration import (
    AnalysisContext,
    AnalysisSession,
    AsyncResultSink,
    CodePipeline,
    CodeRunSummary,
    FunctionFilters,
)


# ----------------------------------------------------------------------
# Fakes
# ----------------------------------------------------------------------


class FakeLLM(AsyncLLMClient):
    """Fake LLM client that returns scripted responses based on a routing key.

    Routes by inspecting the `messages` argument:
      - If the last message contains 'context_collection', return the next
        context-bundle response from the queue.
      - Else if it contains 'analysis' or stage-4b cue, return the next issues
        response.
      - Else if it contains 'call_tree' cue, return the next call-tree response.
    """

    def __init__(
        self,
        *,
        context_responses: Optional[List[str]] = None,
        analysis_responses: Optional[List[str]] = None,
        call_tree_responses: Optional[List[str]] = None,
        call_tree_context_responses: Optional[List[str]] = None,
    ):
        self.config = LLMClientConfig(api_url="http://fake", model="claude-sonnet-4-5", max_tokens=64000)
        self._ctx_q = list(context_responses or [])
        self._ana_q = list(analysis_responses or [])
        self._ct_q = list(call_tree_responses or [])
        self._ct_ctx_q = list(call_tree_context_responses or [])
        self.sends: List[Dict[str, Any]] = []

    async def send(self, system_prompt, messages, *, enable_system_cache=True, cache_ttl="1h") -> LLMResponse:
        self.sends.append({"system": system_prompt[:80] if system_prompt else None, "messages": messages})
        # Route based on system prompt content (the stage spec injects its system prompt).
        sp = (system_prompt or "").lower()
        is_call_tree = "call tree" in sp or "call-tree" in sp or "call_tree" in sp
        if is_call_tree and "context collection" in sp:
            # Call-tree Step 1 (context collection). Default "{}" → no extra
            # context, so tests that don't script it still analyze tree-only.
            text = self._next(self._ct_ctx_q, "call_tree_context", default="{}")
        elif is_call_tree:
            text = self._next(self._ct_q, "call_tree")
        elif "context" in sp and "diff" not in sp and "trace" not in sp and "perf" not in sp:
            text = self._next(self._ctx_q, "context")
        else:
            text = self._next(self._ana_q, "analysis")
        return LLMResponse(
            text=text,
            input_tokens=100,
            output_tokens=50,
            raw={"choices": [{"message": {"content": text}}], "usage": {"input_tokens": 100, "output_tokens": 50}},
        )

    async def aclose(self) -> None:
        pass

    @staticmethod
    def _next(queue: List[str], kind: str, default: str = "[]") -> str:
        if not queue:
            return default  # safe default: empty issue array / empty context object
        return queue.pop(0)


class StubPublisher:
    """In-memory publisher; supports cache-hit tests via the `cached` dict."""

    def __init__(self, *, cached: Optional[Dict[tuple, Dict[str, Any]]] = None):
        self.added: List[Dict[str, Any]] = []
        self.cached = cached or {}

    def add_result(self, *, repo_name, file_path, function, function_checksum, results) -> str:
        self.added.append({
            "repo_name": repo_name,
            "file_path": file_path,
            "function": function,
            "function_checksum": function_checksum,
            "results": list(results),
        })
        return f"id-{len(self.added)}"

    def check_existing_result(self, file_name, function_name, checksum):
        return self.cached.get((file_name, function_name, checksum))


@pytest.fixture
def session_and_sink(tmp_path):
    """Build a real AnalysisSession + stubbed publisher pair.

    Patches the session's LLM client to a FakeLLM the test then re-points
    per scenario. Returns (session, sink, publisher, llm).
    """
    ctx = AnalysisContext.from_config(
        repo_path=str(tmp_path / "repo"),
        config={"model": "claude-sonnet-4-5", "max_analysis_workers": 2},
        output_base_dir=str(tmp_path / "out"),
        api_key="dummy",
    )
    session = AnalysisSession.create(ctx)
    pub = StubPublisher()
    sink = AsyncResultSink(pub, repo_name=ctx.repo_name)
    return session, sink, pub


def _make_call_graph() -> List[Dict[str, Any]]:
    """Two functions, both above the min length filter (default 7)."""
    return [
        {
            "file": "src/a.swift",
            "functions": [
                {
                    "function": "longFoo",
                    "context": {"file": "src/a.swift", "start": 1, "end": 30},
                    "functions_invoked": [],
                },
                {
                    "function": "longBar",
                    "context": {"file": "src/a.swift", "start": 40, "end": 80},
                    "functions_invoked": [],
                },
            ],
        }
    ]


def _single_function_call_graph(function: str = "longFoo") -> List[Dict[str, Any]]:
    """Single-function call graph for tests that need an unambiguous root.

    `select_functions` sorts longest-first and `sorted(set(...))` for call-tree
    root selection breaks ties alphabetically — so when there are multiple
    candidates `num_to_analyze=1` picks the alphabetical-first longest, which
    may not be what the test wants.
    """
    return [
        {
            "file": "src/a.swift",
            "functions": [
                {
                    "function": function,
                    "context": {"file": "src/a.swift", "start": 1, "end": 30},
                    "functions_invoked": [],
                },
            ],
        }
    ]


def _bundle_for(function: str) -> str:
    """Minimal valid Stage 4a bundle."""
    return json.dumps({
        "schema_version": "1.0",
        "primary_function": {
            "function_name": function,
            "file_path": f"src/a.swift",
            "source": f"func {function}() {{}}",
        },
        "callees": [],
        "callers": [],
    })


# ----------------------------------------------------------------------
# Happy path — Stage 4a → 4b → publish
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_analyze_repo_per_function_happy_path(session_and_sink):
    session, sink, pub = session_and_sink
    # Two functions × (4a + 4b) = 4 LLM calls. The two functions run
    # concurrently, so we can't pin which analysis response lands on which
    # function — just assert that the totals add up.
    fake = FakeLLM(
        context_responses=[_bundle_for("longFoo"), _bundle_for("longBar")],
        analysis_responses=[
            '[{"issue": "issue A", "severity": "high"}]',
            "[]",
        ],
    )
    session.llm = fake

    pipeline = CodePipeline(session, sink)
    async with session:
        summary = await pipeline.analyze_repo(_make_call_graph(), FunctionFilters())

    assert summary.error is None
    assert summary.selected == 2
    assert summary.successful == 2
    assert summary.failed == 0
    assert summary.cached == 0
    # Both functions published exactly once.
    assert len(pub.added) == 2
    assert {a["function"] for a in pub.added} == {"longFoo", "longBar"}
    # Together the two publications carry exactly one issue, which was
    # sanitized through the sink (default `category` filled in).
    all_issues = [issue for entry in pub.added for issue in entry["results"]]
    assert len(all_issues) == 1
    assert all_issues[0]["issue"] == "issue A"
    assert all_issues[0]["category"] == "general"


# ----------------------------------------------------------------------
# Event sequence — RunStarted + per-function + RunCompleted
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_analyze_repo_emits_full_event_sequence(session_and_sink):
    session, sink, pub = session_and_sink
    fake = FakeLLM(
        context_responses=[_bundle_for("longFoo"), _bundle_for("longBar")],
        analysis_responses=["[]", "[]"],
    )
    session.llm = fake

    received: List[str] = []

    pipeline = CodePipeline(session, sink)
    async with session:
        session.subscribe(lambda e: received.append(e.type))
        await pipeline.analyze_repo(_make_call_graph(), FunctionFilters())

    # Order: run_started, then a function_started+function_complete pair per
    # function (interleaved with each other since they run concurrently), then
    # run_completed last.
    assert received[0] == "run_started"
    assert received[-1] == "run_completed"
    assert received.count("function_started") == 2
    assert received.count("function_complete") == 2


# ----------------------------------------------------------------------
# Cache hit — republish without LLM call
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cache_hit_skips_llm(session_and_sink, tmp_path):
    session, _sink, _pub = session_and_sink
    # Pre-seed publisher with a cached result for longFoo's source checksum.
    # We can't easily compute the source checksum (it reads from disk via
    # HashUtil), so we set up the call graph to point at a real file we control.
    repo = Path(session.ctx.repo_path)
    src = repo / "src"
    src.mkdir(parents=True, exist_ok=True)
    file_content = "\n".join([f"line {i}" for i in range(40)])
    (src / "a.swift").write_text(file_content, encoding="utf-8")

    from hindsight.utils.hash_util import HashUtil
    real_checksum = HashUtil.checksum_for_function_source(
        session.ctx.repo_path, "src/a.swift", 1, 30
    )
    pub = StubPublisher(cached={
        ("src/a.swift", "longFoo", real_checksum): {
            "results": [{"issue": "cached defect", "severity": "low", "category": "memory"}]
        }
    })
    sink = AsyncResultSink(pub, repo_name=session.ctx.repo_name)
    # No analysis_responses — if the LLM is called the test will fail.
    fake = FakeLLM(analysis_responses=[])
    session.llm = fake

    call_graph = [
        {
            "file": "src/a.swift",
            "functions": [
                {
                    "function": "longFoo",
                    "context": {"file": "src/a.swift", "start": 1, "end": 30},
                }
            ],
        }
    ]
    pipeline = CodePipeline(session, sink)
    async with session:
        summary = await pipeline.analyze_repo(call_graph, FunctionFilters())

    assert summary.cached == 1
    assert summary.successful == 0
    # LLM was never invoked.
    assert fake.sends == []
    # And the cached issue was republished.
    assert len(pub.added) == 1
    assert pub.added[0]["results"][0]["issue"] == "cached defect"


# ----------------------------------------------------------------------
# Fault isolation — one function's Stage 4a fails, others succeed
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_per_function_failure_isolated(session_and_sink):
    session, sink, pub = session_and_sink
    # longFoo's 4a returns garbage; longBar succeeds.
    fake = FakeLLM(
        context_responses=["this is not JSON at all", _bundle_for("longBar")],
        analysis_responses=["[]"],  # only bar gets to 4b
    )
    session.llm = fake

    failure_events: List[Any] = []
    pipeline = CodePipeline(session, sink)
    async with session:
        session.subscribe(
            lambda e: failure_events.append(e) if e.type == "function_failed" else None
        )
        summary = await pipeline.analyze_repo(_make_call_graph(), FunctionFilters())

    assert summary.successful == 1
    assert summary.failed == 1
    assert summary.error is None  # outer run still OK
    assert len(failure_events) == 1
    # The bar function published; foo did not.
    assert [a["function"] for a in pub.added] == ["longBar"]


# ----------------------------------------------------------------------
# Empty input — selected=0, run_completed still fires
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_call_graph_completes_cleanly(session_and_sink):
    session, sink, _pub = session_and_sink
    fake = FakeLLM()
    session.llm = fake

    received: List[str] = []
    pipeline = CodePipeline(session, sink)
    async with session:
        session.subscribe(lambda e: received.append(e.type))
        summary = await pipeline.analyze_repo([], FunctionFilters())

    assert summary.selected == 0
    assert summary.successful == summary.failed == summary.cached == 0
    assert received == ["run_started", "run_completed"]


# ----------------------------------------------------------------------
# Stage 4a bundle on-disk cache survives across runs
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stage_4a_bundle_persisted_and_reused(session_and_sink):
    session, sink, pub = session_and_sink
    fake = FakeLLM(
        context_responses=[_bundle_for("longFoo")],
        analysis_responses=['[{"issue": "found"}]'],
    )
    session.llm = fake

    pipeline = CodePipeline(session, sink)
    work_item_call_graph = _make_call_graph()[:1]
    work_item_call_graph[0]["functions"] = work_item_call_graph[0]["functions"][:1]  # just longFoo

    async with session:
        # First run uses the bundle from fake's context_responses.
        await pipeline.analyze_repo(work_item_call_graph, FunctionFilters())

    # The bundle file should now exist on disk.
    from hindsight.orchestration.pipeline_code import CodePipeline as _CP
    bundle_checksum = _CP._bundle_checksum("src/a.swift", "longFoo")
    bundle_path = Path(session.ctx.context_bundles_dir) / f"{bundle_checksum[:8]}.json"
    assert bundle_path.exists()
    saved = json.loads(bundle_path.read_text())
    assert "primary_function" in saved


# ----------------------------------------------------------------------
# Sanitization — LLM omits 'severity', sink fills it in
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_published_issues_are_sanitized(session_and_sink):
    session, sink, pub = session_and_sink
    fake = FakeLLM(
        context_responses=[_bundle_for("longFoo")],
        analysis_responses=['[{"description": "no severity field at all"}]'],
    )
    session.llm = fake

    pipeline = CodePipeline(session, sink)
    cg = _make_call_graph()[:1]
    cg[0]["functions"] = cg[0]["functions"][:1]
    async with session:
        await pipeline.analyze_repo(cg, FunctionFilters())

    # The sink should have backfilled missing fields.
    issue = pub.added[0]["results"][0]
    assert issue["severity"] == "medium"
    assert issue["category"] == "general"
    assert issue["issue"] == "no severity field at all"  # description → issue fallback


# ----------------------------------------------------------------------
# Issue filter hook
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_issue_filter_runs_before_publish(session_and_sink):
    session, sink, pub = session_and_sink
    fake = FakeLLM(
        context_responses=[_bundle_for("longFoo")],
        analysis_responses=['[{"issue": "drop me", "category": "trivial"}, {"issue": "keep me"}]'],
    )
    session.llm = fake

    def drop_trivial(issues, function_context):
        return [i for i in issues if i.get("category") != "trivial"]

    pipeline = CodePipeline(session, sink, issue_filter=drop_trivial)
    cg = _make_call_graph()[:1]
    cg[0]["functions"] = cg[0]["functions"][:1]
    async with session:
        await pipeline.analyze_repo(cg, FunctionFilters())

    assert len(pub.added) == 1
    titles = [i["issue"] for i in pub.added[0]["results"]]
    assert titles == ["keep me"]


@pytest.mark.asyncio
async def test_issue_filter_exception_falls_back_to_unfiltered(session_and_sink):
    session, sink, pub = session_and_sink
    fake = FakeLLM(
        context_responses=[_bundle_for("longFoo")],
        analysis_responses=['[{"issue": "x"}]'],
    )
    session.llm = fake

    def buggy_filter(issues, function_context):
        raise RuntimeError("filter bug")

    pipeline = CodePipeline(session, sink, issue_filter=buggy_filter)
    cg = _make_call_graph()[:1]
    cg[0]["functions"] = cg[0]["functions"][:1]
    async with session:
        summary = await pipeline.analyze_repo(cg, FunctionFilters())

    # Filter failure must not fail the run; unfiltered issues still publish.
    assert summary.successful == 1
    assert pub.added[0]["results"][0]["issue"] == "x"


# ----------------------------------------------------------------------
# Token callback
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_token_callback_called_per_llm_call(session_and_sink):
    session, sink, _pub = session_and_sink
    fake = FakeLLM(
        context_responses=[_bundle_for("longFoo")],
        analysis_responses=["[]"],
    )
    session.llm = fake

    token_events: List[tuple[int, int]] = []
    pipeline = CodePipeline(
        session, sink,
        token_callback=lambda i, o: token_events.append((i, o)),
    )
    cg = _make_call_graph()[:1]
    cg[0]["functions"] = cg[0]["functions"][:1]
    async with session:
        await pipeline.analyze_repo(cg, FunctionFilters())

    # 4a + 4b = 2 LLM calls = 2 token events.
    assert len(token_events) == 2
    assert all(i == 100 and o == 50 for i, o in token_events)


# ----------------------------------------------------------------------
# Call-tree mode — single LLM run per root, issues grouped by defect_function
# ----------------------------------------------------------------------


class FakeCallTreeBuilder:
    """Minimal stand-in for CallTreeBuilder that produces a 2-node tree."""

    def build(self, root: str):
        @dataclass
        class _Node:
            function: str
            file: str
            checksum: str

        @dataclass
        class _Tree:
            root: str
            root_file: str
            root_checksum: str
            nodes: list

            def to_dict(self):
                return {
                    "schema_version": "2.0",
                    "root": {"function": self.root, "file": self.root_file, "checksum": self.root_checksum},
                    "nodes": [{"function": n.function, "file": n.file, "checksum": n.checksum} for n in self.nodes],
                    "stats": {"node_count": len(self.nodes), "total_chars": 100, "tree_signature": "sig"},
                    "truncation": {"depth_cap_hit": False, "char_cap_hit": False, "node_cap_hit": False, "stubbed_nodes": []},
                }

        return _Tree(
            root=root,
            root_file="src/a.swift",
            root_checksum="ROOT_CHECKSUM",
            nodes=[
                _Node(root, "src/a.swift", "ROOT_CHECKSUM"),
                _Node("helper", "src/b.swift", "HELPER_CHECKSUM"),
            ],
        )


@pytest.mark.asyncio
async def test_call_tree_groups_issues_by_defect_function(tmp_path):
    ctx = AnalysisContext.from_config(
        repo_path=str(tmp_path / "repo"),
        config={
            "model": "claude-sonnet-4-5",
            "max_analysis_workers": 2,
            "call_tree_analysis_enabled": True,
        },
        output_base_dir=str(tmp_path / "out"),
        api_key="dummy",
    )
    session = AnalysisSession.create(ctx)
    pub = StubPublisher()
    sink = AsyncResultSink(pub, repo_name=ctx.repo_name)
    fake = FakeLLM(
        call_tree_responses=[json.dumps([
            {"defect_function": "longFoo", "issue": "bug in root"},
            {"defect_function": "helper", "issue": "bug in callee"},
            {"defect_function": "out_of_tree_fn", "issue": "should attribute to root"},
        ])],
    )
    session.llm = fake

    pipeline = CodePipeline(session, sink)
    async with session:
        summary = await pipeline.analyze_repo(
            _single_function_call_graph("longFoo"),
            FunctionFilters(),
            num_to_analyze=1,
            call_tree_builder=FakeCallTreeBuilder(),
        )

    assert summary.error is None
    assert summary.successful >= 1
    # Three publications: longFoo (its own + the out-of-tree one re-pinned), helper.
    pub_by_fn: Dict[str, List[Dict[str, Any]]] = {}
    for entry in pub.added:
        pub_by_fn.setdefault(entry["function"], []).extend(entry["results"])
    # The out-of-tree issue got re-attributed to the root.
    assert "longFoo" in pub_by_fn
    assert "helper" in pub_by_fn
    long_foo_titles = [i["issue"] for i in pub_by_fn["longFoo"]]
    assert "bug in root" in long_foo_titles
    assert "should attribute to root" in long_foo_titles  # re-pinned
    assert [i["issue"] for i in pub_by_fn["helper"]] == ["bug in callee"]
    # helper's publish used the helper node's checksum (different file too).
    helper_entry = next(e for e in pub.added if e["function"] == "helper")
    assert helper_entry["function_checksum"] == "HELPER_CHECKSUM"
    assert helper_entry["file_path"] == "src/b.swift"


@pytest.mark.asyncio
async def test_call_tree_empty_issues_publishes_root_marker(tmp_path):
    """Zero issues from the LLM still records the root so the cache works."""
    ctx = AnalysisContext.from_config(
        repo_path=str(tmp_path / "repo"),
        config={
            "model": "claude-sonnet-4-5",
            "call_tree_analysis_enabled": True,
        },
        output_base_dir=str(tmp_path / "out"),
        api_key="dummy",
    )
    session = AnalysisSession.create(ctx)
    pub = StubPublisher()
    sink = AsyncResultSink(pub, repo_name=ctx.repo_name)
    fake = FakeLLM(call_tree_responses=["[]"])
    session.llm = fake

    pipeline = CodePipeline(session, sink)
    async with session:
        await pipeline.analyze_repo(
            _single_function_call_graph("longFoo"),
            FunctionFilters(),
            num_to_analyze=1,
            call_tree_builder=FakeCallTreeBuilder(),
        )

    # An empty placeholder publication under the root is recorded.
    assert any(e["function"] == "longFoo" and e["results"] == [] for e in pub.added)
