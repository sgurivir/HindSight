"""JSON-embedded tool-request protocol.

This is how tool calls travel between the model and the orchestrator:

  - The system prompt instructs the model to emit JSON like::

        ```json
        {"tool": "readFile", "path": "src/foo.swift", "reason": "..."}
        ```

  - The model puts that JSON anywhere in its response text.
  - `extract_tool_requests()` regex-scans the text and returns every JSON object
    with a `tool` key.
  - Each request becomes a `ToolCall(name, args)` that the orchestrator hands
    to `ToolRegistry`.

This is intentionally NOT the provider-native tool-use API. Keeping tool calls
in plaintext JSON means the same prompts work across providers and we can log
the exact bytes the model emitted.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Any, Dict, List


_MARKDOWN_PATTERN = re.compile(r'```json\s*(\{[^}]*"tool"[^}]*\})\s*```', re.DOTALL)
_SIMPLE_PATTERN = re.compile(r'(\{[^}]*"tool"[^}]*\})', re.DOTALL)


@dataclass(frozen=True)
class ToolCall:
    """One tool call extracted from a model response."""

    name: str
    args: Dict[str, Any]

    def make_id(self, iteration: int, index: int) -> str:
        """Build a stable id for conversation logging.

        Matches the legacy format `json_tool_{iter}_{idx}` so existing
        conversation logs stay readable across the migration.
        """
        return f"json_tool_{iteration}_{index}"


def extract_tool_requests(content: str) -> List[ToolCall]:
    """Return every JSON tool request embedded in `content`.

    Looks first for fenced ```json blocks containing a `"tool"` key, then for
    bare `{...}` snippets with a `"tool"` key. Duplicates between the two
    passes are de-duped by exact-string match (same logic as the legacy
    extractor).
    """
    markdown_matches = _MARKDOWN_PATTERN.findall(content)
    simple_matches = _SIMPLE_PATTERN.findall(content)

    seen: set[str] = set()
    ordered: list[str] = []
    for match in markdown_matches + simple_matches:
        if match in seen:
            continue
        seen.add(match)
        ordered.append(match)

    requests: list[ToolCall] = []
    for raw in ordered:
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(parsed, dict) or "tool" not in parsed:
            continue
        name = str(parsed.get("tool"))
        args = {k: v for k, v in parsed.items() if k != "tool"}
        requests.append(ToolCall(name=name, args=args))
    return requests


def make_legacy_tool_use_block(call: ToolCall) -> Dict[str, Any]:
    """Shape a `ToolCall` into the legacy `tool_use` dict expected by old
    `Tools.execute_tool_use`.

    Used during the transition while orchestration is still mid-migration.
    The new `ToolRegistry` consumes `ToolCall` directly and ignores this.
    """
    return {
        "id": f"json_{call.name}_{int(time.time())}",
        "name": call.name,
        "input": dict(call.args),
    }
