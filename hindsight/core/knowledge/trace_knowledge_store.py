#!/usr/bin/env python3
"""
Trace Knowledge Store — SQLite+FTS5 persistent store for trace analysis learnings.

Two tables:
- trace_learnings: General callstack-level learnings (keyed by entity_key)
- function_optimizations: Function-level optimization cache (keyed by file_path + function_name)

Uses FTS5 for full-text search and WAL mode for concurrent access.
"""

import json
import os
import sqlite3
from datetime import datetime, timezone
from typing import Dict, List, Optional

from ...utils.log_util import get_logger

logger = get_logger(__name__)

_DEFAULT_DB_PATH = os.path.join(os.path.expanduser("~"), ".hindsight", "trace_knowledge.db")

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS trace_learnings (
    id INTEGER PRIMARY KEY,
    entity_key TEXT NOT NULL,
    summary TEXT NOT NULL,
    related_context TEXT DEFAULT '',
    confidence REAL DEFAULT 0.8,
    created_at TEXT NOT NULL,
    repo_name TEXT DEFAULT ''
);

CREATE VIRTUAL TABLE IF NOT EXISTS trace_learnings_fts USING fts5(
    entity_key,
    summary,
    related_context,
    content='trace_learnings',
    content_rowid='id'
);

CREATE TRIGGER IF NOT EXISTS trace_learnings_ai AFTER INSERT ON trace_learnings BEGIN
    INSERT INTO trace_learnings_fts(rowid, entity_key, summary, related_context)
    VALUES (new.id, new.entity_key, new.summary, new.related_context);
END;

CREATE TRIGGER IF NOT EXISTS trace_learnings_ad AFTER DELETE ON trace_learnings BEGIN
    INSERT INTO trace_learnings_fts(trace_learnings_fts, rowid, entity_key, summary, related_context)
    VALUES ('delete', old.id, old.entity_key, old.summary, old.related_context);
END;

CREATE TRIGGER IF NOT EXISTS trace_learnings_au AFTER UPDATE ON trace_learnings BEGIN
    INSERT INTO trace_learnings_fts(trace_learnings_fts, rowid, entity_key, summary, related_context)
    VALUES ('delete', old.id, old.entity_key, old.summary, old.related_context);
    INSERT INTO trace_learnings_fts(rowid, entity_key, summary, related_context)
    VALUES (new.id, new.entity_key, new.summary, new.related_context);
END;

