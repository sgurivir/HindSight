#!/usr/bin/env python3
"""
Inspect the unified Hindsight knowledge base (KnowledgeStore) and print a
human-readable summary.

Schema is one `learnings` table with `subject` (`code` | `trace` | `diff`)
and `kind` (`summary` | `invariant` | `finding` | `optimization`)
discriminators. See `hindsight/core/knowledge/knowledge_store.py`.

Usage:
    inspect_knowledge_base.py <db_path>
    inspect_knowledge_base.py <db_path> --subject code
    inspect_knowledge_base.py <db_path> --kind finding
    inspect_knowledge_base.py <db_path> --function parseJSON
    inspect_knowledge_base.py <db_path> --file src/foo.swift
    inspect_knowledge_base.py <db_path> --search "lock ordering"
    inspect_knowledge_base.py <db_path> --count
    inspect_knowledge_base.py <db_path> --stats

`<db_path>` is `~/llm_artifacts/<repo>/knowledge.db` by default. Pass `~` and
the script will resolve it; if you pass a directory, it appends `knowledge.db`.
"""

import argparse
import json
import os
import sqlite3
import sys
from typing import List, Optional, Tuple


VALID_SUBJECTS = ("code", "trace", "diff")
VALID_KINDS = ("summary", "invariant", "finding", "optimization")


def _format_tags(raw: Optional[str]) -> str:
    if not raw:
        return ""
    try:
        tags = json.loads(raw)
    except json.JSONDecodeError:
        return raw
    if not isinstance(tags, list):
        return raw
    return ", ".join(str(t) for t in tags)


def _print_entry(row: sqlite3.Row, index: int) -> None:
    conf = f"{row['confidence']:.2f}" if row["confidence"] is not None else "n/a"
    entity = row["entity_key"]
    func = row["function_name"] or "-"
    file_path = row["file_path"] or "-"
    checksum = row["checksum"] or "-"
    severity = f"  severity={row['severity']}" if row["severity"] else ""
    tags = _format_tags(row["tags"])
    tags_line = f"     Tags       : {tags}\n" if tags else ""

    print(f"[{index}] {row['subject']}/{row['kind']}  •  {entity}{severity}")
    print(f"     Function   : {func}")
    print(f"     File       : {file_path}")
    print(f"     Checksum   : {checksum}")
    print(f"     Confidence : {conf}")
    print(f"     Created    : {row['created_at']}")
    print(f"     Updated    : {row['updated_at']}")
    print(f"     Summary    : {row['summary']}")
    if row["details"]:
        details = row["details"]
        if len(details) > 400:
            details = details[:400] + "  [... truncated]"
        print(f"     Details    : {details}")
    print(tags_line, end="")
    print()


def _resolve_db_path(raw: str) -> str:
    path = os.path.expanduser(raw)
    if os.path.isdir(path):
        path = os.path.join(path, "knowledge.db")
    return path


def _build_query(
    subject: Optional[str],
    kind: Optional[str],
    function: Optional[str],
    file_path: Optional[str],
    repo: Optional[str],
    search: Optional[str],
    limit: int,
) -> Tuple[str, List]:
    conditions: List[str] = []
    params: List = []

    if subject:
        conditions.append("l.subject = ?")
        params.append(subject)
    if kind:
        conditions.append("l.kind = ?")
        params.append(kind)
    if function:
        conditions.append("l.function_name = ?")
        params.append(function)
    if file_path:
        conditions.append("l.file_path = ?")
        params.append(file_path)
    if repo:
        conditions.append("l.repo_name = ?")
        params.append(repo)

    if search:
        # FTS5 ranked search across summary + details.
        conditions.append("l.id IN (SELECT rowid FROM learnings_fts WHERE learnings_fts MATCH ?)")
        # Quote each token so phrases with hyphens/colons don't trip FTS5.
        tokens = [t for t in search.split() if t]
        params.append(" OR ".join(f'"{t}"' for t in tokens) if tokens else search)

    where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    order_clause = "ORDER BY l.updated_at DESC"
    limit_clause = f"LIMIT {limit}" if limit > 0 else ""

    query = f"""
        SELECT l.id, l.subject, l.repo_name, l.kind, l.entity_key,
               l.file_path, l.function_name, l.checksum,
               l.summary, l.details, l.tags, l.severity, l.confidence,
               l.created_at, l.updated_at
          FROM learnings l
         {where_clause}
         {order_clause}
         {limit_clause}
    """
    return query, params


