"""One-shot LLM call helpers.

For places that don't need the full iterative tool-using loop — a directory
classifier asking "is this third-party code?", an issue filter asking "is
this issue trivial?", a file-summary generator asking for a short paragraph.

These wrap a single `AsyncLLMClient.send()` call so the callers don't need to
re-implement message construction.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from hindsight.utils.log_util import get_logger

from .client import AsyncLLMClient
from .errors import LLMError

logger = get_logger(__name__)


async def one_shot_text(
    client: AsyncLLMClient,
    *,
    system_prompt: str,
    user_prompt: str,
    enable_system_cache: bool = True,
) -> Optional[str]:
    """Send one request and return the assistant's plain-text response.

    Returns None on API failure; the caller decides whether to retry.
    """
    try:
        response = await client.send(
            system_prompt=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
            enable_system_cache=enable_system_cache,
        )
        return response.text
    except LLMError as exc:
        logger.error(f"one_shot_text failed: {exc}")
        return None


async def one_shot_json(
    client: AsyncLLMClient,
    *,
    system_prompt: str,
    user_prompt: str,
    enable_system_cache: bool = True,
) -> Optional[Any]:
    """Send one request and parse the response body as JSON.

    Tries to be tolerant: if the response is wrapped in markdown fences, we
    strip them; if it isn't strict JSON, we return None and the caller can
    retry with a stricter prompt.
    """
    text = await one_shot_text(
        client,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        enable_system_cache=enable_system_cache,
    )
    if text is None:
        return None
    stripped = _strip_markdown_fences(text)
    try:
        return json.loads(stripped)
    except json.JSONDecodeError as exc:
        logger.warning(f"one_shot_json: response was not valid JSON ({exc}); raw='{stripped[:200]}'")
        return None


def _strip_markdown_fences(text: str) -> str:
    """Remove a single surrounding ```json ... ``` fence if present."""
    s = text.strip()
    if s.startswith("```"):
        first_newline = s.find("\n")
        if first_newline != -1:
            s = s[first_newline + 1:]
        if s.endswith("```"):
            s = s[:-3]
    return s.strip()
