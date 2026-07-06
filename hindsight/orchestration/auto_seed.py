"""Programmatic seeding of the KnowledgeStore after successful analyses.

The LLM is instructed to call `store_knowledge` for each function it
reasons about, but in practice it rarely does — leaving the DB empty
and denying future `lookup_knowledge` calls any hits. That trains the
model to skip lookup, which suppresses store, and the loop never warms.

Auto-seeding closes that loop by writing a low-confidence summary for
every non-stubbed node the call-tree stage successfully analyzed. The
next run's lookup returns something instead of `[]`, giving the model
a visible reason to keep calling the tool. LLM-written entries live
alongside seeds (different `tags`) and take priority via higher
confidence when both exist.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

from hindsight.core.knowledge import KnowledgeStore
from hindsight.utils.log_util import get_logger

logger = get_logger(__name__)


_SEED_CONFIDENCE = 0.4

# The unique index is (subject, repo, kind, entity_key, checksum, tags).
# A distinct tag keeps seed rows from colliding with LLM-written entries —
# both can coexist and lookup_knowledge returns whichever ranks higher.
_SEED_TAGS = ["auto-seed"]

# Numbered-source prefix from CallTreeSectionGenerator: "  123 | body".
_LINE_PREFIX_RE = re.compile(r"^\s*\d+\s*\|\s?")

# Comment / docstring prefixes we skip when hunting for a signature line.
_COMMENTY_PREFIXES = ("//", "#", "/*", "*/", "*", '"""', "'''")

_MAX_SIGNATURE_SEARCH_LINES = 10
_SIGNATURE_MAX_CHARS = 200


def seed_call_tree_summaries(
    store: Optional[KnowledgeStore],
    *,
    subject: str,
    tree_dict: Dict[str, Any],
    issues: Optional[List[Dict[str, Any]]] = None,
) -> int:
    """Write a low-confidence summary for each non-stub node in the tree.

    Returns the number of rows written. Returns 0 quietly when the store
    is absent — auto-seeding is best-effort, never load-bearing.
    """
    if store is None:
        return 0

    nodes = tree_dict.get("nodes") if isinstance(tree_dict, dict) else None
    if not isinstance(nodes, list) or not nodes:
        return 0

    issues_by_key = _index_issues_by_function(issues or [])

    written = 0
    skipped_stub = 0
    for node in nodes:
        if not isinstance(node, dict):
            continue

        # Stubs weren't reasoned about — no body available to summarize.
        source = node.get("source") or ""
        if not source or node.get("source_omitted_reason"):
            skipped_stub += 1
            continue

        function_name = (node.get("function") or "").strip()
        file_path = (node.get("file") or "").strip()
        if not function_name or not file_path:
            continue

        signature = _extract_signature(source)
        if not signature:
            continue

        entity_key = f"{file_path}::{function_name}"
        issue_marker = _issue_marker_for(function_name, file_path, issues_by_key)

        try:
            store.record_learning(
                subject=subject,
                kind="summary",
                entity_key=entity_key,
                summary=_compose_summary(signature, node, issue_marker),
                confidence=_SEED_CONFIDENCE,
                file_path=file_path,
                function_name=function_name,
                checksum=node.get("checksum") or None,
                details=_compose_details(node, issue_marker),
                tags=_SEED_TAGS,
            )
            written += 1
        except Exception as exc:  # noqa: BLE001 — seeding must never crash pipeline
            logger.debug(f"[auto-seed] skipped {entity_key}: {exc}")

    logger.info(
        f"[auto-seed] wrote {written} summary row(s) "
        f"({skipped_stub} stub(s) skipped, {len(nodes)} node(s) total)"
    )
    return written


def _extract_signature(source: str) -> str:
    """First non-comment content line from a numbered source block."""
    seen = 0
    fallback = ""
    for raw in source.splitlines():
        stripped = _LINE_PREFIX_RE.sub("", raw).strip()
        if not stripped:
            continue
        seen += 1
        if not fallback:
            fallback = stripped
        if not stripped.startswith(_COMMENTY_PREFIXES):
            return stripped[:_SIGNATURE_MAX_CHARS]
        if seen >= _MAX_SIGNATURE_SEARCH_LINES:
            break
    return fallback[:_SIGNATURE_MAX_CHARS]


def _index_issues_by_function(
    issues: Iterable[Dict[str, Any]],
) -> Dict[Tuple[str, str], List[Dict[str, Any]]]:
    grouped: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    for issue in issues:
        if not isinstance(issue, dict):
            continue
        fn = (issue.get("function_name") or issue.get("function") or "").strip()
        fp = (issue.get("file_path") or issue.get("file") or "").strip()
        if not fn or not fp:
            continue
        grouped.setdefault((fn, fp), []).append(issue)
    return grouped


def _issue_marker_for(
    function_name: str,
    file_path: str,
    issues_by_key: Dict[Tuple[str, str], List[Dict[str, Any]]],
) -> Optional[str]:
    hits = issues_by_key.get((function_name, file_path))
    if not hits:
        return None
    if len(hits) == 1:
        return "1 defect flagged by call-tree analysis"
    return f"{len(hits)} defects flagged by call-tree analysis"


def _compose_summary(
    signature: str,
    node: Dict[str, Any],
    issue_marker: Optional[str],
) -> str:
    parts = [f"Signature: {signature}"]
    callees = node.get("expanded_calls") or []
    if callees:
        parts.append(f"{len(callees)} callee(s) in tree")
    if issue_marker:
        parts.append(issue_marker)
    return ". ".join(parts) + "."


def _compose_details(
    node: Dict[str, Any],
    issue_marker: Optional[str],
) -> Optional[str]:
    lines = ["Auto-seeded from a call-tree analysis; not authored by the LLM."]
    start = node.get("start_line")
    end = node.get("end_line")
    if start and end:
        lines.append(f"Original source lines: {start}-{end}.")
    out_of_tree = node.get("other_callees") or []
    if out_of_tree:
        preview = ", ".join(out_of_tree[:5])
        suffix = f" (+{len(out_of_tree) - 5} more)" if len(out_of_tree) > 5 else ""
        lines.append(f"Callees outside the analyzed tree: {preview}{suffix}.")
    if issue_marker:
        lines.append(f"Analyzer flag: {issue_marker}.")
    return "\n".join(lines)