def _print_stats(conn: sqlite3.Connection) -> None:
    total = conn.execute("SELECT COUNT(*) AS n FROM learnings").fetchone()["n"]
    print(f"Total rows: {total}")
    if total == 0:
        return

    print("\nBy subject:")
    for r in conn.execute(
        "SELECT subject, COUNT(*) AS n FROM learnings GROUP BY subject ORDER BY n DESC"
    ):
        print(f"  {r['subject']:<8} {r['n']}")

    print("\nBy kind:")
    for r in conn.execute(
        "SELECT kind, COUNT(*) AS n FROM learnings GROUP BY kind ORDER BY n DESC"
    ):
        print(f"  {r['kind']:<14} {r['n']}")

    print("\nBy (subject, kind):")
    for r in conn.execute(
        """SELECT subject, kind, COUNT(*) AS n FROM learnings
           GROUP BY subject, kind ORDER BY subject, kind"""
    ):
        print(f"  {r['subject']:<8} {r['kind']:<14} {r['n']}")

    print("\nBy repo_name:")
    for r in conn.execute(
        "SELECT repo_name, COUNT(*) AS n FROM learnings GROUP BY repo_name ORDER BY n DESC"
    ):
        repo = r["repo_name"] or "(empty)"
        print(f"  {repo:<32} {r['n']}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Inspect the unified Hindsight KnowledgeStore SQLite DB."
    )
    parser.add_argument(
        "db_path",
        help="Path to knowledge.db (or the directory containing it).",
    )
    parser.add_argument(
        "--subject",
        choices=VALID_SUBJECTS,
        help="Filter by subject (code | trace | diff).",
    )
    parser.add_argument(
        "--kind",
        choices=VALID_KINDS,
        help="Filter by kind (summary | invariant | finding | optimization).",
    )
    parser.add_argument(
        "--function",
        metavar="NAME",
        help="Exact-match filter on function_name.",
    )
    parser.add_argument(
        "--file",
        metavar="PATH",
        dest="file_path",
        help="Exact-match filter on file_path.",
    )
    parser.add_argument(
        "--repo",
        metavar="NAME",
        help="Filter by repo_name (useful when the DB was shared across repos).",
    )
    parser.add_argument(
        "--search",
        metavar="TERM",
        help="FTS5 search across summary + details (tokens OR'd).",
    )
    parser.add_argument(
        "--count",
        action="store_true",
        help="Only print the total number of matching entries.",
    )
    parser.add_argument(
        "--stats",
        action="store_true",
        help="Print aggregate counts by subject, kind, and repo. Ignores filters.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        metavar="N",
        help="Limit output to the N most-recently updated entries (0 = no limit).",
    )

    args = parser.parse_args()

    db_path = _resolve_db_path(args.db_path)
    if not os.path.exists(db_path):
        print(f"Error: DB file not found: {db_path}", file=sys.stderr)
        return 1

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    try:
        if args.stats:
            print(f"Knowledge base: {db_path}\n")
            _print_stats(conn)
            return 0

        query, params = _build_query(
            subject=args.subject,
            kind=args.kind,
            function=args.function,
            file_path=args.file_path,
            repo=args.repo,
            search=args.search,
            limit=args.limit,
        )

        try:
            rows = conn.execute(query, params).fetchall()
        except sqlite3.OperationalError as exc:
            print(f"Error querying DB: {exc}", file=sys.stderr)
            return 1

        if args.count:
            print(f"Total matching entries: {len(rows)}")
            return 0

        if not rows:
            print("No entries found.")
            return 0

        filter_parts = []
        for label, value in (
            ("subject", args.subject), ("kind", args.kind),
            ("function", args.function), ("file", args.file_path),
            ("repo", args.repo), ("search", args.search),
        ):
            if value:
                filter_parts.append(f"{label}={value!r}")
        filter_desc = f"  (filters: {', '.join(filter_parts)})" if filter_parts else ""

        print(f"Knowledge base: {db_path}")
        print(f"Entries: {len(rows)}{filter_desc}")
        print("=" * 72)
        print()

        for idx, row in enumerate(rows, start=1):
            _print_entry(row, idx)
    finally:
        conn.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
