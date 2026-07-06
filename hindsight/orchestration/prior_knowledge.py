"""Helpers for injecting prior `KnowledgeStore` learnings into stage prompts.

Every analyzer pipeline calls into the same shared knowledge store. To make
prior learnings visible to the LLM without forcing it to call
`read_knowledge_*` every iteration, each stage can prepend a small
"Prior knowledge from previous analyses" block to its system prompt.

This module collects two formatting flavors used across pipelines:

- `format_prior_knowledge_for_function(...)` — looks up by function name (+
  optional file_path / checksum). Used by per-function stages (Stage 4a/4b,
  Stage Da/Db, Stage Ta/Tb).

- `format_prior_knowledge_for_functions(...)` — looks up multiple function
  names at once (call-tree stages, where the prompt covers a whole subtree).

Both return `None` when there's nothing useful to inject. Both swallow store
errors — a knowledge-store hiccup must never fail the analysis run.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Tuple

from hindsight.core.knowledge import KnowledgeStore
from hindsight.utils.log_util import get_logger

logger = get_logger(__name__)


_DEFAULT_MAX_ENTRIES_PER_FUNCTION = 3
_DEFAULT_MAX_TOTAL_ENTRIES = 12


def format_prior_knowledge_for_function(
    store: Optional[KnowledgeStore],
    subject: str,
    *,
    function_name: str,
    file_path: Optional[str] = None,
    function_checksum: Optional[str] = None,
    max_entries: int = 5,
) -> Optional[str]:
    """Return a "Prior knowledge" block for a single function, or None.

    When `function_checksum` is provided, fresh-checksum entries (and entries
    with no checksum) are preferred; stale entries are still included if
    nothing fresh exists but are clearly marked.
    """
    if store is None or not function_name:
        return None
    try:
        hits = store.recall_by_function(
            subject=subject,
            function_name=function_name,
            file_path=file_path or None,
        )
    except Exception as exc:  # noqa: BLE001 — never let a store error fail the run
        logger.debug(f"recall_by_function failed for {function_name}: {exc}")
        return None
    if not hits:
        return None

    if function_checksum:
        fresh = [
            h for h in hits
            if not h.get("checksum") or h.get("checksum") == function_checksum
        ]
        if fresh:
            hits = fresh
    hits = hits[:max_entries]
    return _render_block(
        intro=(
            "The shared knowledge store has the following learnings for this function. "
            "Verify, expand, or trust them as appropriate — do not blindly accept stale information."
        ),
        groups=[(function_name, hits)],
        current_checksum=function_checksum,
    )


def format_prior_knowledge_for_functions(
    store: Optional[KnowledgeStore],
    subject: str,
    functions: Iterable[Tuple[str, Optional[str], Optional[str]]],
    *,
    max_entries_per_function: int = _DEFAULT_MAX_ENTRIES_PER_FUNCTION,
    max_total_entries: int = _DEFAULT_MAX_TOTAL_ENTRIES,
) -> Optional[str]:
    """Multi-function variant. Each item is `(function_name, file_path?, checksum?)`.

    Useful for call-tree and trace stages where the prompt spans a chain of
    functions. Caps total entries to avoid blowing up the system prompt.
    """
    if store is None:
        return None
    groups: List[Tuple[str, List[Dict[str, Any]]]] = []
    total = 0
    for entry in functions:
        if total >= max_total_entries:
            break
        function_name = entry[0]
        file_path = entry[1] if len(entry) > 1 else None
        checksum = entry[2] if len(entry) > 2 else None
        if not function_name:
            continue
        try:
            hits = store.recall_by_function(
                subject=subject,
                function_name=function_name,
                file_path=file_path or None,
            )
        except Exception as exc:  # noqa: BLE001 — never let a store error fail the run
            logger.debug(f"recall_by_function failed for {function_name}: {exc}")
            continue
        if not hits:
            continue
        if checksum:
            fresh = [
                h for h in hits
                if not h.get("checksum") or h.get("checksum") == checksum
            ]
            if fresh:
                hits = fresh
        take = min(max_entries_per_function, max_total_entries - total)
        hits = hits[:take]
        if not hits:
            continue
        groups.append((function_name, hits))
        total += len(hits)

    if not groups:
        return None
    return _render_block(
        intro=(
            "The shared knowledge store has the following learnings for functions in scope. "
            "Verify, expand, or trust them as appropriate — do not blindly accept stale information."
        ),
        groups=groups,
        current_checksum=None,
    )


# ----------------------------------------------------------------------
# Rendering
# ----------------------------------------------------------------------


def _render_block(
    *,
    intro: str,
    groups: List[Tuple[str, List[Dict[str, Any]]]],
    current_checksum: Optional[str],
) -> str:
    lines = ["## Prior knowledge from previous analyses", "", intro, ""]
    for function_name, hits in groups:
        if len(groups) > 1:
            lines.append(f"### {function_name}")
        for h in hits:
            kind = h.get("kind", "?")
            summary = (h.get("summary") or "").strip()
            confidence = h.get("confidence")
            tags = h.get("tags") or []
            checksum = h.get("checksum")
            stale = (
                " (checksum changed — may be stale)"
                if current_checksum and checksum and checksum != current_checksum
                else ""
            )
            tag_str = f" [tags: {', '.join(tags)}]" if tags else ""
            lines.append(
                f"- **{kind}** (confidence={confidence}){tag_str}{stale}: {summary}"
            )
        if len(groups) > 1:
            lines.append("")
    # Dedup guidance: the block above already delivers what a lookup would
    # return, so re-calling `lookup_knowledge` for these entities wastes an
    # LLM turn. Only re-lookup when the entry is stale or missing.
    lines.append("")
    lines.append(
        "**Do NOT call `lookup_knowledge` for the entries listed above** — you already "
        "have them. Only issue new lookups for functions, files, or topics not covered here."
    )
    return "\n".join(lines).rstrip()
