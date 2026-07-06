"""Tests for the 2 knowledge tools — dispatch through the tool registry,
subject binding, schema validation, None-store degradation, confidence
default, `behavior` field merging.
"""

from __future__ import annotations

import json

import pytest

from hindsight.core.knowledge import KnowledgeStore
from hindsight.llm.tool_protocol import ToolCall
from hindsight.llm.tools import (
    ToolContext,
    build_default_registry,
    knowledge_tool_names,
    register_knowledge_tools,
    validate_tool_parameters,
)
from hindsight.llm.tools.registry import ToolRegistry


KB_ALLOWED = frozenset(knowledge_tool_names())


@pytest.fixture
def store(tmp_path):
    s = KnowledgeStore(db_path=str(tmp_path / "k.db"), repo_name="demo")
    yield s
    s.close()


@pytest.fixture
def tool_ctx(tmp_path):
    return ToolContext(repo_path=str(tmp_path))


def _registry(tool_ctx, store, *, subject="code"):
    return build_default_registry(tool_ctx, knowledge_store=store, knowledge_subject=subject)


# ----------------------------------------------------------------------
# Schema-level validation
# ----------------------------------------------------------------------


def test_tool_names_are_unified():
    assert knowledge_tool_names() == ("lookup_knowledge", "store_knowledge")


def test_store_knowledge_only_requires_entity_key_and_summary():
    ok, _ = validate_tool_parameters(
        "store_knowledge",
        {"entity_key": "k", "summary": "s"},
    )
    assert ok, "confidence and kind should not be required — defaults apply"


def test_store_knowledge_accepts_all_fields():
    ok, _ = validate_tool_parameters(
        "store_knowledge",
        {
            "kind": "summary", "entity_key": "k", "summary": "s",
            "confidence": 0.5, "behavior": "LINE 42: allocates X",
        },
    )
    assert ok


def test_lookup_knowledge_requires_query():
    ok, err = validate_tool_parameters("lookup_knowledge", {})
    assert not ok and "query" in err


def test_lookup_knowledge_accepts_alias_q():
    ok, _ = validate_tool_parameters("lookup_knowledge", {"q": "hello"})
    assert ok


def test_lookup_knowledge_accepts_alias_function_name_and_file_path():
    ok, _ = validate_tool_parameters("lookup_knowledge", {"function_name": "parseJSON"})
    assert ok
    ok, _ = validate_tool_parameters("lookup_knowledge", {"file_path": "src/Cache.swift"})
    assert ok


def test_store_knowledge_confidence_type_validation():
    # `confidence` is optional in the schema (defaults to 0.8 at runtime), so
    # schema-level validation doesn't reject a bad type. The runtime handler
    # is the one that enforces number-ness — see the async test below.
    ok, _ = validate_tool_parameters(
        "store_knowledge",
        {"entity_key": "k", "summary": "s", "confidence": "high"},
    )
    assert ok


@pytest.mark.asyncio
async def test_store_knowledge_runtime_rejects_non_numeric_confidence(store, tool_ctx):
    reg = _registry(tool_ctx, store)
    out = await reg.execute(
        ToolCall(name="store_knowledge", args={
            "entity_key": "k", "summary": "s", "confidence": "high",
        }),
        allowed=KB_ALLOWED,
    )
    payload = json.loads(out)
    assert payload["ok"] is False
    assert "number" in payload["error"].lower()


# ----------------------------------------------------------------------
# Registry dispatch — record + lookup
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_record_then_lookup_by_function_name(store, tool_ctx):
    reg = _registry(tool_ctx, store)
    out = await reg.execute(
        ToolCall(name="store_knowledge", args={
            "kind": "summary",
            "entity_key": "src/foo.swift::myFunc",
            "summary": "parses JSON safely",
            "confidence": 0.9,
            "file_path": "src/foo.swift",
            "function_name": "myFunc",
            "checksum": "abc123",
        }),
        allowed=KB_ALLOWED,
    )
    payload = json.loads(out)
    assert payload["ok"] is True
    assert payload["id"] > 0

    # The unified FTS index covers function_name — a lookup by the raw
    # function name should surface the entry even though the summary text
    # doesn't repeat it.
    out = await reg.execute(
        ToolCall(name="lookup_knowledge", args={"query": "myFunc"}),
        allowed=KB_ALLOWED,
    )
    results = json.loads(out)
    assert len(results) == 1
    assert results[0]["summary"] == "parses JSON safely"


