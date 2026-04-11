# analytics_metrics.py
from __future__ import annotations
import argparse
import os
import sqlite3
import sys
import threading
import traceback
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any


@dataclass
class SessionInfo:
    """Represents a session from the sessions table"""
    session_id: str
    user_name: str
    repo: str
    repo_dir: str
    start_date: str
    auth_token: str


@dataclass
class TokenUsage:
    """Represents token usage from the token_usage table"""
    session_id: str
    timestamp_start: str
    timestamp_end: str
    tokens_used: int
    retry_errors: int
    cost_usd: float


@dataclass
class FunctionAnalysis:
    """Represents function analysis from the function_analysis table"""
    session_id: str
    timestamp_start: str
    functions_analyzed: int
    result: str  # 'pass' or 'fail'


class AnalyticsMetrics:
    """
    Redesigned AnalyticsMetrics with three separate tables:
    1. sessions - user_name, repo, repo_dir, session_id, start_date, auth_token
    2. token_usage - session_id, timestamp_start, timestamp_end, tokens_used, retry_errors, cost_usd
    3. function_analysis - session_id, timestamp_start, functions_analyzed, result (pass/fail)

    Thread-safe implementation using threading.Lock for all database operations.
    """

    def __init__(self, db_path: str = "analytics.db"):
        self.db_path = db_path
        self._lock = threading.Lock()
        self.load_db()

    def load_db(self) -> None:
        """Create new DB automatically if none exists, with all three tables"""
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            try:
                with conn:
                    # Create sessions table
                    conn.execute("""
                        CREATE TABLE IF NOT EXISTS sessions (
                            session_id TEXT PRIMARY KEY,
                            user_name TEXT NOT NULL,
                            repo TEXT NOT NULL,
                            repo_dir TEXT NOT NULL,
                            start_date TEXT NOT NULL,
                            auth_token TEXT NOT NULL
                        )
                    """)

                    # Create token_usage table
                    conn.execute("""
                        CREATE TABLE IF NOT EXISTS token_usage (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            session_id TEXT NOT NULL,
                            timestamp_start TEXT NOT NULL,
                            timestamp_end TEXT NOT NULL,
                            tokens_used INTEGER NOT NULL,
                            retry_errors INTEGER NOT NULL DEFAULT 0,
                            cost_usd REAL NOT NULL DEFAULT 0.0,
                            FOREIGN KEY (session_id) REFERENCES sessions (session_id)
                        )
                    """)

                    # Create function_analysis table
                    conn.execute("""
                        CREATE TABLE IF NOT EXISTS function_analysis (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            session_id TEXT NOT NULL,
                            timestamp_start TEXT NOT NULL,
                            functions_analyzed INTEGER NOT NULL,
                            result TEXT NOT NULL CHECK (result IN ('pass', 'fail')),
                            FOREIGN KEY (session_id) REFERENCES sessions (session_id)
                        )
                    """)

                    # Create indexes for better performance
                    conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_name)")
                    conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_repo ON sessions(repo)")
                    conn.execute("CREATE INDEX IF NOT EXISTS idx_token_usage_session ON token_usage(session_id)")
                    conn.execute("CREATE INDEX IF NOT EXISTS idx_function_analysis_session ON function_analysis(session_id)")

            finally:
                conn.close()

    def create_session(self, user_name: str, repo: str, repo_dir: str, auth_token: str) -> str:
        """Create a new session and return the session_id"""
        with self._lock:
            session_id = str(uuid.uuid4())
            start_date = datetime.now(timezone.utc).isoformat(timespec="seconds")

            conn = sqlite3.connect(self.db_path)
            try:
                with conn:
                    conn.execute("""
                        INSERT INTO sessions (session_id, user_name, repo, repo_dir, start_date, auth_token)
                        VALUES (?, ?, ?, ?, ?, ?)
                    """, (session_id, user_name, repo, repo_dir, start_date, auth_token))
            finally:
                conn.close()

            return session_id

    def add_tokens_used(self, session_id: str, timestamp: Optional[str] = None,
                       token_count: int = 0, retry_errors: int = 0,
                       cost_usd: float = 0.0, duration_seconds: float = 0.0) -> None:
        """Add token usage record for a session"""
        with self._lock:
            if timestamp is None:
                timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")

            # Calculate end timestamp based on duration
            if duration_seconds > 0:
                start_time = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
                end_time = start_time.timestamp() + duration_seconds
                timestamp_end = datetime.fromtimestamp(end_time, timezone.utc).isoformat(timespec="seconds")
            else:
                timestamp_end = timestamp

            conn = sqlite3.connect(self.db_path)
            try:
                with conn:
                    conn.execute("""
                        INSERT INTO token_usage (session_id, timestamp_start, timestamp_end, tokens_used, retry_errors, cost_usd)
                        VALUES (?, ?, ?, ?, ?, ?)
                    """, (session_id, timestamp, timestamp_end, token_count, retry_errors, cost_usd))
            finally:
                conn.close()

    def add_function_analysis(self, session_id: str, functions_analyzed: int,
                            result: str, timestamp: Optional[str] = None) -> None:
        """Add function analysis record for a session"""
        with self._lock:
            if timestamp is None:
                timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")

            if result not in ['pass', 'fail']:
                raise ValueError("Result must be either 'pass' or 'fail'")

            conn = sqlite3.connect(self.db_path)
            try:
                with conn:
                    conn.execute("""
                        INSERT INTO function_analysis (session_id, timestamp_start, functions_analyzed, result)
                        VALUES (?, ?, ?, ?)
                    """, (session_id, timestamp, functions_analyzed, result))
            finally:
                conn.close()

    def dump_all_sessions(self) -> List[Dict[str, Any]]:
        """Return list of all sessions and their attributes without printing individual sessions"""
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            try:
                cur = conn.cursor()

                # Get all sessions with aggregated data
                sessions = cur.execute("""
                    SELECT
                        s.session_id,
                        s.user_name,
                        s.repo,
                        s.repo_dir,
                        s.start_date,
                        s.auth_token,
                        COALESCE(SUM(tu.tokens_used), 0) as total_tokens,
                        COALESCE(SUM(tu.cost_usd), 0.0) as total_cost,
                        COALESCE(SUM(tu.retry_errors), 0) as total_retries,
                        COALESCE(SUM(fa.functions_analyzed), 0) as total_functions,
                        COUNT(CASE WHEN fa.result = 'pass' THEN 1 END) as functions_passed,
                        COUNT(CASE WHEN fa.result = 'fail' THEN 1 END) as functions_failed
                    FROM sessions s
                    LEFT JOIN token_usage tu ON s.session_id = tu.session_id
                    LEFT JOIN function_analysis fa ON s.session_id = fa.session_id
                    GROUP BY s.session_id, s.user_name, s.repo, s.repo_dir, s.start_date, s.auth_token
                    ORDER BY s.start_date DESC
                """).fetchall()

                session_list = []
                for session in sessions:
                    session_data = {
                        "session_id": session["session_id"],
                        "user_name": session["user_name"],
                        "repo": session["repo"],
                        "repo_dir": session["repo_dir"],
                        "start_date": session["start_date"],
                        "auth_token": session["auth_token"][:10] + "..." if session["auth_token"] else "None",  # Truncate for security
                        "total_tokens": session["total_tokens"],
                        "total_cost_usd": round(session["total_cost"], 4),
                        "total_retries": session["total_retries"],
                        "total_functions": session["total_functions"],
                        "functions_passed": session["functions_passed"],
                        "functions_failed": session["functions_failed"]
                    }
                    session_list.append(session_data)

                return session_list
            finally:
                conn.close()

    def dump_token_usage(self) -> Dict[str, Any]:
        """Print and return cumulative summary of token usage"""
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            try:
                cur = conn.cursor()

                # Get overall token usage summary
                summary = cur.execute("""
                    SELECT
                        COUNT(*) as total_usage_records,
                        SUM(tokens_used) as total_tokens,
                        SUM(cost_usd) as total_cost,
                        SUM(retry_errors) as total_retries,
                        AVG(tokens_used) as avg_tokens_per_record,
                        AVG(cost_usd) as avg_cost_per_record
                    FROM token_usage
                """).fetchone()

                # Get usage by user
                by_user = cur.execute("""
                    SELECT
                        s.user_name,
                        COUNT(tu.id) as usage_records,
                        SUM(tu.tokens_used) as total_tokens,
                        SUM(tu.cost_usd) as total_cost,
                        SUM(tu.retry_errors) as total_retries
                    FROM sessions s
                    LEFT JOIN token_usage tu ON s.session_id = tu.session_id
                    WHERE tu.id IS NOT NULL
                    GROUP BY s.user_name
                    ORDER BY total_cost DESC
                """).fetchall()

                # Get usage by repo
                by_repo = cur.execute("""
                    SELECT
                        s.repo,
                        COUNT(tu.id) as usage_records,
                        SUM(tu.tokens_used) as total_tokens,
                        SUM(tu.cost_usd) as total_cost,
                        SUM(tu.retry_errors) as total_retries
                    FROM sessions s
                    LEFT JOIN token_usage tu ON s.session_id = tu.session_id
                    WHERE tu.id IS NOT NULL
                    GROUP BY s.repo
                    ORDER BY total_cost DESC
                """).fetchall()

                result = {
                    "total_usage_records": summary["total_usage_records"] or 0,
                    "total_tokens": summary["total_tokens"] or 0,
                    "total_cost_usd": round(summary["total_cost"] or 0.0, 4),
                    "total_retries": summary["total_retries"] or 0,
                    "avg_tokens_per_record": round(summary["avg_tokens_per_record"] or 0.0, 2),
                    "avg_cost_per_record": round(summary["avg_cost_per_record"] or 0.0, 4),
                    "by_user": [
                        {
                            "user_name": row["user_name"],
                            "usage_records": row["usage_records"],
                            "total_tokens": row["total_tokens"],
                            "total_cost_usd": round(row["total_cost"], 4),
                            "total_retries": row["total_retries"]
                        }
                        for row in by_user
                    ],
                    "by_repo": [
                        {
                            "repo": row["repo"],
                            "usage_records": row["usage_records"],
                            "total_tokens": row["total_tokens"],
                            "total_cost_usd": round(row["total_cost"], 4),
                            "total_retries": row["total_retries"]
                        }
                        for row in by_repo
                    ]
                }

                # Print summary
                print("=== Token Usage Summary ===")
                print(f"Total Usage Records: {result['total_usage_records']}")
                print(f"Total Tokens: {result['total_tokens']:,}")
                print(f"Total Cost: ${result['total_cost_usd']}")
                print(f"Total Retries: {result['total_retries']}")
                print(f"Avg Tokens/Record: {result['avg_tokens_per_record']}")
                print(f"Avg Cost/Record: ${result['avg_cost_per_record']}")

                if result['by_user']:
                    print("\n— By User —")
                    for user in result['by_user']:
                        print(f"  {user['user_name']}: {user['total_tokens']:,} tokens, ${user['total_cost_usd']}")

                if result['by_repo']:
                    print("\n— By Repository —")
                    for repo in result['by_repo']:
                        print(f"  {repo['repo']}: {repo['total_tokens']:,} tokens, ${repo['total_cost_usd']}")

                return result
            finally:
                conn.close()

    def get_session_info(self, session_id: str) -> Optional[SessionInfo]:
        """Get session information by session_id"""
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            try:
                cur = conn.cursor()
                row = cur.execute("""
                    SELECT session_id, user_name, repo, repo_dir, start_date, auth_token
                    FROM sessions
                    WHERE session_id = ?
                """, (session_id,)).fetchone()

                if row:
                    return SessionInfo(
                        session_id=row["session_id"],
                        user_name=row["user_name"],
                        repo=row["repo"],
                        repo_dir=row["repo_dir"],
                        start_date=row["start_date"],
                        auth_token=row["auth_token"]
                    )
                return None
            finally:
                conn.close()


