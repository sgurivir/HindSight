#!/usr/bin/env python3
# Created by Sridhar Gurivireddy
"""
Analytics helper class for AnalysisRunner to track LLM usage and analysis results.
This class manages analytics tracking with a singleton pattern to ensure only one instance
exists per program run.
"""

import os
import hashlib
import getpass
from datetime import datetime, timezone
from typing import Optional, Dict, Any
from pathlib import Path

from ..accounting.analytics_metrics import AnalyticsMetrics
from ..utils.log_util import get_logger


class AnalyticsHelper:
    """
    Helper class for AnalysisRunner to manage analytics tracking.

    This class follows the singleton pattern to ensure only one instance exists
    per program run. It manages:
    1. Session creation and tracking
    2. Token usage recording
    3. Analysis results recording

    Tables managed:
    - sessions: user_name, repo, repo_dir, session_id, start_date, auth_token
    - token_usage: session_id, timestamp_start, timestamp_end, tokens_used, retry_errors, cost_usd
    - function_analysis: session_id, timestamp_start, functions_analyzed, result
    """

    _instance: Optional['AnalyticsHelper'] = None
    _initialized: bool = False

    def __new__(cls, *args, **kwargs):
        """Ensure singleton pattern - only one instance per program run."""
        if cls._instance is None:
            cls._instance = super(AnalyticsHelper, cls).__new__(cls)
        return cls._instance

    def __init__(self, repo_path: str = None, db_path: str = None):
        """
        Initialize the analytics helper.

        Args:
            repo_path: Path to the repository (used to determine database location)
            db_path: Optional explicit path to the SQLite database file
        """
        # Prevent re-initialization of singleton
        if self._initialized:
            return

        self.logger = get_logger(__name__)

        # Determine database path
        if db_path:
            self.db_path = db_path
        elif repo_path:
            self.db_path = self._get_database_path(repo_path)
        else:
            # Fallback to current directory
            self.db_path = "analytics.db"

        # Ensure database directory exists
        db_dir = os.path.dirname(self.db_path)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)

        self.analytics_metrics = AnalyticsMetrics(self.db_path)
        self.session_id: Optional[str] = None
        self.current_function_start_time: Optional[str] = None
        self.current_function_hash: Optional[str] = None

        # Mark as initialized
        AnalyticsHelper._initialized = True

        self.logger.info(f"AnalyticsHelper singleton initialized with database: {self.db_path}")

    @classmethod
    def get_instance(cls, repo_path: str = None, db_path: str = None) -> 'AnalyticsHelper':
        """
        Get the singleton instance of AnalyticsHelper.

        Args:
            repo_path: Path to the repository (used to determine database location)
            db_path: Optional explicit path to the SQLite database file

        Returns:
            AnalyticsHelper: The singleton instance
        """
        if cls._instance is None:
            cls._instance = AnalyticsHelper(repo_path, db_path)
        return cls._instance

    def _get_database_path(self, repo_path: str) -> str:
        """
        Get the database path based on repository path.
        Database will be located at ~/hindsight_analytics/<repo_name>/analytics.db

        Args:
            repo_path: Path to the repository

        Returns:
            str: Path to the database file
        """
        repo_name = os.path.basename(os.path.abspath(repo_path))
        analytics_dir = os.path.expanduser(f"~/hindsight_analytics/{repo_name}")
        return os.path.join(analytics_dir, "analytics.db")

    def _generate_function_hash(self, function_data: str) -> str:
        """
        Generate a hash for function or callstack data.

        Args:
            function_data: Function name, callstack, or other identifying data

        Returns:
            str: SHA-256 hash of the function data
        """
        return hashlib.sha256(function_data.encode('utf-8')).hexdigest()[:16]

    def start_session(self, repo_path: str, auth_token: str = "dummy_auth_token_v1.0") -> str:
        """
        Start a new analytics session for the program run.

        Args:
            repo_path: Path to the repository being analyzed
            auth_token: Authentication token (dummy for now)

        Returns:
            str: The created session ID
        """
        if self.session_id is not None:
            self.logger.warning(f"Session already exists: {self.session_id}")
            return self.session_id

        try:
            # Get user information
            user_name = getpass.getuser()

            # Extract repo name and directory
            repo_path_obj = Path(repo_path).resolve()
            repo_name = repo_path_obj.name
            repo_dir = str(repo_path_obj)

            # Create session
            self.session_id = self.analytics_metrics.create_session(
                user_name=user_name,
                repo=repo_name,
                repo_dir=repo_dir,
                auth_token=auth_token
            )

            self.logger.info(f"Analytics session started: {self.session_id}")
            self.logger.info(f"User: {user_name}, Repo: {repo_name}")
            self.logger.info(f"Repo Directory: {repo_dir}")

            return self.session_id

        except Exception as e:
            self.logger.error(f"Failed to start analytics session: {e}")
            raise

    def record_token_usage(self, tokens_used: int, retry_errors: int = 0,
                          cost_usd: float = 0.0, duration_seconds: float = 0.0,
                          timestamp_start: Optional[str] = None) -> None:
        """
        Record token usage for the current session.

        Args:
            tokens_used: Number of tokens consumed
            retry_errors: Number of retry errors encountered
            cost_usd: Cost in USD for the token usage
            duration_seconds: Duration of the operation in seconds
            timestamp_start: Start timestamp (uses current time if None)
        """
        if self.session_id is None:
            self.logger.error("No active session. Call start_session() first.")
            return

        try:
            self.analytics_metrics.add_tokens_used(
                session_id=self.session_id,
                timestamp=timestamp_start,
                token_count=tokens_used,
                retry_errors=retry_errors,
                cost_usd=cost_usd,
                duration_seconds=duration_seconds
            )

            self.logger.debug(f"Recorded token usage: {tokens_used} tokens, "
                            f"{retry_errors} retries, ${cost_usd:.4f}")

        except Exception as e:
            self.logger.error(f"Failed to record token usage: {e}")

    def start_function_analysis(self, function_data: str = None) -> None:
        """
        Mark the start of a function analysis operation.
        This records the timestamp and function hash for later use in recording the analysis result.

        Args:
            function_data: Function name, callstack, or other identifying data to hash
        """
        self.current_function_start_time = datetime.now(timezone.utc).isoformat(timespec="seconds")

        if function_data:
            self.current_function_hash = self._generate_function_hash(function_data)
            self.logger.debug(f"Function analysis started for hash: {self.current_function_hash}")
        else:
            self.current_function_hash = None
            self.logger.debug("Function analysis started")

    def record_function_analysis_result(self, functions_analyzed: int,
                                      success: bool, function_data: str = None) -> None:
        """
        Record the result of function analysis.

        Args:
            functions_analyzed: Number of functions that were analyzed
            success: Whether the analysis was successful (True for pass, False for fail)
            function_data: Optional function name, callstack, or other identifying data
        """
        if self.session_id is None:
            self.logger.error("No active session. Call start_session() first.")
            return

        try:
            result = "pass" if success else "fail"
            timestamp = self.current_function_start_time or datetime.now(timezone.utc).isoformat(timespec="seconds")

            # Use provided function_data or current hash
            function_hash = None
            if function_data:
                function_hash = self._generate_function_hash(function_data)
            elif self.current_function_hash:
                function_hash = self.current_function_hash

            self.analytics_metrics.add_function_analysis(
                session_id=self.session_id,
                functions_analyzed=functions_analyzed,
                result=result,
                timestamp=timestamp
            )

            if function_hash:
                self.logger.debug(f"Recorded function analysis: {functions_analyzed} functions, "
                                f"result: {result}, hash: {function_hash}")
            else:
                self.logger.debug(f"Recorded function analysis: {functions_analyzed} functions, "
                                f"result: {result}")

            # Reset the start time and hash
            self.current_function_start_time = None
            self.current_function_hash = None

        except Exception as e:
            self.logger.error(f"Failed to record function analysis result: {e}")

    def get_session_id(self) -> Optional[str]:
        """
        Get the current session ID.

        Returns:
            Optional[str]: The current session ID, or None if no session is active
        """
        return self.session_id

    def get_session_summary(self) -> Optional[Dict[str, Any]]:
        """
        Get a summary of the current session's analytics data.

        Returns:
            Optional[Dict[str, Any]]: Session summary data, or None if no session is active
        """
        if self.session_id is None:
            self.logger.warning("No active session to summarize")
            return None

        try:
            # Get session info
            session_info = self.analytics_metrics.get_session_info(self.session_id)
            if not session_info:
                self.logger.error(f"Session {self.session_id} not found in database")
                return None

            # Get all sessions to find our current one in the summary
            all_sessions = self.analytics_metrics.dump_all_sessions()
            current_session_data = None

            for session in all_sessions:
                if session['session_id'] == self.session_id:
                    current_session_data = session
                    break

            if current_session_data:
                return {
                    'session_id': current_session_data['session_id'],
                    'user_name': current_session_data['user_name'],
                    'repo': current_session_data['repo'],
                    'repo_dir': session_info.repo_dir,  # Full path from session_info
                    'start_date': current_session_data['start_date'],
                    'total_tokens': current_session_data['total_tokens'],
                    'total_cost_usd': current_session_data['total_cost_usd'],
                    'total_retries': current_session_data['total_retries'],
                    'total_functions': current_session_data['total_functions'],
                    'functions_passed': current_session_data['functions_passed'],
                    'functions_failed': current_session_data['functions_failed']
                }
            else:
                # Fallback to basic session info if aggregated data not available
                return {
                    'session_id': session_info.session_id,
                    'user_name': session_info.user_name,
                    'repo': session_info.repo,
                    'repo_dir': session_info.repo_dir,
                    'start_date': session_info.start_date,
                    'total_tokens': 0,
                    'total_cost_usd': 0.0,
                    'total_retries': 0,
                    'total_functions': 0,
                    'functions_passed': 0,
                    'functions_failed': 0
                }

        except Exception as e:
            self.logger.error(f"Failed to get session summary: {e}")
            return None

    def get_all_sessions_aggregate_stats(self) -> Optional[Dict[str, Any]]:
        """
        Get aggregate statistics for all sessions in the database.

        Returns:
            Optional[Dict[str, Any]]: Aggregate statistics, or None if error
        """
        try:
            all_sessions = self.analytics_metrics.dump_all_sessions()
            if not all_sessions:
                return None

            total_sessions = len(all_sessions)
            total_tokens = sum(session.get('total_tokens', 0) for session in all_sessions)
            total_cost = sum(session.get('total_cost_usd', 0.0) for session in all_sessions)

            # Find earliest date
            earliest_date = None
            for session in all_sessions:
                session_date = session.get('start_date')
                if session_date:
                    if earliest_date is None or session_date < earliest_date:
                        earliest_date = session_date

            return {
                'total_sessions': total_sessions,
                'total_tokens': total_tokens,
                'total_cost_usd': total_cost,
                'earliest_date': earliest_date
            }

        except Exception as e:
            self.logger.error(f"Failed to get aggregate statistics: {e}")
            return None

    def print_session_summary(self) -> None:
        """Print a summary of the current session to the console."""
        summary = self.get_session_summary()
        if not summary:
            self.logger.warning("No session summary available")
            return

        print("\n" + "="*60)
        print("CURRENT SESSION SUMMARY")
        print("="*60)
        print(f"Session ID: {summary['session_id']}")
        print(f"User: {summary['user_name']}")
        print(f"Repository: {summary['repo']}")
        print(f"Directory: {summary['repo_dir']}")
        print(f"Started: {summary['start_date']}")
        print(f"Total Tokens Used: {summary['total_tokens']:,}")
        print(f"Total Cost: ${summary['total_cost_usd']:.4f}")
        print(f"Total Retries: {summary['total_retries']}")
        print(f"Functions Analyzed: {summary['total_functions']}")
        print(f"  - Passed: {summary['functions_passed']}")
        print(f"  - Failed: {summary['functions_failed']}")
        print("="*60)

    def print_all_sessions_aggregate_stats(self) -> None:
        """Print aggregate statistics for all sessions."""
        stats = self.get_all_sessions_aggregate_stats()
        if not stats:
            self.logger.warning("No aggregate statistics available")
            return

        print("\n" + "="*60)
        print("ALL SESSIONS AGGREGATE STATISTICS")
        print("="*60)
        print(f"Total Number of Sessions: {stats['total_sessions']}")
        print(f"Total Tokens Used: {stats['total_tokens']:,}")
        print(f"Total Cost: ${stats['total_cost_usd']:.4f}")
        if stats['earliest_date']:
            print(f"Earliest Session Date: {stats['earliest_date']}")
        else:
            print("Earliest Session Date: N/A")
        print("="*60)

    def end_session(self) -> None:
        """
        End the current analytics session and print summary.
        This doesn't delete the session data, just marks the end of tracking.
        """
        if self.session_id is None:
            self.logger.warning("No active session to end")
            return

        self.logger.info(f"Ending analytics session: {self.session_id}")

        # Print current session summary first
        self.print_session_summary()

        # Then print aggregate statistics for all sessions
        self.print_all_sessions_aggregate_stats()

        # Reset session tracking
        self.session_id = None
        self.current_function_start_time = None

    @classmethod
    def reset_singleton(cls) -> None:
        """
        Reset the singleton instance.
        This is primarily for testing purposes.
        """
        cls._instance = None
        cls._initialized = False