@pytest.mark.asyncio
async def test_lookup_by_file_path(store, tool_ctx):
    reg = _registry(tool_ctx, store)
    await reg.execute(
        ToolCall(name="store_knowledge", args={
            "kind": "summary", "entity_key": "src/Cache.swift",
            "summary": "LRU cache module",
            "file_path": "src/Cache.swift",
        }),
        allowed=KB_ALLOWED,
    )
    out = await reg.execute(
        ToolCall(name="lookup_knowledge", args={"query": "src/Cache.swift"}),
        allowed=KB_ALLOWED,
    )
    results = json.loads(out)
    assert len(results) == 1
    assert results[0]["summary"] == "LRU cache module"


@pytest.mark.asyncio
async def test_lookup_by_topic_free_text(store, tool_ctx):
    reg = _registry(tool_ctx, store)
    await reg.execute(
        ToolCall(name="store_knowledge", args={
            "kind": "invariant", "entity_key": "FooManager-threading",
            "summary": "All writes to FooManager state must happen on the main queue.",
            "tags": ["threading", "FooManager"],
        }),
        allowed=KB_ALLOWED,
    )
    out = await reg.execute(
        ToolCall(name="lookup_knowledge", args={"query": "main queue threading"}),
        allowed=KB_ALLOWED,
    )
    results = json.loads(out)
    assert len(results) == 1


@pytest.mark.asyncio
async def test_store_knowledge_defaults_confidence_to_0_8(store, tool_ctx):
    reg = _registry(tool_ctx, store)
    await reg.execute(
        ToolCall(name="store_knowledge", args={
            "entity_key": "src/foo.swift::myFunc",
            "summary": "parses input",
            "function_name": "myFunc",
        }),
        allowed=KB_ALLOWED,
    )
    hits = store.recall_by_function("code", "myFunc")
    assert len(hits) == 1
    assert hits[0]["confidence"] == 0.8


@pytest.mark.asyncio
async def test_store_knowledge_defaults_kind_to_summary(store, tool_ctx):
    reg = _registry(tool_ctx, store)
    await reg.execute(
        ToolCall(name="store_knowledge", args={
            "entity_key": "src/foo.swift::myFunc",
            "summary": "parses input",
            "function_name": "myFunc",
        }),
        allowed=KB_ALLOWED,
    )
    hits = store.recall_by_function("code", "myFunc")
    assert hits[0]["kind"] == "summary"


@pytest.mark.asyncio
async def test_behavior_field_merges_into_details(store, tool_ctx):
    reg = _registry(tool_ctx, store)
    await reg.execute(
        ToolCall(name="store_knowledge", args={
            "entity_key": "src/foo.swift::myFunc",
            "summary": "parses input",
            "function_name": "myFunc",
            "behavior": "LINE 42: allocates X per call, no pooling",
        }),
        allowed=KB_ALLOWED,
    )
    hits = store.recall_by_function("code", "myFunc")
    assert "LINE 42" in (hits[0]["details"] or "")


