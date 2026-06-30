"""Bedrock-flavored request payload + auth header construction.

This is NOT AWS SigV4. The existing system speaks to AWS Bedrock through
Apple's internal GenAI gateway (`genai.apple.com`, `floodgate.g.apple.com`)
which uses Bearer-token authentication with optional OIDC / FloodGate headers.
The transport is HTTPS POST with a JSON payload — same shape as Anthropic's
Messages API but routed through Apple's gateway.

Auth priority for Apple endpoints (preserved verbatim from the legacy provider):

  1. `project_credentials` → FloodGate token. Sends both
     `Authorization: Bearer ...` and `X-Floodgate-Project-Token: ...`.
  2. `credentials` → OAuth/OIDC token. Sends both
     `Authorization: Bearer ...` and `X-Apple-OIDC-Token: ...`.
  3. `api_key` (legacy) → treated as an OIDC token; seeds the
     `AppleConnectTokenManager` for auto-refresh.
  4. Nothing → falls back to `AppleConnectTokenManager.get_token()` and
     auto-refreshes on every send.

Non-Apple endpoints use the plain `Authorization: Bearer <token>` header.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from hindsight.core.constants import ModelLimits
from hindsight.utils.log_util import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class LLMClientConfig:
    """Configuration for `AsyncLLMClient`.

    Field shape matches `LLMConfig` from the legacy `base_provider` so existing
    config-loading code can pass through unchanged.
    """

    api_url: str
    model: str
    max_tokens: int = 64000
    timeout: float = 300.0
    api_key: str = ""
    credentials: str = ""
    project_credentials: str = ""


def is_apple_genai_endpoint(api_url: str) -> bool:
    """True for Apple's internal GenAI / FloodGate gateways."""
    url = api_url.lower()
    return "genai.apple.com" in url or "floodgate.g.apple.com" in url


@dataclass
class AuthState:
    """Mutable auth state — headers may be refreshed mid-session.

    Refresh path is used when `use_apple_connect_auto_refresh` is True; the
    token manager singleton (`hindsight.utils.api_key_util.get_token_manager`)
    fetches a new OIDC token every ~5 minutes.
    """

    headers: Dict[str, str] = field(default_factory=dict)
    verify_ssl: bool | str = True
    use_apple_connect_auto_refresh: bool = False


def build_auth_state(config: LLMClientConfig) -> AuthState:
    """Build the initial `AuthState` for a session based on the API URL + creds.

    Mirrors the dispatch in the legacy `AWSBedrockProvider.__init__`.
    """
    apple_endpoint = is_apple_genai_endpoint(config.api_url)
    headers: Dict[str, str] = {"Content-Type": "application/json"}
    auto_refresh = False

    if apple_endpoint:
        if config.project_credentials and config.project_credentials.strip():
            token = config.project_credentials.strip()
            headers["Authorization"] = f"Bearer {token}"
            headers["X-Floodgate-Project-Token"] = token
            logger.debug("Bedrock auth: project_credentials (FloodGate)")
        elif config.credentials and config.credentials.strip():
            token = config.credentials.strip()
            headers["Authorization"] = f"Bearer {token}"
            headers["X-Apple-OIDC-Token"] = token
            logger.debug("Bedrock auth: credentials (OIDC)")
        elif config.api_key and config.api_key.strip():
            token = config.api_key.strip()
            headers["Authorization"] = f"Bearer {token}"
            headers["X-Apple-OIDC-Token"] = token
            auto_refresh = True
            logger.debug("Bedrock auth: api_key seeded with AppleConnect auto-refresh")
            _seed_token_manager(token)
        else:
            auto_refresh = True
            logger.debug("Bedrock auth: AppleConnect auto-refresh (no creds provided)")
    else:
        token = (config.credentials or config.api_key or "").strip()
        headers["Authorization"] = f"Bearer {token}"
        logger.debug("Bedrock auth: standard bearer token")

    verify_ssl = _resolve_ssl_verification(apple_endpoint)

    return AuthState(
        headers=headers,
        verify_ssl=verify_ssl,
        use_apple_connect_auto_refresh=auto_refresh,
    )


