"""End-to-end tests for `DiffPipeline` with a fake LLM client.

Mirrors `test_pipeline_code.py` but for the diff workflow:
  - Stage Da → Db happy path
  - Cache hit (republish without LLM)
  - Per-function failure isolated
  - Diff-context bundle persisted to disk
  - Call-tree mode with issue grouping
  - Sanitization at the sink
  - Issue filter hook
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from hindsight.llm import AsyncLLMClient, LLMResponse
from hindsight.llm.bedrock import LLMClientConfig
from hindsight.orchestration import (
    AnalysisContext,
    AnalysisSession,
    AsyncResultSink,
    DiffCallTreeWork,
    DiffFunctionWork,
    DiffPipeline,
    DiffRunSummary,
)
from hindsight.orchestration.pipeline_diff import (
    _build_changed_lines_map,
    _build_changed_lines_map_from_diff_context,
    _filter_issues_to_changed_lines,
    _parse_line_number,
)


# ----------------------------------------------------------------------
# Fakes — reuse the same shape as test_pipeline_code.FakeLLM but route by
# system-prompt content distinct to the diff stages.
# ----------------------------------------------------------------------


class FakeLLM(AsyncLLMClient):
    """Routes by inspecting the FIRST user message for distinctive stage markers.

    The system prompts for Stage Da/Db both mention "context", so we route
    on the user message instead — the pipeline builds these with stable
    headings: `## Function Being Analyzed` (Da), `## Diff Code for Analysis`
    (Db), and the call-tree prompt uses `PromptBuilder.build_diff_call_tree_prompt`
    which produces a different shape.
    """

    def __init__(
        self,
        *,
        context_responses: Optional[List[str]] = None,
        analysis_responses: Optional[List[str]] = None,
        call_tree_responses: Optional[List[str]] = None,
    ):
        self.config = LLMClientConfig(api_url="http://fake", model="claude-sonnet-4-5", max_tokens=64000)
        self._ctx_q = list(context_responses or [])
        self._ana_q = list(analysis_responses or [])
        self._ct_q = list(call_tree_responses or [])
        self.sends: List[Dict[str, Any]] = []

    async def send(
        self,
        system_prompt,
        messages,
        *,
        enable_system_cache=True,
        cache_ttl="1h",
    ) -> LLMResponse:
        self.sends.append({"system": (system_prompt or "")[:80], "messages": messages})
        first_user = ""
        for m in messages:
            if m.get("role") == "user":
                first_user = str(m.get("content", ""))
                break
        if "## Diff Code for Analysis" in first_user:
            text = self._next(self._ana_q)
        elif "## Function Being Analyzed" in first_user:
            text = self._next(self._ctx_q)
        else:
            text = self._next(self._ct_q)
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

    @staticmethod
    def _next(queue: List[str]) -> str:
        return queue.pop(0) if queue else "[]"


class StubPublisher:
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
def session_pub_sink(tmp_path):
    ctx = AnalysisContext.from_config(
        repo_path=str(tmp_path / "repo"),
        config={"model": "claude-sonnet-4-5", "code_analyzer_workers": 2},
        output_base_dir=str(tmp_path / "out"),
        api_key="dummy",
    )
    session = AnalysisSession.create(ctx)
    pub = StubPublisher()
    sink = AsyncResultSink(pub, repo_name=ctx.repo_name)
    return session, pub, sink


def _make_function_work(function: str = "modified_fn", *, checksum: str = "abc123") -> DiffFunctionWork:
    prompt_data = {
        "function": function,
        "file_path": "src/foo.swift",
        "code": "+ func {fn}() {{ return 1 }}".format(fn=function),
        "changed_lines": [10],
        "affected_reason": "modified",
        "data_types_used": [],
        "constants_used": {},
        "invoked_functions": [],
        "invoking_functions": [],
        "diff_context": {"all_changed_files": ["src/foo.swift"]},
    }
    return DiffFunctionWork(
        prompt_data=prompt_data,
        function_name=function,
        file_path="src/foo.swift",
        function_checksum=checksum,
    )


def _stage_da_bundle(function: str = "modified_fn") -> str:
    return json.dumps({
        "schema_version": "1.0",
        "primary_function": {
            "function_name": function,
            "file_path": "src/foo.swift",
            "source": " 10: + func {} {{}}".format(function),
            "changed_lines": [{"line": 10, "marker": "+", "code": "..."}],
            "is_modified": True,
        },
        "callees": [],
        "callers": [],
    })


# ----------------------------------------------------------------------
# Happy path
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_analyze_diff_per_function_happy_path(session_pub_sink):
    session, pub, sink = session_pub_sink
    fake = FakeLLM(
        context_responses=[_stage_da_bundle("foo"), _stage_da_bundle("bar")],
        analysis_responses=[
            '[{"issue": "issue alpha", "severity": "high"}]',
            "[]",
        ],
    )
    session.llm = fake

    pipeline = DiffPipeline(session, sink)
    work = [_make_function_work("foo", checksum="cs-foo"), _make_function_work("bar", checksum="cs-bar")]

    async with session:
        summary = await pipeline.analyze_diff_per_function(work)

    assert summary.error is None
    assert summary.selected == 2
    assert summary.successful == 2
    assert summary.failed == 0
    # Both functions published once each.
    assert len(pub.added) == 2
    assert {a["function"] for a in pub.added} == {"foo", "bar"}
    # One issue across both. Sanitized through the sink.
    all_issues = [i for entry in pub.added for i in entry["results"]]
    assert len(all_issues) == 1
    assert all_issues[0]["issue"] == "issue alpha"
    assert all_issues[0]["category"] == "general"  # default filled in


@pytest.mark.asyncio
async def test_analyze_diff_emits_full_event_sequence(session_pub_sink):
    session, _pub, sink = session_pub_sink
    fake = FakeLLM(
        context_responses=[_stage_da_bundle("foo")],
        analysis_responses=["[]"],
    )
    session.llm = fake

    received: List[str] = []
    pipeline = DiffPipeline(session, sink)
    async with session:
        session.subscribe(lambda e: received.append(e.type))
        await pipeline.analyze_diff_per_function([_make_function_work("foo")])

    assert received[0] == "run_started"
    assert received[-1] == "run_completed"
    assert received.count("function_started") == 1
    assert received.count("function_complete") == 1


# ----------------------------------------------------------------------
# Cache hit
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cache_hit_skips_llm(tmp_path):
    ctx = AnalysisContext.from_config(
        repo_path=str(tmp_path / "repo"),
        config={"model": "claude-sonnet-4-5"},
        output_base_dir=str(tmp_path / "out"),
        api_key="dummy",
    )
    session = AnalysisSession.create(ctx)
    pub = StubPublisher(cached={
        ("src/foo.swift", "modified_fn", "abc123"): {
            "results": [{"issue": "cached", "severity": "low", "category": "memory"}]
        }
    })
    sink = AsyncResultSink(pub, repo_name=ctx.repo_name)
    fake = FakeLLM()  # empty queues — would fail if LLM is invoked
    session.llm = fake

    pipeline = DiffPipeline(session, sink)
    async with session:
        summary = await pipeline.analyze_diff_per_function(
            [_make_function_work("modified_fn", checksum="abc123")]
        )

    assert summary.cached == 1
    assert summary.successful == 0
    assert fake.sends == []  # LLM never called
    assert pub.added[0]["results"][0]["issue"] == "cached"


# ----------------------------------------------------------------------
# Per-function failure isolation
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_per_function_failure_isolated(session_pub_sink):
    session, pub, sink = session_pub_sink
    # One Stage Da response is garbage, the other is valid. With concurrent
    # dispatch we can't pin which function gets which response — we just
    # assert that exactly one of the two failed and the other landed.
    fake = FakeLLM(
        context_responses=["not JSON at all", _stage_da_bundle("either")],
        analysis_responses=["[]"],
    )
    session.llm = fake

    failure_events: List[Any] = []
    pipeline = DiffPipeline(session, sink)
    async with session:
        session.subscribe(
            lambda e: failure_events.append(e) if e.type == "function_failed" else None
        )
        summary = await pipeline.analyze_diff_per_function([
            _make_function_work("foo", checksum="cs-foo"),
            _make_function_work("bar", checksum="cs-bar"),
        ])

    assert summary.successful == 1
    assert summary.failed == 1
    assert summary.error is None
    assert len(failure_events) == 1
    # Exactly one function published; the other failed and never reached publish.
    assert len(pub.added) == 1
    assert pub.added[0]["function"] in {"foo", "bar"}


# ----------------------------------------------------------------------
# Bundle persisted to disk
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stage_da_bundle_persisted(session_pub_sink):
    session, _pub, sink = session_pub_sink
    fake = FakeLLM(
        context_responses=[_stage_da_bundle("foo")],
        analysis_responses=["[]"],
    )
    session.llm = fake

    pipeline = DiffPipeline(session, sink)
    async with session:
        await pipeline.analyze_diff_per_function([_make_function_work("foo")])

    from hindsight.orchestration.pipeline_diff import DiffPipeline as _DP
    cs = _DP._bundle_checksum("foo", "src/foo.swift")
    bundle_path = Path(session.ctx.diff_context_bundles_dir) / f"{cs[:8]}.json"
    assert bundle_path.exists()
    saved = json.loads(bundle_path.read_text())
    assert saved["primary_function"]["function_name"] == "foo"


# ----------------------------------------------------------------------
# Sanitization
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_published_issues_are_sanitized(session_pub_sink):
    session, pub, sink = session_pub_sink
    fake = FakeLLM(
        context_responses=[_stage_da_bundle("foo")],
        analysis_responses=['[{"description": "no severity"}]'],
    )
    session.llm = fake

    pipeline = DiffPipeline(session, sink)
    async with session:
        await pipeline.analyze_diff_per_function([_make_function_work("foo")])

    issue = pub.added[0]["results"][0]
    assert issue["severity"] == "medium"
    assert issue["category"] == "general"
    assert issue["issue"] == "no severity"


# ----------------------------------------------------------------------
# Issue filter hook
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_issue_filter_runs_before_publish(session_pub_sink):
    session, pub, sink = session_pub_sink
    fake = FakeLLM(
        context_responses=[_stage_da_bundle("foo")],
        analysis_responses=[
            '[{"issue": "drop me", "category": "trivial"}, {"issue": "keep me"}]'
        ],
    )
    session.llm = fake

    def drop_trivial(issues, function_context):
        return [i for i in issues if i.get("category") != "trivial"]

    pipeline = DiffPipeline(session, sink, issue_filter=drop_trivial)
    async with session:
        await pipeline.analyze_diff_per_function([_make_function_work("foo")])

    assert [i["issue"] for i in pub.added[0]["results"]] == ["keep me"]


# ----------------------------------------------------------------------
# Token callback
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_token_callback_called_per_llm_call(session_pub_sink):
    session, _pub, sink = session_pub_sink
    fake = FakeLLM(
        context_responses=[_stage_da_bundle("foo")],
        analysis_responses=["[]"],
    )
    session.llm = fake

    token_events: List[tuple[int, int]] = []
    pipeline = DiffPipeline(
        session, sink,
        token_callback=lambda i, o: token_events.append((i, o)),
    )
    async with session:
        await pipeline.analyze_diff_per_function([_make_function_work("foo")])

    # Stage Da + Stage Db = 2 LLM calls.
    assert len(token_events) == 2


# ----------------------------------------------------------------------
# Empty input
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_work_list_completes_cleanly(session_pub_sink):
    session, _pub, sink = session_pub_sink
    received: List[str] = []
    pipeline = DiffPipeline(session, sink)
    async with session:
        session.subscribe(lambda e: received.append(e.type))
        summary = await pipeline.analyze_diff_per_function([])

    assert summary.selected == 0
    assert summary.successful == summary.failed == summary.cached == 0
    assert received == ["run_started", "run_completed"]


# ----------------------------------------------------------------------
# Changed-lines filter — unit + integration
# ----------------------------------------------------------------------


def test_parse_line_number_handles_int_string_range_and_garbage():
    assert _parse_line_number(42) == (42, 42)
    assert _parse_line_number("42") == (42, 42)
    assert _parse_line_number("  42  ") == (42, 42)
    assert _parse_line_number("45-48") == (45, 48)
    assert _parse_line_number("48-45") == (45, 48)  # swapped
    assert _parse_line_number(None) is None
    assert _parse_line_number("") is None
    assert _parse_line_number("foo") is None
    assert _parse_line_number("variableName") is None


def test_filter_drops_issues_outside_changed_lines():
    issues = [
        {"file_path": "a.swift", "line_number": "10", "issue": "on change"},
        {"file_path": "a.swift", "line_number": "11", "issue": "adjacent"},
        {"file_path": "a.swift", "line_number": "99", "issue": "far away"},
        {"file_path": "a.swift", "line_number": "5", "issue": "before"},
    ]
    changed = {"a.swift": {10}}
    kept, dropped = _filter_issues_to_changed_lines(issues, changed, neighborhood=2)
    assert dropped == 2
    assert [i["issue"] for i in kept] == ["on change", "adjacent"]


def test_filter_handles_line_ranges():
    issues = [
        {"file_path": "a.swift", "line_number": "10-12", "issue": "spans change"},
        {"file_path": "a.swift", "line_number": "20-25", "issue": "no overlap"},
    ]
    changed = {"a.swift": {12}}
    kept, dropped = _filter_issues_to_changed_lines(issues, changed, neighborhood=1)
    assert dropped == 1
    assert [i["issue"] for i in kept] == ["spans change"]


def test_filter_drops_issues_on_files_not_in_diff():
    issues = [
        {"file_path": "a.swift", "line_number": "10", "issue": "in-diff file"},
        {"file_path": "b.swift", "line_number": "10", "issue": "not in diff"},
    ]
    changed = {"a.swift": {10}}
    kept, dropped = _filter_issues_to_changed_lines(issues, changed)
    assert dropped == 1
    assert [i["issue"] for i in kept] == ["in-diff file"]


def test_filter_keeps_issues_with_unparseable_line_number():
    # When the model emits a variable name instead of a line number, prefer
    # keeping the finding — we can't tell where it points.
    issues = [
        {"file_path": "a.swift", "line_number": "foo()", "issue": "vague"},
    ]
    changed = {"a.swift": {10}}
    kept, dropped = _filter_issues_to_changed_lines(issues, changed)
    assert dropped == 0
    assert len(kept) == 1


def test_filter_no_op_when_no_diff_info():
    issues = [{"file_path": "a.swift", "line_number": "99", "issue": "x"}]
    kept, dropped = _filter_issues_to_changed_lines(issues, {})
    assert dropped == 0
    assert kept == issues


def test_build_changed_lines_map_from_prompt_data():
    prompt = {"file_path": "src/foo.swift", "changed_lines": [10, 11, 15]}
    assert _build_changed_lines_map(prompt) == {"src/foo.swift": {10, 11, 15}}


def test_build_changed_lines_map_from_diff_context_uses_added_only():
    ctx = {
        "changed_lines_per_file": {
            "a.swift": {"added": [10, 11], "removed": [5]},
            "b.swift": {"added": [], "removed": [99]},
        }
    }
    result = _build_changed_lines_map_from_diff_context(ctx)
    # `removed` ignored; `b.swift` skipped (no added lines).
    assert result == {"a.swift": {10, 11}}


@pytest.mark.asyncio
async def test_per_function_drops_issues_outside_changed_lines(session_pub_sink):
    """End-to-end: the LLM hallucinates an issue on an unchanged line; the
    filter drops it before the publisher sees it."""
    session, pub, sink = session_pub_sink
    fake = FakeLLM(
        context_responses=[_stage_da_bundle("foo")],
        analysis_responses=[
            json.dumps([
                {"file_path": "src/foo.swift", "line_number": "10", "issue": "on change"},
                {"file_path": "src/foo.swift", "line_number": "999", "issue": "pre-existing"},
            ])
        ],
    )
    session.llm = fake

    pipeline = DiffPipeline(session, sink)
    async with session:
        await pipeline.analyze_diff_per_function([_make_function_work("foo")])

    # Only the on-change issue survives.
    assert len(pub.added) == 1
    published = pub.added[0]["results"]
    assert len(published) == 1
    assert published[0]["issue"] == "on change"


# ----------------------------------------------------------------------
# Call-tree mode — issue grouping with out-of-tree re-pinning
# ----------------------------------------------------------------------


def _make_call_tree_work() -> DiffCallTreeWork:
    """Two-node diff-marked tree rooted at `root_fn`."""
    return DiffCallTreeWork(
        tree_dict={
            "schema_version": "2.0",
            "root": {"function": "root_fn", "file": "src/a.swift", "checksum": "ROOT_CS"},
            "nodes": [
                {
                    "function": "root_fn",
                    "file": "src/a.swift",
                    "checksum": "ROOT_CS",
                    "is_modified": True,
                    "changed_lines": [10],
                },
                {
                    "function": "helper_fn",
                    "file": "src/b.swift",
                    "checksum": "HELPER_CS",
                    "is_modified": False,
                    "changed_lines": [],
                },
            ],
            "stats": {"node_count": 2, "total_chars": 200, "tree_signature": "sig"},
            "truncation": {
                "depth_cap_hit": False, "char_cap_hit": False,
                "node_cap_hit": False, "stubbed_nodes": [],
            },
        },
        diff_context={
            "all_changed_files": ["src/a.swift"],
            "changed_lines_per_file": {"src/a.swift": {"added": [10], "removed": []}},
        },
        root_name="root_fn",
        root_file="src/a.swift",
        root_checksum="ROOT_CS",
    )


@pytest.mark.asyncio
async def test_call_tree_groups_issues_by_defect_function(session_pub_sink):
    session, pub, sink = session_pub_sink
    fake = FakeLLM(call_tree_responses=[json.dumps([
        {"defect_function": "root_fn", "issue": "bug in root"},
        {"defect_function": "helper_fn", "issue": "bug in helper"},
        {"defect_function": "out_of_tree", "issue": "re-pin to root"},
    ])])
    session.llm = fake

    pipeline = DiffPipeline(session, sink)
    async with session:
        summary = await pipeline.analyze_diff_call_tree([_make_call_tree_work()])

    assert summary.error is None
    assert summary.successful == 1

    by_fn: Dict[str, List[Dict[str, Any]]] = {}
    for entry in pub.added:
        by_fn.setdefault(entry["function"], []).extend(entry["results"])
    assert "root_fn" in by_fn and "helper_fn" in by_fn
    root_titles = [i["issue"] for i in by_fn["root_fn"]]
    assert "bug in root" in root_titles
    assert "re-pin to root" in root_titles  # out-of-tree → root
    assert [i["issue"] for i in by_fn["helper_fn"]] == ["bug in helper"]
    # helper's publish used the helper node's checksum + file.
    helper_entry = next(e for e in pub.added if e["function"] == "helper_fn")
    assert helper_entry["function_checksum"] == "HELPER_CS"
    assert helper_entry["file_path"] == "src/b.swift"


@pytest.mark.asyncio
async def test_call_tree_empty_issues_records_root_marker(session_pub_sink):
    session, pub, sink = session_pub_sink
    fake = FakeLLM(call_tree_responses=["[]"])
    session.llm = fake

    pipeline = DiffPipeline(session, sink)
    async with session:
        await pipeline.analyze_diff_call_tree([_make_call_tree_work()])

    # Empty issues → still records a placeholder under the root.
    assert any(
        e["function"] == "root_fn" and e["results"] == [] for e in pub.added
    )


@pytest.mark.asyncio
async def test_call_tree_garbled_llm_response_yields_empty(session_pub_sink):
    """When the LLM never returns valid JSON, parse falls back to empty list.

    That's a deliberate behavior: empty list is a legitimate "no defects"
    answer, so a stuck LLM degrades to "no findings" rather than a hard
    failure. The root marker is still recorded so the cache reflects "tried".
    """
    session, pub, sink = session_pub_sink
    fake = FakeLLM(call_tree_responses=["this is not JSON"])
    session.llm = fake

    pipeline = DiffPipeline(session, sink)
    async with session:
        summary = await pipeline.analyze_diff_call_tree([_make_call_tree_work()])

    assert summary.error is None
    # No `defect_function` keys parseable → no per-function groups → root marker only.
    assert any(
        e["function"] == "root_fn" and e["results"] == [] for e in pub.added
    )
