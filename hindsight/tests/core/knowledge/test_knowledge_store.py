"""Tests for `KnowledgeStore` — schema, UPSERT, kind/subject filtering, FTS5,
checksum staleness handling, `delete_subject` scoping.
"""

from __future__ import annotations

import os

import pytest

from hindsight.core.knowledge import KnowledgeStore


@pytest.fixture
def store(tmp_path):
    s = KnowledgeStore(db_path=str(tmp_path / "k.db"), repo_name="demo")
    yield s
    s.close()


def test_creates_schema_on_open(tmp_path):
    db = str(tmp_path / "k.db")
    s = KnowledgeStore(db_path=db, repo_name="demo")
    assert os.path.exists(db)
    s.close()
    # Re-opening on existing DB must not fail.
    s2 = KnowledgeStore(db_path=db, repo_name="demo")
    s2.close()


def test_upsert_reuses_row_for_same_identity(store):
    a = store.record_learning(
        "code", "summary", "src/foo::a", "first",
        confidence=0.8, function_name="a", file_path="src/foo", checksum="cs1",
    )
    b = store.record_learning(
        "code", "summary", "src/foo::a", "updated",
        confidence=0.9, function_name="a", file_path="src/foo", checksum="cs1",
    )
    assert a == b
    hits = store.recall_by_function("code", "a")
    assert len(hits) == 1
    assert hits[0]["summary"] == "updated"
    assert hits[0]["confidence"] == 0.9


def test_different_checksum_creates_new_row(store):
    rid1 = store.record_learning(
        "code", "summary", "src/foo::a", "fresh",
        confidence=0.8, function_name="a", file_path="src/foo", checksum="cs1",
    )
    rid2 = store.record_learning(
        "code", "summary", "src/foo::a", "stale",
        confidence=0.7, function_name="a", file_path="src/foo", checksum="OLD",
    )
    assert rid1 != rid2
    assert len(store.recall_by_function("code", "a")) == 2
    assert len(store.recall_by_function("code", "a", checksum="cs1")) == 1


def test_subject_isolation(store):
    store.record_learning("code", "summary", "k1", "code one", confidence=0.5)
    store.record_learning("trace", "invariant", "k2", "trace one", confidence=0.5)
    store.record_learning("diff", "summary", "k3", "diff one", confidence=0.5)

    assert len(store.recall_by_topic("code", "one")) == 1
    assert len(store.recall_by_topic("trace", "one")) == 1
    assert len(store.recall_by_topic("diff", "one")) == 1


def test_kind_filter(store):
    store.record_learning("code", "summary", "k1", "summary text", confidence=0.5)
    store.record_learning("code", "invariant", "k2", "invariant text",
                          confidence=0.5)
    assert len(store.recall_by_topic("code", "text")) == 2
    assert len(store.recall_by_topic("code", "text", kind="invariant")) == 1
    assert len(store.recall_by_topic("code", "text", kind="summary")) == 1


def test_fts5_topic_search(store):
    store.record_learning("code", "summary", "k1", "parses JSON safely",
                          confidence=0.9, details="uses bounded decoder")
    store.record_learning("code", "summary", "k2", "writes binary blob",
                          confidence=0.9, details="no JSON involved here")
    hits = store.recall_by_topic("code", "JSON")
    # Both rows mention JSON (summary or details); FTS5 ranks them.
    assert {h["entity_key"] for h in hits} == {"k1", "k2"}


def test_tag_filter(store):
    store.record_learning("code", "invariant", "k1", "lock-order rule", confidence=0.9,
                          tags=["memory"])
    store.record_learning("code", "invariant", "k2", "lock-order rule", confidence=0.9,
                          tags=["concurrency"])
    hits = store.recall_by_topic("code", "lock", tags=["memory"])
    assert len(hits) == 1
    assert "memory" in hits[0]["tags"]


def test_recall_by_file(store):
    store.record_learning("code", "summary", "src/foo::a", "a", confidence=0.5,
                          file_path="src/foo", function_name="a")
    store.record_learning("code", "summary", "src/foo::b", "b", confidence=0.5,
                          file_path="src/foo", function_name="b")
    store.record_learning("code", "summary", "src/bar::c", "c", confidence=0.5,
                          file_path="src/bar", function_name="c")
    hits = store.recall_by_file("code", "src/foo")
    assert {h["function_name"] for h in hits} == {"a", "b"}


def test_delete_subject_is_scoped(store):
    store.record_learning("code", "summary", "k", "code", confidence=0.5)
    store.record_learning("trace", "invariant", "k", "trace", confidence=0.5)
    n = store.delete_subject("code")
    assert n == 1
    assert store.recall_by_topic("code", "code") == []
    assert len(store.recall_by_topic("trace", "trace")) == 1


def test_repo_isolation(tmp_path):
    db = str(tmp_path / "shared.db")
    a = KnowledgeStore(db_path=db, repo_name="repo_a")
    b = KnowledgeStore(db_path=db, repo_name="repo_b")
    try:
        a.record_learning("code", "summary", "k", "from a", confidence=0.5)
        assert len(a.recall_by_topic("code", "from")) == 1
        assert b.recall_by_topic("code", "from") == []
    finally:
        a.close()
        b.close()


