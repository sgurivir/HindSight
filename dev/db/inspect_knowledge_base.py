#!/usr/bin/env python3
"""
Inspect the Hindsight knowledge base SQLite DB and print a human-readable summary.

Usage:
    python inspect_knowledge_base.py <db_path>
    python inspect_knowledge_base.py <db_path> --stage context
    python inspect_knowledge_base.py <db_path> --search fetchData
    python inspect_knowledge_base.py <db_path> --stage analysis --search network
    python inspect_knowledge_base.py <db_path> --count
"""

import argparse
import datetime
import os
import sqlite3
import sys


def format_timestamp(ts: float) -> str:
    return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


def print_entry(row: sqlite3.Row, index: int) -> None:
    analyzed = format_timestamp(row["analyzed_at"]) if row["analyzed_at"] else "unknown"
    conf = f"{row['confidence']:.2f}" if row["confidence"] is not None else "n/a"

    print(f"[{index}] {row['function_name']}  @  {row['file_name']}")
    print(f"     Stage      : {row['stage']}")
    print(f"     Confidence : {conf}")
    print(f"     Analyzed   : {analyzed}")
    print(f"     Summary    : {row['summary']}")
    if row["related_context"]:
        # Truncate very long related_context to keep output readable
        rc = row["related_context"]
        if len(rc) > 300:
            rc = rc[:300] + "  [... truncated]"
        print(f"     Context    : {rc}")
    print()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Print human-readable entries from a Hindsight knowledge base SQLite DB."
    )
    parser.add_argument("db_path", help="Path to the SQLite knowledge base file.")
    parser.add_argument(
        "--stage",
        metavar="STAGE",
        help="Filter by pipeline stage (e.g. context, analysis, diff_analysis).",
    )
    parser.add_argument(
        "--search",
        metavar="TERM",
        help="Case-insensitive substring filter applied to function_name and summary.",
    )
    parser.add_argument(
        "--count",
        action="store_true",
        help="Only print the total number of matching entries.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        metavar="N",
        help="Limit output to the N most-recently analyzed entries (0 = no limit).",
    )
    args = parser.parse_args()

    db_path = os.path.expanduser(args.db_path)
    if not os.path.exists(db_path):
        print(f"Error: DB file not found: {db_path}", file=sys.stderr)
        return 1

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # Build query
    conditions = []
    params = []

    if args.stage:
        conditions.append("stage = ?")
        params.append(args.stage)

    if args.search:
        like = f"%{args.search}%"
        conditions.append("(function_name LIKE ? OR summary LIKE ?)")
        params.extend([like, like])

    where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    order_clause = "ORDER BY analyzed_at DESC"
    limit_clause = f"LIMIT {args.limit}" if args.limit > 0 else ""

    query = f"""
        SELECT function_name, file_name, summary, related_context,
               confidence, stage, analyzed_at
          FROM function_knowledge
         {where_clause}
         {order_clause}
         {limit_clause}
    """

    try:
        rows = conn.execute(query, params).fetchall()
    except sqlite3.OperationalError as exc:
        print(f"Error querying DB: {exc}", file=sys.stderr)
        conn.close()
        return 1

    conn.close()

    if args.count:
        print(f"Total entries: {len(rows)}")
        return 0

    if not rows:
        print("No entries found.")
        return 0

    # Print header
    filter_desc_parts = []
    if args.stage:
        filter_desc_parts.append(f"stage={args.stage!r}")
    if args.search:
        filter_desc_parts.append(f"search={args.search!r}")
    filter_desc = f"  (filters: {', '.join(filter_desc_parts)})" if filter_desc_parts else ""
    print(f"Knowledge base: {db_path}")
    print(f"Entries: {len(rows)}{filter_desc}")
    print("=" * 72)
    print()

    for idx, row in enumerate(rows, start=1):
        print_entry(row, idx)

    return 0


if __name__ == "__main__":
    sys.exit(main())
