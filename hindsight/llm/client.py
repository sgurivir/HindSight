"""Async LLM client.

One `AsyncLLMClient` per `AnalysisSession`. Owns a single `httpx.AsyncClient`
so connection pooling works across all stages and all concurrent functions.
Exposes one method — `send()` — that handles auth refresh, retries, and
typed-error mapping.

Thread/loop affinity:
  - All public methods are coroutines.
  - The underlying `httpx.AsyncClient` is created lazily on first use so the
    client can be constructed outside an event loop (e.g. by configuration
    code) and only acquires loop-bound resources when the FastAPI handler
    or CLI's `asyncio.run` actually starts using it.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import httpx

from hindsight.utils.log_util import get_logger

from .bedrock import (
    AuthState,
    LLMClientConfig,
    build_auth_state,
    build_payload,
    check_token_limit,
    estimate_tokens,
    refresh_auth_if_needed,
)
from .errors import LLMFatalError, LLMResponseShapeError, LLMTransientError

logger = get_logger(__name__)

DEFAULT_MAX_RETRIES = 3
DEFAULT_RETRY_DELAYS = (60, 60, 90)  # seconds; matches legacy DEFAULT_RETRY_DELAYS

INPUT_TOO_LONG_ERROR = "input is too long"


@dataclass
class LLMResponse:
    """Parsed response from a successful LLM call."""

    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    raw: Optional[Dict[str, Any]] = None


class AsyncLLMClient:
    """Async client for the Bedrock / Apple GenAI gateway.

    Usage::

        client = AsyncLLMClient(config)
        try:
            response = await client.send(system_prompt, messages)
        finally:
            await client.aclose()

    Or as an async context manager::

        async with AsyncLLMClient(config) as client:
            response = await client.send(system_prompt, messages)
    """

    def __init__(
        self,
        config: LLMClientConfig,
        *,
        max_retries: int = DEFAULT_MAX_RETRIES,
        retry_delays: tuple[int, ...] = DEFAULT_RETRY_DELAYS,
    ):
        self.config = config
        self._auth = build_auth_state(config)
        self._max_retries = max_retries
        self._retry_delays = retry_delays
        self._http: Optional[httpx.AsyncClient] = None
        self._http_lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def __aenter__(self) -> "AsyncLLMClient":
        await self._ensure_http()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Release HTTP connections. Safe to call multiple times."""
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    # ------------------------------------------------------------------
    # Token budgeting
    # ------------------------------------------------------------------

    def estimate_tokens(self, text: str) -> int:
        return estimate_tokens(text)

    def check_token_limit(self, system_prompt: str, user_prompt: str) -> bool:
        return check_token_limit(self.config, system_prompt, user_prompt)

    # ------------------------------------------------------------------
    # Sending
    # ------------------------------------------------------------------

    async def send(
        self,
        system_prompt: Optional[str],
        messages: List[Dict[str, Any]],
        *,
        enable_system_cache: bool = True,
        cache_ttl: str = "1h",
    ) -> LLMResponse:
        """Send one request and return its parsed response.

        If `system_prompt` is non-empty, it is prepended as the system message
        (cached when `enable_system_cache=True`). The model's response text
        comes back as `LLMResponse.text`; the raw dict is also retained for
        conversation logging.

        Raises:
            LLMFatalError: 400-class errors or token-limit violations.
            LLMTransientError: 429 / 5xx / timeout after retry exhaustion.
            LLMResponseShapeError: 200 OK but no extractable text content.
        """
        full_messages: list[Dict[str, Any]] = []
        if system_prompt:
            full_messages.append({"role": "system", "content": system_prompt})
        full_messages.extend(messages)

        try:
            payload = build_payload(
                self.config,
                full_messages,
                enable_system_cache=enable_system_cache,
                cache_ttl=cache_ttl,
            )
        except ValueError as exc:
            raise LLMFatalError(str(exc)) from exc

        raw = await self._post_with_retries(payload)
        text = self._extract_text(raw)
        usage = raw.get("usage", {}) if isinstance(raw, dict) else {}
        input_tokens = usage.get("input_tokens", 0) or usage.get("prompt_tokens", 0)
        output_tokens = usage.get("output_tokens", 0) or usage.get("completion_tokens", 0)
        return LLMResponse(
            text=text,
            input_tokens=int(input_tokens or 0),
            output_tokens=int(output_tokens or 0),
            raw=raw,
        )

    async def validate_connection(self) -> bool:
        """Quick handshake — sends a 1-token ping and confirms a usable reply."""
        try:
            response = await self.send(
                system_prompt=None,
                messages=[{"role": "user", "content": "Hello, please respond with 'OK' to confirm connection."}],
                enable_system_cache=False,
            )
            return "OK" in response.text.upper()
        except Exception as exc:
            logger.error(f"Connection validation failed: {exc}")
            return False

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _ensure_http(self) -> httpx.AsyncClient:
        """Create the httpx client lazily under a lock.

        Lazy creation lets us build the client at config time (not loop time)
        and still get a single shared connection pool for all sends.
        """
        if self._http is not None:
            return self._http
        async with self._http_lock:
            if self._http is None:
                self._http = httpx.AsyncClient(
                    timeout=self.config.timeout,
                    verify=self._auth.verify_ssl,
                    trust_env=False,  # CVE-2024-35195: don't read .netrc
                    follow_redirects=False,
                )
                logger.debug(
                    f"Created httpx.AsyncClient for {self.config.api_url} "
                    f"(verify={self._auth.verify_ssl}, trust_env=False)"
                )
        return self._http

    async def _post_with_retries(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """POST with the legacy retry policy: 3 retries on 429/5xx/timeout/network."""
        http = await self._ensure_http()
        last_error: str = "unknown"

        for attempt in range(self._max_retries + 1):
            # Refresh Apple OIDC token if our session uses auto-refresh.
            # AppleConnectTokenManager is sync; run in a thread so we don't
            # block the event loop on any internal I/O it does.
            await asyncio.to_thread(refresh_auth_if_needed, self._auth)

            try:
                start = time.monotonic()
                response = await http.post(
                    self.config.api_url,
                    headers=self._auth.headers,
                    json=payload,
                )
                duration = time.monotonic() - start
                logger.debug(f"LLM request completed in {duration:.2f}s (status={response.status_code})")

                if response.status_code == 200:
                    if attempt > 0:
                        logger.info(f"LLM request succeeded after {attempt} retries")
                    return response.json()

                error_text = response.text
                logger.error(
                    f"LLM request failed with status {response.status_code}: {error_text[:500]}"
                )

                if response.status_code == 400 and INPUT_TOO_LONG_ERROR in error_text.lower():
                    raise LLMFatalError("input_too_long", status_code=400)

                if response.status_code in (500, 502, 503, 504):
                    if attempt < self._max_retries:
                        await self._wait_before_retry(attempt, f"server error {response.status_code}")
                        continue
                    raise LLMTransientError(
                        f"server_error: {error_text[:500]}",
                        status_code=response.status_code,
                    )

                if response.status_code == 429:
                    if attempt < self._max_retries:
                        await self._wait_before_retry(attempt, "rate limit (429)")
                        continue
                    raise LLMTransientError("rate_limit_exceeded", status_code=429)

                # Other 4xx — non-retriable.
                raise LLMFatalError(
                    f"api_error: {error_text[:500]}",
                    status_code=response.status_code,
                )

            except httpx.TimeoutException as exc:
                last_error = f"timeout: {exc}"
                if attempt < self._max_retries:
                    await self._wait_before_retry(attempt, "timeout")
                    continue
                raise LLMTransientError(last_error) from exc
            except httpx.RequestError as exc:
                last_error = f"network: {exc}"
                if attempt < self._max_retries:
                    await self._wait_before_retry(attempt, f"network error: {exc}")
                    continue
                raise LLMTransientError(last_error) from exc

        # Defensive: the loop above either returns or raises.
        raise LLMTransientError(f"max_retries_exceeded: {last_error}")

    async def _wait_before_retry(self, attempt: int, reason: str) -> None:
        delay = self._retry_delays[min(attempt, len(self._retry_delays) - 1)]
        logger.warning(
            f"LLM request failing ({reason}); waiting {delay}s before retry "
            f"(attempt {attempt + 1}/{self._max_retries + 1})"
        )
        await asyncio.sleep(delay)

    @staticmethod
    def _extract_text(response: Dict[str, Any]) -> str:
        """Extract the assistant text from either Bedrock or Anthropic shapes.

        Bedrock:   {"choices": [{"message": {"content": "..."}}], "usage": {...}}
        Anthropic: {"content": [{"type": "text", "text": "..."}], "usage": {...}}
        """
        if not isinstance(response, dict):
            raise LLMResponseShapeError(f"Unexpected response type: {type(response).__name__}")

        choices = response.get("choices")
        if isinstance(choices, list) and choices:
            message = choices[0].get("message", {})
            content = message.get("content", "")
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                parts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
                return "\n".join(parts)

        content_blocks = response.get("content")
        if isinstance(content_blocks, list):
            parts = [b.get("text", "") for b in content_blocks if isinstance(b, dict) and b.get("type") == "text"]
            if parts:
                return "\n".join(parts)

        raise LLMResponseShapeError(f"No extractable text in response (keys: {list(response.keys())})")