def test_invalid_subject_raises(store):
    with pytest.raises(ValueError):
        store.record_learning("badsubj", "summary", "k", "v", confidence=0.5)
    with pytest.raises(ValueError):
        store.recall_by_function("badsubj", "fn")


def test_invalid_kind_raises(store):
    with pytest.raises(ValueError):
        store.record_learning("code", "badkind", "k", "v", confidence=0.5)


def test_missing_required_fields_raise(store):
    with pytest.raises(ValueError):
        store.record_learning("code", "summary", "", "summary text", confidence=0.5)
    with pytest.raises(ValueError):
        store.record_learning("code", "summary", "key", "", confidence=0.5)


def test_max_results_caps_topic_search(store):
    for i in range(15):
        store.record_learning(
            "code", "summary", f"k{i}", f"learning about JSON parsing #{i}",
            confidence=0.5,
        )
    assert len(store.recall_by_topic("code", "JSON", max_results=5)) == 5
    assert len(store.recall_by_topic("code", "JSON", max_results=20)) == 15


def test_persistence_across_open(tmp_path):
    db = str(tmp_path / "k.db")
    s = KnowledgeStore(db_path=db, repo_name="demo")
    s.record_learning("trace", "invariant", "k", "persisted", confidence=0.7)
    s.close()
    s2 = KnowledgeStore(db_path=db, repo_name="demo")
    try:
        hits = s2.recall_by_topic("trace", "persisted")
        assert len(hits) == 1
    finally:
        s2.close()


# ----------------------------------------------------------------------
# Unified FTS5 — Janus-style single lookup across identity fields
# ----------------------------------------------------------------------


def test_lookup_matches_function_name_via_fts(store):
    """The FTS index now covers function_name so a lookup by the raw
    function name surfaces the entry even when the summary/details don't
    mention it."""
    store.record_learning(
        "code", "summary", "src/foo.swift::obscureSummary", "the parser",
        confidence=0.8, file_path="src/foo.swift", function_name="obscureSummary",
    )
    hits = store.lookup("code", "obscureSummary")
    assert len(hits) == 1
    assert hits[0]["function_name"] == "obscureSummary"


def test_lookup_matches_file_path_via_fts(store):
    """The FTS index now covers file_path so a lookup by the file path
    surfaces the entry even when nothing else mentions it."""
    store.record_learning(
        "code", "summary", "src/CacheModule.swift", "an LRU cache",
        confidence=0.8, file_path="src/CacheModule.swift",
    )
    hits = store.lookup("code", "CacheModule")
    assert len(hits) >= 1
    assert any(h["file_path"] == "src/CacheModule.swift" for h in hits)


def test_lookup_matches_entity_key_via_fts(store):
    """Cross-cutting rules have free-form entity_keys — those must be
    searchable too."""
    store.record_learning(
        "code", "invariant", "FooManager-main-queue-only",
        "writes serialized to the main queue", confidence=0.8,
        tags=["threading"],
    )
    hits = store.lookup("code", "FooManager")
    assert len(hits) == 1


def test_lookup_alias_of_recall_by_topic(store):
    """The `lookup` method is the Janus-style alias — it should return the
    same result as `recall_by_topic` for equivalent queries."""
    store.record_learning(
        "code", "summary", "k", "content indexed by summary text",
        confidence=0.8,
    )
    a = store.lookup("code", "indexed")
    b = store.recall_by_topic("code", "indexed")
    assert [r["id"] for r in a] == [r["id"] for r in b]


def test_fts_migration_from_two_column_index(tmp_path):
    """An existing DB with the old 2-column FTS index (`summary, details`)
    is migrated on open. Rows written before the migration remain
    searchable via the identity fields after rebuild."""
    import sqlite3

    db = str(tmp_path / "k.db")
    # Simulate an old-format DB: create learnings + an old 2-column FTS by
    # hand and populate a row. On open, KnowledgeStore should replace the
    # FTS index and rebuild it — after which the new identity-field search
    # should work on the pre-existing row.
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE learnings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            subject TEXT NOT NULL,
            repo_name TEXT NOT NULL,
            kind TEXT NOT NULL,
            entity_key TEXT NOT NULL,
            file_path TEXT,
            function_name TEXT,
            checksum TEXT,
            summary TEXT NOT NULL,
            details TEXT,
            tags TEXT,
            severity TEXT,
            confidence REAL NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE VIRTUAL TABLE learnings_fts USING fts5(
            summary, details, content='learnings', content_rowid='id'
        );
        INSERT INTO learnings (
            subject, repo_name, kind, entity_key, file_path, function_name,
            summary, details, confidence, created_at, updated_at
        ) VALUES (
            'code', 'demo', 'summary', 'src/legacy.swift::oldFn',
            'src/legacy.swift', 'oldFn', 'inherited from old schema',
            NULL, 0.7, '2026-01-01', '2026-01-01'
        );
        """
    )
    conn.commit()
    conn.close()

    s = KnowledgeStore(db_path=db, repo_name="demo")
    try:
        # After migration + rebuild, the pre-existing row must be findable
        # by function_name (not present in summary or details).
        hits = s.lookup("code", "oldFn")
        assert len(hits) == 1
        assert hits[0]["function_name"] == "oldFn"
    finally:
        s.close()
