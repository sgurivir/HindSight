"""Async wrapper around the existing `AffectedFunctionDetector`.

The detector is sync and CPU-bound (it walks the call graph BFS-style). We
just run it in a worker thread so it doesn't block the event loop while
other LLM calls progress in parallel.

This module is a deliberately thin façade — the detector logic itself is
out of scope per the rewrite plan (it's AST-adjacent code we shouldn't
touch).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from hindsight.diff_analyzers.affected_function_detector import (
    AffectedFunctionDetector,
    extract_changed_lines_per_file,
)
from hindsight.utils.log_util import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class AffectedFunction:
    """Typed view of one entry the detector returns.

    The detector returns plain dicts; this dataclass gives the pipeline a
    stable, typed handle. Unknown keys land in `extras` so we don't lose
    information when the detector evolves.
    """

    function: str
    file_path: str
    start: int
    end: int
    affected_reason: str
    changed_lines: List[int]
    extras: Dict[str, Any]

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "AffectedFunction":
        known_keys = {"function", "file", "file_path", "start", "end", "affected_reason", "changed_lines"}
        return cls(
            function=str(raw.get("function", "")),
            # Detector uses 'file'; some downstream consumers want 'file_path'.
            file_path=str(raw.get("file_path") or raw.get("file", "")),
            start=int(raw.get("start", 0) or 0),
            end=int(raw.get("end", 0) or 0),
            affected_reason=str(raw.get("affected_reason", "")),
            changed_lines=list(raw.get("changed_lines") or []),
            extras={k: v for k, v in raw.items() if k not in known_keys},
        )


@dataclass(frozen=True)
class AffectedFunctionSelection:
    """Result of `select_affected_functions`."""

    affected: List[AffectedFunction]
    changed_lines_per_file: Dict[str, Dict[str, List[int]]]


async def select_affected_functions(
    *,
    call_graph: List[Dict[str, Any]],
    functions: Dict[str, Any],
    diff_content: str,
    include_transitive: bool = True,
) -> AffectedFunctionSelection:
    """Run the detector on a worker thread and return typed results.

    Args:
        call_graph: Loaded merged_call_graph.json.
        functions:  Loaded merged_functions.json.
        diff_content: Raw unified-diff text from `git diff`.
        include_transitive: Pass through to the detector. Legacy behavior is
            True (one BFS level of callers/callees beyond direct edits).

    Returns:
        Typed selection. Empty `affected` is a normal result (no functions
        changed) and is not an error.
    """
    return await asyncio.to_thread(
        _select_sync,
        call_graph,
        functions,
        diff_content,
        include_transitive,
    )


def _select_sync(
    call_graph: List[Dict[str, Any]],
    functions: Dict[str, Any],
    diff_content: str,
    include_transitive: bool,
) -> AffectedFunctionSelection:
    """Sync core of `select_affected_functions`. Fault-tolerant — any failure
    in the detector becomes an empty selection rather than a raised exception.
    """
    try:
        changed_lines_per_file = extract_changed_lines_per_file(diff_content)
    except Exception as exc:  # noqa: BLE001 — soft-fail
        logger.error(f"extract_changed_lines_per_file failed: {exc}")
        changed_lines_per_file = {}

    try:
        detector = AffectedFunctionDetector(
            call_graph=call_graph,
            functions=functions,
            changed_lines_per_file=changed_lines_per_file,
        )
    except Exception as exc:  # noqa: BLE001 — soft-fail
        logger.error(f"AffectedFunctionDetector init failed: {exc}")
        return AffectedFunctionSelection(affected=[], changed_lines_per_file=changed_lines_per_file)

    try:
        raw = detector.get_affected_functions(include_transitive=include_transitive)
    except TypeError:
        # Older detector signatures don't take include_transitive.
        try:
            raw = detector.get_affected_functions()
        except Exception as exc:  # noqa: BLE001
            logger.error(f"get_affected_functions failed: {exc}")
            raw = []
    except Exception as exc:  # noqa: BLE001
        logger.error(f"get_affected_functions failed: {exc}")
        raw = []

    typed = [AffectedFunction.from_dict(r) for r in raw if isinstance(r, dict)]
    logger.info(
        f"select_affected_functions: {len(typed)} affected "
        f"({len(changed_lines_per_file)} files changed)"
    )
    return AffectedFunctionSelection(
        affected=typed,
        changed_lines_per_file=changed_lines_per_file,
    )
