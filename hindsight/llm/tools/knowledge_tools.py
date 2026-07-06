"""Knowledge tools — 2 LLM-facing tools backed by `KnowledgeStore`.

Janus-style surface:
  - `lookup_knowledge(query)` — one FTS5 search across summary/details/entity_key/function_name/file_path
  - `store_knowledge(...)` — UPSERT one learning

`subject` is fixed per call site (one registration call per analyzer pipeline),
NOT an LLM parameter. The handlers close over the store + subject so the model
never has to think about which subject it's writing to.

Registration:

    register_knowledge_tools(registry, store, subject='code')
    register_knowledge_tools(registry, store, subject='trace')

If `store` is None the handlers respond with a friendly "knowledge store
unavailable" message — pipelines that fail to construct the store keep running.
"""

from __future__ import annotations

import json
import time
from typing import Any, Dict, Optional

from hindsight.core.knowledge import KnowledgeStore
from hindsight.utils.log_util import get_logger

from .registry import ToolContext, ToolRegistry

logger = get_logger(__name__)


_KNOWLEDGE_TOOL_NAMES = (
    "lookup_knowledge",
    "store_knowledge",
)

_DEFAULT_CONFIDENCE = 0.8
_DEFAULT_LOOKUP_MAX_RESULTS = 5


def knowledge_tool_names() -> tuple[str, ...]:
    return _KNOWLEDGE_TOOL_NAMES


def register_knowledge_tools(
    registry: ToolRegistry,
    store: Optional[KnowledgeStore],
    *,
    subject: str,
) -> None:
    """Register the 2 knowledge tools on `registry`, bound to `subject`."""
    registry.register("lookup_knowledge", _make_lookup(store, subject))
    registry.register("store_knowledge", _make_record_learning(store, subject))


# ----------------------------------------------------------------------
# Handler factories — each returns a closure matching `ToolHandler`.
# ----------------------------------------------------------------------


def _make_lookup(store: Optional[KnowledgeStore], subject: str):
    async def handler(args: Dict[str, Any], _ctx: ToolContext) -> str:
        if store is None:
            return _unavailable("lookup_knowledge")
        query = _str(args.get("query"))
        if not query:
            return _err("query is required")
        kind = _opt_str(args.get("kind"))
        max_results_raw = args.get("max_results", _DEFAULT_LOOKUP_MAX_RESULTS)
        try:
            max_results = int(max_results_raw)
        except (TypeError, ValueError):
            return _err("max_results must be an integer")

        started = time.monotonic()
        try:
            results = store.lookup(
                subject, query, kind=kind, max_results=max_results,
            )
        except ValueError as exc:
            return _err(str(exc))
        elapsed_ms = int((time.monotonic() - started) * 1000)
        logger.info(
            f"[KB] lookup_knowledge subject={subject} query={query!r} kind={kind!r} "
            f"hits={len(results)} elapsed={elapsed_ms}ms"
        )
        return _format_results(results)

    return handler


def _make_record_learning(store: Optional[KnowledgeStore], subject: str):
    async def handler(args: Dict[str, Any], _ctx: ToolContext) -> str:
        if store is None:
            return _unavailable("store_knowledge")

        entity_key = _str(args.get("entity_key"))
        summary = _str(args.get("summary"))
        if not entity_key or not summary:
            return _err("entity_key and summary are required")

        # kind defaults to 'summary' when omitted — the common case is a
        # function/file summary, and requiring the field just adds friction.
        kind = _opt_str(args.get("kind")) or "summary"

        # confidence defaults to 0.8 (Janus default) when omitted so the LLM
        # can record without stopping to grade itself.
        raw_confidence = args.get("confidence")
        if raw_confidence is None:
            confidence = _DEFAULT_CONFIDENCE
        else:
            try:
                confidence = float(raw_confidence)
            except (TypeError, ValueError):
                return _err("confidence must be a number between 0.0 and 1.0")

        tags = args.get("tags")
        if tags is not None and not isinstance(tags, (list, tuple)):
            return _err("tags must be an array of strings")

        # Merge `behavior` (line-anchored specifics, encouraged in the tool
        # description) into `details` so the FTS5 index picks it up under the
        # same column. If both are provided, concatenate.
        details = _opt_str(args.get("details"))
        behavior = _opt_str(args.get("behavior"))
        if behavior:
            details = f"{behavior}\n\n{details}" if details else behavior

        try:
            rowid = store.record_learning(
                subject=subject,
                kind=kind,
                entity_key=entity_key,
                summary=summary,
                confidence=confidence,
                file_path=_opt_str(args.get("file_path")),
                function_name=_opt_str(args.get("function_name")),
                checksum=_opt_str(args.get("checksum")),
                details=details,
                tags=tags,
            )
        except ValueError as exc:
            return _err(str(exc))
        except Exception as exc:  # noqa: BLE001 — surface DB errors to LLM
            logger.exception(f"store_knowledge failed: {exc}")
            return _err(f"record failed: {exc}")

        logger.info(
            f"[KB] store_knowledge subject={subject} kind={kind} "
            f"entity_key={entity_key!r} confidence={confidence} id={rowid}"
        )
        return json.dumps({"ok": True, "id": rowid})

    return handler


# ----------------------------------------------------------------------
# Small helpers
# ----------------------------------------------------------------------


def _str(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _opt_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    s = str(value).strip()
    return s or None


def _err(message: str) -> str:
    return json.dumps({"ok": False, "error": message})


def _unavailable(tool_name: str) -> str:
    return json.dumps({"ok": False, "error": f"{tool_name} unavailable — knowledge store not configured"})


def _format_results(results) -> str:
    """Return a JSON string the LLM can read. Empty list = miss."""
    return json.dumps(results, default=str)
