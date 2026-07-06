"""Unified knowledge store — shared across code, trace, and diff analyzers.

One `learnings` table with `subject` (`'code' | 'trace' | 'diff'`) and `kind`
(`'summary' | 'invariant' | 'finding' | 'optimization'`) discriminators.

`subject` is fixed per call site (the code pipeline always writes `'code'`,
the trace pipeline always writes `'trace'`) — never an LLM-facing parameter.

UPSERT uniqueness key is `(subject, repo_name, kind, entity_key, checksum, tags)`,
so re-asserting the same learning updates rather than duplicates.

WAL mode + `check_same_thread=False` lets concurrent async writers share one
connection.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from ...utils.log_util import get_logger

logger = get_logger(__name__)


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS learnings (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    subject       TEXT NOT NULL,
    repo_name     TEXT NOT NULL,
    kind          TEXT NOT NULL,
    entity_key    TEXT NOT NULL,
    file_path     TEXT,
    function_name TEXT,
    checksum      TEXT,
    summary       TEXT NOT NULL,
    details       TEXT,
    tags          TEXT,
    severity      TEXT,
    confidence    REAL NOT NULL,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_learnings_identity
    ON learnings(subject, repo_name, kind, entity_key,
                 IFNULL(checksum, ''), IFNULL(tags, ''));

CREATE INDEX IF NOT EXISTS ix_learnings_func
    ON learnings(subject, repo_name, function_name, file_path);
CREATE INDEX IF NOT EXISTS ix_learnings_file
    ON learnings(subject, repo_name, file_path);
CREATE INDEX IF NOT EXISTS ix_learnings_chk
    ON learnings(subject, repo_name, checksum);
CREATE INDEX IF NOT EXISTS ix_learnings_kind
    ON learnings(subject, repo_name, kind);

CREATE VIRTUAL TABLE IF NOT EXISTS learnings_fts USING fts5(
    summary, details, entity_key, function_name, file_path,
    content='learnings', content_rowid='id',
    tokenize='porter unicode61'
);

CREATE TRIGGER IF NOT EXISTS learnings_ai AFTER INSERT ON learnings BEGIN
    INSERT INTO learnings_fts(rowid, summary, details, entity_key, function_name, file_path)
    VALUES (new.id, new.summary, IFNULL(new.details, ''), new.entity_key,
            IFNULL(new.function_name, ''), IFNULL(new.file_path, ''));
END;

CREATE TRIGGER IF NOT EXISTS learnings_ad AFTER DELETE ON learnings BEGIN
    INSERT INTO learnings_fts(learnings_fts, rowid, summary, details, entity_key, function_name, file_path)
    VALUES ('delete', old.id, old.summary, IFNULL(old.details, ''), old.entity_key,
            IFNULL(old.function_name, ''), IFNULL(old.file_path, ''));
END;

CREATE TRIGGER IF NOT EXISTS learnings_au AFTER UPDATE ON learnings BEGIN
    INSERT INTO learnings_fts(learnings_fts, rowid, summary, details, entity_key, function_name, file_path)
    VALUES ('delete', old.id, old.summary, IFNULL(old.details, ''), old.entity_key,
            IFNULL(old.function_name, ''), IFNULL(old.file_path, ''));
    INSERT INTO learnings_fts(rowid, summary, details, entity_key, function_name, file_path)
    VALUES (new.id, new.summary, IFNULL(new.details, ''), new.entity_key,
            IFNULL(new.function_name, ''), IFNULL(new.file_path, ''));
END;
"""


VALID_SUBJECTS = frozenset({"code", "trace", "diff"})
VALID_KINDS = frozenset({"summary", "invariant"})


def _normalize_tags(tags: Any) -> Optional[str]:
    """Tags are stored as a JSON-serialized sorted list so the unique index is stable."""
    if tags is None:
        return None
    if isinstance(tags, str):
        # Caller passed a pre-encoded JSON string — re-parse to normalize.
        try:
            parsed = json.loads(tags)
        except json.JSONDecodeError:
            parsed = [tags]
    elif isinstance(tags, (list, tuple, set)):
        parsed = list(tags)
    else:
        parsed = [tags]
    cleaned = sorted({str(t) for t in parsed if t is not None})
    return json.dumps(cleaned) if cleaned else None


