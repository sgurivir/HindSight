"""Security analysis pipeline — shared helpers.

The legacy security analyzers (`SinkAnalyzer`, `ExternalInputAnalyzer`,
`FlowVulnerabilityAnalyzer`) are already self-orchestrating around an
async `llm_request_fn(system_prompt, messages) -> str` callable, an
`mcp_server` for tool dispatch, and a `RateLimiter`. We don't need a
full pipeline class for them — they own their batching, retry, and
result aggregation logic.

This module provides the **one** new piece the rewrite required:
`make_async_llm_request_fn`, which builds the `llm_request_fn` callable
on top of the new `AsyncLLMClient` instead of the legacy `Claude` +
`create_llm_provider` shim. Plus a small async-context-manager wrapper
so the caller can run a batch and tear the httpx pool down deterministically.

`RateLimiter` and `CodeNavigationServer` aren't re-exported here — the
analyzers import them directly:

    from hindsight.llm.rate_limit import AsyncRateLimiter as RateLimiter
    from hindsight.core.lang_util.code_navigation import CodeNavigationServer
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Awaitable, Callable, Dict, List, Mapping

from hindsight.core.constants import (
    DEFAULT_LLM_API_END_POINT,
    DEFAULT_LLM_MODEL,
    DEFAULT_MAX_TOKENS,
)
from hindsight.llm import AsyncLLMClient, LLMClientConfig
from hindsight.llm.errors import LLMError
from hindsight.utils.log_util import get_logger

logger = get_logger(__name__)


LLMRequestFn = Callable[[str, List[Dict[str, str]]], Awaitable[str]]


@asynccontextmanager
async def open_security_llm(
    *,
    api_key: str,
    config: Mapping[str, Any],
    model_override: str = "",
    max_tokens_override: int = 0,
) -> AsyncIterator[LLMRequestFn]:
    """Yield an async `(system_prompt, messages) -> str` callable backed
    by a single `AsyncLLMClient`. The client is closed when the context
    exits.

    Matches the legacy callable signature used by SinkAnalyzer,
    ExternalInputAnalyzer, and FlowVulnerabilityAnalyzer so the analyzer
    bodies don't need any other changes.

    On any LLM error this returns an empty string — same as the legacy
    `make_request` path, which swallowed errors as `return ""`. The
    security analyzers treat empty strings as "no answer; skip this
    function". A noisy upstream failure that breaks one batch must not
    kill the whole pipeline.
    """
    client_config = LLMClientConfig(
        api_url=str(config.get("api_end_point", DEFAULT_LLM_API_END_POINT)),
        model=model_override or str(config.get("model", DEFAULT_LLM_MODEL)),
        max_tokens=int(max_tokens_override or config.get("max_tokens", DEFAULT_MAX_TOKENS)),
        api_key=api_key or "",
    )

    async with AsyncLLMClient(client_config) as client:

        async def _request(system_prompt: str, messages: List[Dict[str, str]]) -> str:
            try:
                response = await client.send(
                    system_prompt=system_prompt,
                    messages=messages,
                    enable_system_cache=True,
                    cache_ttl="1h",
                )
            except LLMError as exc:
                logger.warning(f"security LLM request failed: {exc}")
                return ""
            except Exception as exc:  # noqa: BLE001 — must not crash the analyzer
                logger.warning(f"security LLM request raised unexpectedly: {exc}")
                return ""
            return response.text or ""

        yield _request


__all__ = ["LLMRequestFn", "open_security_llm"]