@pytest.mark.asyncio
async def test_subject_binding_isolates_writes(tool_ctx, tmp_path):
    """The same store can serve different subjects via separate registrations —
    writes against one subject must not bleed into another."""
    store = KnowledgeStore(db_path=str(tmp_path / "shared.db"), repo_name="demo")
    try:
        code_reg = _registry(tool_ctx, store, subject="code")
        trace_reg = ToolRegistry(tool_ctx)
        register_knowledge_tools(trace_reg, store, subject="trace")

        # Code-side write
        await code_reg.execute(
            ToolCall(name="store_knowledge", args={
                "kind": "summary", "entity_key": "src/foo::a",
                "summary": "code-only learning", "confidence": 0.8,
            }),
            allowed=KB_ALLOWED,
        )
        # Trace-side lookup must NOT see the code learning
        out = await trace_reg.execute(
            ToolCall(name="lookup_knowledge", args={"query": "code-only"}),
            allowed=KB_ALLOWED,
        )
        assert json.loads(out) == []

        # Trace-side write
        await trace_reg.execute(
            ToolCall(name="store_knowledge", args={
                "kind": "invariant", "entity_key": "lock-order",
                "summary": "trace-only invariant", "confidence": 0.7,
            }),
            allowed=KB_ALLOWED,
        )
        out = await code_reg.execute(
            ToolCall(name="lookup_knowledge", args={"query": "trace-only"}),
            allowed=KB_ALLOWED,
        )
        assert json.loads(out) == []
    finally:
        store.close()


@pytest.mark.asyncio
async def test_lookup_returns_empty_on_miss(store, tool_ctx):
    reg = _registry(tool_ctx, store)
    out = await reg.execute(
        ToolCall(name="lookup_knowledge", args={"query": "nonexistent_function"}),
        allowed=KB_ALLOWED,
    )
    assert json.loads(out) == []


@pytest.mark.asyncio
async def test_none_store_degrades_gracefully(tool_ctx):
    reg = build_default_registry(tool_ctx, knowledge_store=None)
    out = await reg.execute(
        ToolCall(name="store_knowledge", args={
            "kind": "summary", "entity_key": "k", "summary": "s", "confidence": 0.5,
        }),
        allowed=KB_ALLOWED,
    )
    payload = json.loads(out)
    assert payload["ok"] is False
    assert "unavailable" in payload["error"].lower()

    out = await reg.execute(
        ToolCall(name="lookup_knowledge", args={"query": "anything"}),
        allowed=KB_ALLOWED,
    )
    payload = json.loads(out)
    assert payload["ok"] is False


@pytest.mark.asyncio
async def test_record_rejects_invalid_kind(store, tool_ctx):
    reg = _registry(tool_ctx, store)
    out = await reg.execute(
        ToolCall(name="store_knowledge", args={
            "kind": "badkind", "entity_key": "k", "summary": "s", "confidence": 0.5,
        }),
        allowed=KB_ALLOWED,
    )
    payload = json.loads(out)
    assert payload["ok"] is False
    assert "kind" in payload["error"].lower()


@pytest.mark.asyncio
async def test_tool_not_in_allowed_set_is_blocked(store, tool_ctx):
    reg = _registry(tool_ctx, store)
    out = await reg.execute(
        ToolCall(name="store_knowledge", args={
            "kind": "summary", "entity_key": "k", "summary": "s", "confidence": 0.5,
        }),
        allowed=frozenset({"readFile"}),
    )
    assert "not available" in out


@pytest.mark.asyncio
async def test_lookup_kind_filter(store, tool_ctx):
    reg = _registry(tool_ctx, store)
    await reg.execute(ToolCall(name="store_knowledge", args={
        "kind": "summary", "entity_key": "k1", "summary": "the JSON parser",
        "confidence": 0.9,
    }), allowed=KB_ALLOWED)
    await reg.execute(ToolCall(name="store_knowledge", args={
        "kind": "invariant", "entity_key": "k2",
        "summary": "JSON parsing must happen on the parser queue",
        "confidence": 0.9,
    }), allowed=KB_ALLOWED)
    out = await reg.execute(
        ToolCall(name="lookup_knowledge", args={"query": "JSON", "kind": "invariant"}),
        allowed=KB_ALLOWED,
    )
    results = json.loads(out)
    assert len(results) == 1
    assert results[0]["kind"] == "invariant"
