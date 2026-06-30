"""Async wrapper around the synchronous publisher/subscriber result store.

Key properties for FastAPI streaming:

1. Write-through. `publish()` calls the underlying publisher (which writes
   one JSON file per function) immediately. If the run crashes mid-way, the
   results that landed before the crash are already on disk and visible to
   the existing report generator.
2. Fault-tolerant input. Every issue dict passes through `_sanitize_issue`
   so an LLM that omits 'severity' or returns 'description' instead of
   'issue' can't poison the result store.
3. Soft-fail on backend errors. If `add_result` itself raises (full disk,
   permission error), `publish()` returns an error string rather than
   propagating — the caller logs and continues.

The sync publisher's locking is replaced with an `asyncio.Lock` here. The
underlying `_publisher.add_result` runs in a worker thread via
`asyncio.to_thread`.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Protocol

from hindsight.utils.log_util import get_logger

logger = get_logger(__name__)


# Minimal protocol so we can plug in different publishers (code / diff /
# trace) without depending on their concrete classes. The legacy publishers
# all expose these two methods with the same shape.
class ResultPublisher(Protocol):
    def add_result(
        self,
        repo_name: str,
        file_path: str,
        function: str,
        function_checksum: str,
        results: List[Dict[str, Any]],
    ) -> str:                                                       # noqa: D401, E501
        ...

    def check_existing_result(
        self,
        file_name: str,
        function_name: str,
        checksum: str,
    ) -> Optional[Dict[str, Any]]:
        ...


@dataclass
class PublishOutcome:
    """Result of a `publish()` call.

    Errors are returned (not raised) so worker code can log and proceed to the
    next function without crashing the run.
    """

    ok: bool
    issue_count: int
    error: Optional[str] = None


class AsyncResultSink:
    """Async front-end for a sync `ResultPublisher`.

    One instance per session. All writes are write-through: the moment a
    function's results land here, they are persisted to disk by the
    underlying publisher. This is what makes partial-run recovery work.
    """

    def __init__(self, publisher: ResultPublisher, *, repo_name: str):
        self._publisher = publisher
        self._repo_name = repo_name
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Cache lookup
    # ------------------------------------------------------------------

    async def check_existing(
        self,
        *,
        file_path: str,
        function: str,
        checksum: str,
    ) -> Optional[Dict[str, Any]]:
        """Look up a previously-published result with the same checksum.

        A cache hit means we can skip the LLM call entirely. Cache failures
        (publisher raised) degrade to a miss — analysis just re-runs, which
        is correct (slow but not wrong).
        """
        try:
            return await asyncio.to_thread(
                self._publisher.check_existing_result,
                file_path,
                function,
                checksum,
            )
        except Exception as exc:  # noqa: BLE001 — cache lookup must never fail the run
            logger.warning(
                f"check_existing failed for {function} ({file_path}); treating as miss: {exc}"
            )
            return None

    # ------------------------------------------------------------------
    # Write-through publish
    # ------------------------------------------------------------------

    async def publish(
        self,
        *,
        file_path: str,
        function: str,
        checksum: str,
        issues: List[Dict[str, Any]],
    ) -> PublishOutcome:
        """Persist one function's results.

        Issues are sanitized before write so downstream consumers can rely on
        every issue having at least `issue`, `severity`, `category`. Empty
        result lists are still written — they record "this function was
        analyzed, no defects" so the cache works.
        """
        sanitized = [self._sanitize_issue(i) for i in issues if isinstance(i, dict)]
        async with self._lock:
            try:
                await asyncio.to_thread(
                    self._publisher.add_result,
                    repo_name=self._repo_name,
                    file_path=file_path,
                    function=function,
                    function_checksum=checksum,
                    results=sanitized,
                )
                return PublishOutcome(ok=True, issue_count=len(sanitized))
            except Exception as exc:  # noqa: BLE001 — never fail the run on backend error
                err = f"{type(exc).__name__}: {exc}"
                logger.error(f"publish failed for {function} ({file_path}): {err}")
                return PublishOutcome(ok=False, issue_count=len(sanitized), error=err)

    # ------------------------------------------------------------------
    # Sanitization — tolerate LLM imperfection
    # ------------------------------------------------------------------

    @staticmethod
    def _sanitize_issue(issue: Dict[str, Any]) -> Dict[str, Any]:
        """Provide defaults for fields the LLM commonly omits.

        Mirrors the legacy `_run_llm_analysis` post-processing in
        `code_analyzer.py` so the published shape is identical.
        """
        out = dict(issue)
        if "issue" not in out:
            out["issue"] = out.get("description", "No description provided")
        if "severity" not in out:
            out["severity"] = "medium"
        if "category" not in out:
            out["category"] = "general"
        return out
