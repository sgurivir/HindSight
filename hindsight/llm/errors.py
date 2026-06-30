"""Typed errors for the async LLM stack.

Replaces the dict-shaped `{"error": "...", "status_code": ...}` returns from the
old `AWSBedrockProvider.make_request`. Callers either get a successful
`LLMResponse` or one of these exceptions — never a sentinel dict.
"""

from __future__ import annotations


class LLMError(Exception):
    """Base for all LLM-stack errors."""


class LLMTokenLimitExceeded(LLMError):
    """The combined system + user prompt exceeds the model's input budget.

    Raised by the client before any HTTP call. The orchestrator typically logs
    this and skips the function; it is not retriable.
    """


class LLMTransientError(LLMError):
    """A retriable failure that exhausted the retry budget.

    Examples: HTTP 429, HTTP 5xx, request timeouts, connection resets.
    The client tries `DEFAULT_RETRY_DELAYS` before raising this; orchestrators
    treat it as a function-level failure.
    """

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class LLMFatalError(LLMError):
    """A non-retriable API error (HTTP 400, malformed payload, auth failure).

    The client does not retry these. Orchestrators treat as a function-level
    failure but should surface them prominently — they typically indicate a
    bug in prompt construction or configuration.
    """

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class LLMResponseShapeError(LLMError):
    """The API returned 200 but the response body was missing expected fields.

    Raised when `choices[0].message.content` (Bedrock-style) or `content[*].text`
    (Anthropic-style) cannot be located. Indicates an upstream contract change.
    """