def _decode_tags(raw: Optional[str]) -> List[str]:
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if isinstance(value, list):
        return [str(v) for v in value]
    return []


class KnowledgeStore:
    """Single SQLite-backed store shared across all analyzer subjects.

    Constructed once per `AnalysisSession`. Safe under asyncio concurrency
    thanks to WAL + `check_same_thread=False`.
    """

    def __init__(self, db_path: str, repo_name: str):
        self._db_path = db_path
        self._repo_name = repo_name or ""
        os.makedirs(os.path.dirname(self._db_path) or ".", exist_ok=True)
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        needs_rebuild = self._migrate_fts_if_needed(self._conn)
        self._conn.executescript(_SCHEMA_SQL)
        if needs_rebuild:
            # `learnings` may already contain rows from before the FTS
            # migration; rebuild so lookup_knowledge can find them.
            self._conn.execute("INSERT INTO learnings_fts(learnings_fts) VALUES('rebuild')")
        self._conn.commit()
        # macOS libsqlite3 traps on truly-concurrent connection access even
        # with check_same_thread=False — serialize at the Python level. SQLite
        # itself is fast enough that this is not a real bottleneck here.
        self._lock = threading.Lock()
        logger.info(f"KnowledgeStore opened at {self._db_path} (repo={self._repo_name})")

    @staticmethod
    def _migrate_fts_if_needed(conn: sqlite3.Connection) -> bool:
        """Drop `learnings_fts` if its column list is out of date. Return
        True when a drop happened so the caller can rebuild the index after
        `_SCHEMA_SQL` recreates the table.

        The FTS5 index was extended to include `entity_key`, `function_name`,
        and `file_path` so a single `lookup_knowledge(query)` can match across
        all identity fields. Old DBs have a 2-column index (`summary, details`)
        and must be rebuilt; new DBs pass through untouched.
        """
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='learnings_fts'"
        ).fetchone()
        if not row:
            return False
        sql = row[0] or ""
        if "entity_key" in sql and "function_name" in sql and "file_path" in sql:
            return False
        logger.info("Migrating learnings_fts to extended schema (entity_key, function_name, file_path)")
        conn.executescript(
            """
            DROP TRIGGER IF EXISTS learnings_ai;
            DROP TRIGGER IF EXISTS learnings_ad;
            DROP TRIGGER IF EXISTS learnings_au;
            DROP TABLE IF EXISTS learnings_fts;
            """
        )
        conn.commit()
        return True

    # ------------------------------------------------------------------
    # Recall
    # ------------------------------------------------------------------

    def recall_by_function(
        self,
        subject: str,
        function_name: str,
        *,
        file_path: Optional[str] = None,
        checksum: Optional[str] = None,
        kind: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Exact-match lookup by function name (optionally narrowed by file/checksum/kind)."""
        self._check_subject(subject)
        if not function_name:
            return []
        conditions = ["subject = ?", "repo_name = ?", "function_name = ?"]
        params: List[Any] = [subject, self._repo_name, function_name]
        if file_path:
            conditions.append("file_path = ?")
            params.append(file_path)
        if checksum:
            conditions.append("checksum = ?")
            params.append(checksum)
        if kind:
            self._check_kind(kind)
            conditions.append("kind = ?")
            params.append(kind)
        sql = (
            "SELECT * FROM learnings WHERE "
            + " AND ".join(conditions)
            + " ORDER BY updated_at DESC"
        )
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def recall_by_file(
        self,
        subject: str,
        file_path: str,
        *,
        kind: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Exact-match lookup by file path."""
        self._check_subject(subject)
        if not file_path:
            return []
        conditions = ["subject = ?", "repo_name = ?", "file_path = ?"]
        params: List[Any] = [subject, self._repo_name, file_path]
        if kind:
            self._check_kind(kind)
            conditions.append("kind = ?")
            params.append(kind)
        sql = (
            "SELECT * FROM learnings WHERE "
            + " AND ".join(conditions)
            + " ORDER BY updated_at DESC"
        )
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def recall_by_topic(
        self,
        subject: str,
        query: str,
        *,
        kind: Optional[str] = None,
        tags: Optional[Iterable[str]] = None,
        max_results: int = 10,
    ) -> List[Dict[str, Any]]:
        """FTS5-ranked search across summary + details + entity_key + function_name + file_path.

        The single-tool `lookup_knowledge` (Janus-style) is backed by this
        method: one query string, all indexed fields, ranked by FTS5.
        """
        self._check_subject(subject)
        if not query or not query.strip():
            return []

        fts_query = self._build_fts_query(query)
        if not fts_query:
            return []

        conditions = ["l.subject = ?", "l.repo_name = ?", "learnings_fts MATCH ?"]
        params: List[Any] = [subject, self._repo_name, fts_query]
        if kind:
            self._check_kind(kind)
            conditions.append("l.kind = ?")
            params.append(kind)

        sql = (
            "SELECT l.* FROM learnings l "
            "JOIN learnings_fts fts ON l.id = fts.rowid "
            "WHERE " + " AND ".join(conditions) + " "
            "ORDER BY rank LIMIT ?"
        )
        params.append(max(1, max_results))
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        results = [self._row_to_dict(r) for r in rows]

        if tags:
            wanted = {str(t) for t in tags if t}
            if wanted:
                results = [r for r in results if wanted & set(r.get("tags") or [])]
        return results

    # `lookup` is the alias the LLM-facing `lookup_knowledge` tool uses. Kept
    # as a distinct method so callers reading it grep-clean; internally it
    # delegates to `recall_by_topic` (which now indexes identity fields too).
    def lookup(
        self,
        subject: str,
        query: str,
        *,
        kind: Optional[str] = None,
        max_results: int = 5,
    ) -> List[Dict[str, Any]]:
        """Single-tool Janus-style lookup — FTS5-ranked across every indexed field."""
        return self.recall_by_topic(subject, query, kind=kind, max_results=max_results)

    # ------------------------------------------------------------------
    # Record (UPSERT)
    # ------------------------------------------------------------------

    def record_learning(
        self,
        subject: str,
        kind: str,
        entity_key: str,
        summary: str,
        *,
        confidence: float,
        file_path: Optional[str] = None,
        function_name: Optional[str] = None,
        checksum: Optional[str] = None,
        details: Optional[str] = None,
        tags: Any = None,
        severity: Optional[str] = None,
    ) -> int:
        """UPSERT a learning. Returns the rowid (new or existing)."""
        self._check_subject(subject)
        self._check_kind(kind)
        if not entity_key:
            raise ValueError("entity_key is required")
        if not summary:
            raise ValueError("summary is required")

        now = datetime.now(timezone.utc).isoformat()
        tags_norm = _normalize_tags(tags)
        # The unique index uses IFNULL(checksum,'') / IFNULL(tags,''), so the
        # conflict-target columns must match — pass NULLs through, sqlite will
        # match on the indexed expression.
        with self._lock:
            cursor = self._conn.execute(
                """
                INSERT INTO learnings (
                    subject, repo_name, kind, entity_key, file_path, function_name,
                    checksum, summary, details, tags, severity, confidence,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(subject, repo_name, kind, entity_key,
                            IFNULL(checksum, ''), IFNULL(tags, ''))
                DO UPDATE SET
                    summary = excluded.summary,
                    details = excluded.details,
                    file_path = excluded.file_path,
                    function_name = excluded.function_name,
                    severity = excluded.severity,
                    confidence = excluded.confidence,
                    updated_at = excluded.updated_at
                """,
                (
                    subject, self._repo_name, kind, entity_key, file_path, function_name,
                    checksum, summary, details, tags_norm, severity, float(confidence),
                    now, now,
                ),
            )
            self._conn.commit()
            rowid = cursor.lastrowid
            # On UPDATE, lastrowid is 0 — look up the actual id.
            if not rowid:
                row = self._conn.execute(
                    """SELECT id FROM learnings
                       WHERE subject = ? AND repo_name = ? AND kind = ? AND entity_key = ?
                         AND IFNULL(checksum, '') = IFNULL(?, '')
                         AND IFNULL(tags, '') = IFNULL(?, '')""",
                    (subject, self._repo_name, kind, entity_key, checksum, tags_norm),
                ).fetchone()
                rowid = row["id"] if row else 0
        return int(rowid)

    # ------------------------------------------------------------------
    # Bulk operations
    # ------------------------------------------------------------------

    def delete_subject(self, subject: str) -> int:
        """Drop all rows for one subject in this repo. Returns rows deleted."""
        self._check_subject(subject)
        with self._lock:
            cursor = self._conn.execute(
                "DELETE FROM learnings WHERE subject = ? AND repo_name = ?",
                (subject, self._repo_name),
            )
            self._conn.commit()
        deleted = cursor.rowcount or 0
        logger.info(
            f"KnowledgeStore.delete_subject(subject={subject!r}, repo={self._repo_name!r}) "
            f"→ {deleted} rows"
        )
        return deleted

    def close(self) -> None:
        """Close the underlying connection. Safe to call repeatedly."""
        try:
            with self._lock:
                self._conn.close()
        except Exception as exc:  # noqa: BLE001 — best-effort close
            logger.debug(f"KnowledgeStore.close failed: {exc}")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @property
    def repo_name(self) -> str:
        return self._repo_name

    @property
    def db_path(self) -> str:
        return self._db_path

    @staticmethod
    def _check_subject(subject: str) -> None:
        if subject not in VALID_SUBJECTS:
            raise ValueError(
                f"Invalid subject {subject!r}; expected one of {sorted(VALID_SUBJECTS)}"
            )

    @staticmethod
    def _check_kind(kind: str) -> None:
        if kind not in VALID_KINDS:
            raise ValueError(
                f"Invalid kind {kind!r}; expected one of {sorted(VALID_KINDS)}"
            )

    @staticmethod
    def _build_fts_query(query: str) -> str:
        """Tokenize a free-form query into an FTS5 OR-of-phrases query.

        Two-stage strategy:
          1. Split on whitespace to get "semantic groups" (each group is one
             identifier the LLM meant to search for — a function name, a file
             path, a topic phrase).
          2. Within each group, aggressively replace non-alphanumeric
             (except underscore) with spaces so that `::`, `.`, `->`, `(…)`,
             `-[…:]`, `<T>` etc. all reduce to identifier subtokens.

        Each group becomes ONE quoted phrase — inside a quoted phrase, FTS5
        requires the subtokens to be adjacent in the indexed text. Groups
        are OR-joined. This preserves the LLM's intent (`code-only` vs
        `trace-only` are distinct phrases and don't collide on the shared
        `only` subtoken) while remaining loose across formatting differences
        (`MyClass::myMethod` matches an entity_key stored as
        `src/foo.swift::MyClass.myMethod` because both tokenize to the
        adjacent pair `MyClass myMethod`).
        """
        # Whitespace split gives us the LLM's semantic groups.
        groups = query.split()
        phrases: List[str] = []
        for group in groups:
            # Reduce non-alphanumeric to spaces, then collapse the resulting
            # subtokens into a single space-joined phrase. Keeps `_` (part of
            # snake_case identifiers) as a word character.
            cleaned = re.sub(r"[^\w]+", " ", group, flags=re.UNICODE).strip()
            if not cleaned:
                continue
            # Drop 1-char subtokens as noise (single letters like generic
            # parameters rarely help). If the whole phrase collapses to
            # nothing, skip it.
            subtokens = [t for t in cleaned.split() if len(t) >= 2]
            if not subtokens:
                continue
            phrase = " ".join(subtokens)
            phrases.append(f'"{phrase}"')
        if not phrases:
            return ""
        # Cap total phrases to keep the FTS query bounded.
        phrases = phrases[:12]
        return " OR ".join(phrases)

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
        out = {k: row[k] for k in row.keys()}
        out["tags"] = _decode_tags(out.get("tags"))
        return out