def refresh_auth_if_needed(state: AuthState) -> None:
    """If the session is on AppleConnect auto-refresh, fetch a fresh token.

    Called by the client before each request. No-op for static-credential
    sessions. Safe to call from a thread (`AppleConnectTokenManager` is
    sync but its internal mutex is reentrant-friendly).
    """
    if not state.use_apple_connect_auto_refresh:
        return
    try:
        from hindsight.utils.api_key_util import get_token_manager

        tm = get_token_manager()
        if not tm.needs_refresh():
            return
        logger.info("Refreshing AppleConnect token...")
        token = tm.get_token()
        if not token:
            logger.warning("Failed to get AppleConnect token - headers not updated")
            return
        state.headers["Authorization"] = f"Bearer {token}"
        state.headers["X-Apple-OIDC-Token"] = token
        preview = f"{token[:4]}...{token[-4:]}" if len(token) > 8 else "****"
        logger.info(f"AppleConnect token refreshed (token: {preview})")
    except ImportError as exc:
        logger.error(f"Failed to import token manager: {exc}")
    except Exception as exc:
        logger.error(f"Error refreshing AppleConnect token: {exc}")


def build_payload(
    config: LLMClientConfig,
    messages: List[Dict[str, Any]],
    *,
    stream: bool = False,
    enable_system_cache: bool = True,
    cache_ttl: str = "1h",
) -> Dict[str, Any]:
    """Build the request body sent to the Bedrock / Apple gateway.

    Validates the rough token budget up front to fail fast instead of
    discovering it from a 400 response. The caller is expected to have run a
    `check_token_limit()` already; this is a defense-in-depth check.

    Raises:
        ValueError: if the estimated input tokens exceed the model's context
            window minus `max_tokens`.
    """
    processed: list[Dict[str, Any]] = []
    total_chars = 0
    for message in messages:
        m = dict(message)
        content = m.get("content", "")
        total_chars += len(content) if isinstance(content, str) else len(str(content))
        if enable_system_cache and m.get("role") == "system":
            m["cache_control"] = {"type": "ephemeral", "ttl": cache_ttl}
        processed.append(m)

    estimated_tokens = total_chars // 3
    context_window = ModelLimits.get_context_window(config.model)
    max_input_tokens = context_window - config.max_tokens
    if estimated_tokens > max_input_tokens:
        raise ValueError(
            "Payload exceeds token limits: "
            f"total_chars={total_chars:,}, est_tokens={estimated_tokens:,}, "
            f"max_input_tokens={max_input_tokens:,}, "
            f"context_window={context_window:,}, max_tokens={config.max_tokens:,}"
        )

    max_output = ModelLimits.get_max_output_tokens(config.model)
    return {
        "model": config.model,
        "max_tokens": min(config.max_tokens, max_output),
        "messages": processed,
        "stream": stream,
    }


def estimate_tokens(text: str) -> int:
    """Conservative ~3 chars/token estimation (matches legacy behavior)."""
    return len(text) // 3


def check_token_limit(config: LLMClientConfig, system_prompt: str, user_prompt: str) -> bool:
    """True iff combined prompts fit in the model's context window."""
    estimated = estimate_tokens(system_prompt + user_prompt)
    context_window = ModelLimits.get_context_window(config.model)
    return estimated <= context_window - config.max_tokens


# ----------------------------------------------------------------------
# Internals
# ----------------------------------------------------------------------


def _seed_token_manager(token: str) -> None:
    """Inject an initial OIDC token into the singleton manager.

    Matches the legacy seeding done in `AWSBedrockProvider.__init__` so that
    the first request does not waste time re-fetching the token we already
    have.
    """
    try:
        from hindsight.utils.api_key_util import get_token_manager

        tm = get_token_manager()
        if tm._current_token is None:  # noqa: SLF001 — same surface used by legacy code
            tm._current_token = token  # noqa: SLF001
            tm._token_acquired_at = time.time()  # noqa: SLF001
            tm._is_apple_connect_token = True  # noqa: SLF001
    except Exception as exc:
        logger.debug(f"Token manager seeding skipped: {exc}")


def _resolve_ssl_verification(apple_endpoint: bool) -> bool | str:
    """Mirror the legacy SSL verification logic.

    External endpoints always verify. Apple-internal endpoints check for a
    custom CA bundle env var; if absent, the `ENABLE_SSL_VERIFICATION` env var
    may turn on strict verification (likely to fail with self-signed certs);
    default is no verification for the internal gateway.
    """
    if not apple_endpoint:
        return True
    custom_bundle = os.getenv("REQUESTS_CA_BUNDLE") or os.getenv("CURL_CA_BUNDLE")
    if custom_bundle and os.path.exists(custom_bundle):
        logger.debug(f"Using custom CA bundle for Apple endpoint: {custom_bundle}")
        return custom_bundle
    if os.getenv("ENABLE_SSL_VERIFICATION", "false").lower() == "true":
        logger.warning("SSL verification enabled for Apple endpoint (may fail with self-signed certs)")
        return True
    logger.debug("SSL verification disabled for Apple internal endpoint (default)")
    return False