CREATE TABLE IF NOT EXISTS function_optimizations (
    id INTEGER PRIMARY KEY,
    file_path TEXT NOT NULL,
    function_name TEXT NOT NULL,
    summary TEXT NOT NULL,
    details TEXT DEFAULT '',
    severity TEXT DEFAULT '',
    confidence REAL DEFAULT 0.8,
    created_at TEXT NOT NULL,
    repo_name TEXT DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_func_opt_path_name
    ON function_optimizations(file_path, function_name);

CREATE INDEX IF NOT EXISTS idx_func_opt_name
    ON function_optimizations(function_name);

CREATE INDEX IF NOT EXISTS idx_func_opt_repo
    ON function_optimizations(repo_name);
"""


class TraceKnowledgeStore:
    """
    SQLite+FTS5 store for trace analysis learnings and function optimization cache.

    Thread-safe: uses check_same_thread=False and WAL journal mode.
    """

    TOOL_NAMES = {"lookup_knowledge", "store_learning", "lookup_function_optimization", "store_function_optimization"}

    def __init__(self, db_path: str = None):
        self._db_path = db_path or _DEFAULT_DB_PATH
        os.makedirs(os.path.dirname(self._db_path), exist_ok=True)
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._init_schema()
        logger.info(f"TraceKnowledgeStore opened at {self._db_path}")

    def _init_schema(self) -> None:
        self._conn.executescript(_SCHEMA_SQL)
        self._conn.commit()

    # ─── General learnings (trace_learnings table) ─────────────────────────

    def store_learning(
        self,
        entity_key: str,
        summary: str,
        related_context: str = "",
        confidence: float = 0.8,
        repo_name: str = "",
    ) -> int:
        """Insert a general learning and return its rowid."""
        created_at = datetime.now(timezone.utc).isoformat()
        cursor = self._conn.execute(
            """INSERT INTO trace_learnings (entity_key, summary, related_context, confidence, created_at, repo_name)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (entity_key, summary, related_context, confidence, created_at, repo_name),
        )
        self._conn.commit()
        logger.debug(f"Stored learning: entity_key={entity_key}, id={cursor.lastrowid}")
        return cursor.lastrowid

    def query_knowledge(self, query: str, max_results: int = 5, repo_name: str = None) -> List[Dict]:
        """FTS5 search across entity_key, summary, and related_context."""
        if not query.strip():
            return []

        import re
        normalized = re.sub(r'[:?\[\]<>]', ' ', query)
        tokens = [t for t in normalized.split() if t]
        if not tokens:
            return []
        fts_query = " OR ".join(f'"{t.replace(chr(34), chr(34)+chr(34))}"' for t in tokens)

        if repo_name:
            rows = self._conn.execute(
                """SELECT tl.* FROM trace_learnings tl
                   JOIN trace_learnings_fts fts ON tl.id = fts.rowid
                   WHERE trace_learnings_fts MATCH ? AND tl.repo_name = ?
                   ORDER BY rank
                   LIMIT ?""",
                (fts_query, repo_name, max_results),
            ).fetchall()
        else:
            rows = self._conn.execute(
                """SELECT tl.* FROM trace_learnings tl
                   JOIN trace_learnings_fts fts ON tl.id = fts.rowid
                   WHERE trace_learnings_fts MATCH ?
                   ORDER BY rank
                   LIMIT ?""",
                (fts_query, max_results),
            ).fetchall()

        return [dict(row) for row in rows]

    def get_by_entity(self, entity_key: str) -> List[Dict]:
        """Exact match on entity_key."""
        rows = self._conn.execute(
            "SELECT * FROM trace_learnings WHERE entity_key = ? ORDER BY created_at DESC",
            (entity_key,),
        ).fetchall()
        return [dict(row) for row in rows]

    def get_recent(self, limit: int = 20, repo_name: str = None) -> List[Dict]:
        """Most recent learnings, optionally filtered by repo_name."""
        if repo_name:
            rows = self._conn.execute(
                "SELECT * FROM trace_learnings WHERE repo_name = ? ORDER BY created_at DESC LIMIT ?",
                (repo_name, limit),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM trace_learnings ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    # ─── Function optimization cache (function_optimizations table) ────────

    def store_function_optimization(
        self,
        file_path: str,
        function_name: str,
        summary: str,
        details: str = "",
        severity: str = "",
        confidence: float = 0.8,
        repo_name: str = "",
    ) -> int:
        """
        Cache a function-level optimization finding.

        Uses file_path + function_name as the logical key (function names can
        repeat across files).
        """
        created_at = datetime.now(timezone.utc).isoformat()
        cursor = self._conn.execute(
            """INSERT INTO function_optimizations
               (file_path, function_name, summary, details, severity, confidence, created_at, repo_name)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (file_path, function_name, summary, details, severity, confidence, created_at, repo_name),
        )
        self._conn.commit()
        logger.debug(f"Stored function optimization: {file_path}::{function_name}, id={cursor.lastrowid}")
        return cursor.lastrowid

    def lookup_function_optimization(
        self,
        function_name: str,
        file_path: Optional[str] = None,
        max_results: int = 10,
        repo_name: str = None,
    ) -> List[Dict]:
        """
        Look up cached function optimizations using loose matching.

        Uses LIKE with wildcards (SQL equivalent of .* pattern) so partial
        function names match. For example, querying "processData" would match
        "MyClass::processData", "processDataFrame", etc.

        Args:
            function_name: Function name or substring to search for (uses LIKE %name%)
            file_path: Optional file path to narrow results (also uses LIKE %path%)
            max_results: Maximum number of results to return
            repo_name: Optional repo filter
        """
        conditions = ["function_name LIKE ?"]
        params: list = [f"%{function_name}%"]

        if file_path:
            conditions.append("file_path LIKE ?")
            params.append(f"%{file_path}%")

        if repo_name:
            conditions.append("repo_name = ?")
            params.append(repo_name)

        where_clause = " AND ".join(conditions)
        params.append(max_results)

        rows = self._conn.execute(
            f"""SELECT * FROM function_optimizations
                WHERE {where_clause}
                ORDER BY created_at DESC
                LIMIT ?""",
            params,
        ).fetchall()

        return [dict(row) for row in rows]

    def get_function_optimization_exact(
        self, file_path: str, function_name: str
    ) -> List[Dict]:
        """Exact match on file_path + function_name."""
        rows = self._conn.execute(
            """SELECT * FROM function_optimizations
               WHERE file_path = ? AND function_name = ?
               ORDER BY created_at DESC""",
            (file_path, function_name),
        ).fetchall()
        return [dict(row) for row in rows]

    # ─── Tool dispatch interface ───────────────────────────────────────────

    def execute_tool(self, tool_name: str, params: Dict) -> str:
        """Execute a trace knowledge tool by name. Returns JSON string."""
        dispatch = {
            "lookup_knowledge": self._tool_lookup,
            "store_learning": self._tool_store,
            "lookup_function_optimization": self._tool_lookup_function,
            "store_function_optimization": self._tool_store_function,
        }
        handler = dispatch.get(tool_name)
        if handler is None:
            return json.dumps({"error": f"Unknown trace knowledge tool: {tool_name}"})
        return handler(params)

    def _tool_lookup(self, params: Dict) -> str:
        query = params.get("query", "")
        max_results = int(params.get("max_results", 5))
        repo_name = params.get("repo_name")
        results = self.query_knowledge(query, max_results=max_results, repo_name=repo_name)
        return json.dumps(results, indent=2)

    def _tool_store(self, params: Dict) -> str:
        entity_key = params.get("entity_key", "")
        summary = params.get("summary", "")
        if not entity_key or not summary:
            return json.dumps({"error": "entity_key and summary are required"})
        related_context = params.get("related_context", "")
        confidence = float(params.get("confidence", 0.8))
        repo_name = params.get("repo_name", "")
        rowid = self.store_learning(
            entity_key=entity_key,
            summary=summary,
            related_context=related_context,
            confidence=confidence,
            repo_name=repo_name,
        )
        return json.dumps({"stored": True, "id": rowid})

    def _tool_lookup_function(self, params: Dict) -> str:
        function_name = params.get("function_name", "")
        if not function_name:
            return json.dumps({"error": "function_name is required"})
        file_path = params.get("file_path")
        max_results = int(params.get("max_results", 10))
        repo_name = params.get("repo_name")
        results = self.lookup_function_optimization(
            function_name=function_name,
            file_path=file_path,
            max_results=max_results,
            repo_name=repo_name,
        )
        return json.dumps(results, indent=2)

    def _tool_store_function(self, params: Dict) -> str:
        file_path = params.get("file_path", "")
        function_name = params.get("function_name", "")
        summary = params.get("summary", "")
        if not file_path or not function_name or not summary:
            return json.dumps({"error": "file_path, function_name, and summary are required"})
        details = params.get("details", "")
        severity = params.get("severity", "")
        confidence = float(params.get("confidence", 0.8))
        repo_name = params.get("repo_name", "")
        rowid = self.store_function_optimization(
            file_path=file_path,
            function_name=function_name,
            summary=summary,
            details=details,
            severity=severity,
            confidence=confidence,
            repo_name=repo_name,
        )
        return json.dumps({"stored": True, "id": rowid})

    def get_tool_descriptions(self) -> str:
        """Return formatted tool descriptions for inclusion in LLM prompts."""
        return """Available Trace Knowledge tools:

1. lookup_knowledge: Search past callstack-level learnings by keyword.
   ```json {"tool": "lookup_knowledge", "query": "<keywords>", "max_results": 5}```

2. store_learning: Persist a callstack-level learning for future recall.
   ```json {"tool": "store_learning", "entity_key": "<pattern name>", "summary": "<what was learned>", "related_context": "<optional>", "confidence": 0.8}```

3. lookup_function_optimization: Look up cached function-level optimization findings. Uses loose/fuzzy matching on function_name (partial names work).
   ```json {"tool": "lookup_function_optimization", "function_name": "<function name or substring>", "file_path": "<optional path substring>", "max_results": 10}```

4. store_function_optimization: Cache a function-level optimization finding for future traces that hit the same function.
   ```json {"tool": "store_function_optimization", "file_path": "<relative/path/to/file>", "function_name": "<exact function name>", "summary": "<optimization finding>", "details": "<full explanation>", "severity": "high", "confidence": 0.9}```
"""