def print_analytics_summary(db_path: str) -> None:
    """
    Print comprehensive analytics summary from a database file.

    Args:
        db_path: Path to the analytics database file
    """
    if not os.path.exists(db_path):
        print(f"Error: Database file not found: {db_path}")
        return

    print("="*80)
    print(f"ANALYTICS SUMMARY FOR: {db_path}")
    print("="*80)

    try:
        # Create AnalyticsMetrics instance with the provided database
        metrics = AnalyticsMetrics(db_path)

        # Print sessions summary
        print("\n" + "="*60)
        print("SESSIONS AND TOKEN USAGE BY SESSION")
        print("="*60)
        sessions = metrics.dump_all_sessions()

        if not sessions:
            print("No sessions found in the database.")
        else:
            print(f"\nFound {len(sessions)} session(s) in total.")

        # Print token usage summary
        print("\n" + "="*60)
        print("OVERALL TOKEN USAGE SUMMARY")
        print("="*60)
        token_summary = metrics.dump_token_usage()

        # Print summary by repository and session
        print("\n" + "="*60)
        print("SUMMARY BY REPOSITORY AND SESSION")
        print("="*60)

        if sessions:
            # Group sessions by repository
            repos = {}
            for session in sessions:
                repo_name = session['repo']
                if repo_name not in repos:
                    repos[repo_name] = []
                repos[repo_name].append(session)

            for repo_name, repo_sessions in repos.items():
                print(f"\n📁 Repository: {repo_name}")
                print("-" * 50)

                total_repo_tokens = sum(s['total_tokens'] for s in repo_sessions)
                total_repo_cost = sum(s['total_cost_usd'] for s in repo_sessions)
                total_repo_functions = sum(s['total_functions'] for s in repo_sessions)
                total_repo_passed = sum(s['functions_passed'] for s in repo_sessions)
                total_repo_failed = sum(s['functions_failed'] for s in repo_sessions)

                print(f"  Total Sessions: {len(repo_sessions)}")
                print(f"  Total Tokens: {total_repo_tokens:,}")
                print(f"  Total Cost: ${total_repo_cost:.4f}")
                print(f"  Total Functions: {total_repo_functions} (Pass: {total_repo_passed}, Fail: {total_repo_failed})")

                print(f"\n  Sessions:")
                for i, session in enumerate(sorted(repo_sessions, key=lambda x: x['start_date']), 1):
                    print(f"    {i}. {session['start_date']} - {session['session_id'][:8]}...")
                    print(f"       User: {session['user_name']}")
                    print(f"       Tokens: {session['total_tokens']:,}, Cost: ${session['total_cost_usd']:.4f}")
                    print(f"       Functions: {session['total_functions']} (Pass: {session['functions_passed']}, Fail: {session['functions_failed']})")
                    if session['total_retries'] > 0:
                        print(f"       Retries: {session['total_retries']}")
                    print()

        print("="*80)
        print("ANALYTICS SUMMARY COMPLETE")
        print("="*80)

    except Exception as e:
        print(f"Error reading analytics database: {e}")
        traceback.print_exc()


def main():
    """
    Main function to parse arguments and print analytics summary.
    """
    parser = argparse.ArgumentParser(
        description="Analytics Metrics Summary Tool - Display analytics data from Hindsight database",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python analytics_metrics.py ~/hindsight_analytics/my_repo/analytics.db
  python analytics_metrics.py /path/to/custom/analytics.db
        """
    )

    parser.add_argument(
        "db_path",
        help="Path to the analytics database file"
    )

    args = parser.parse_args()

    try:
        print_analytics_summary(args.db_path)
    except KeyboardInterrupt:
        print("\nOperation cancelled by user.")
        sys.exit(1)
    except Exception as e:
        print(f"Unexpected error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()