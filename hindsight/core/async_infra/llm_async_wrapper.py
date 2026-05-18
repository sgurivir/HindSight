"""
Wraps a synchronous LLM provider call in asyncio.run_in_executor.

Extracted from the async_llm_request pattern in data_flow_analyzer.py,
which wraps provider.create_payload() + provider.make_request() into an
async function suitable for use with the worker pool.

Usage:
    from hindsight.core.llm.llm import create_llm_provider, ClaudeConfig
    provider = create_llm_provider(config)
    async_fn = create_async_llm_fn(provider, system_prompt="You are...")
    response = await async_fn(messages)
"""

import asyncio
from typing import Awaitable, Callable, Dict, List

from ...utils.log_util import get_logger

logger = get_logger(__name__)


def create_async_llm_fn(
    provider,
    system_prompt: str,
) -> Callable[[List[Dict]], Awaitable[str]]:
    """
    Create an async function that wraps a synchronous LLM provider call.

    The returned function accepts a list of message dicts and returns the
    LLM response text. It prepends the system prompt as a system message,
    builds the request payload, and executes the synchronous provider call
    in a thread pool via loop.run_in_executor(None, ...).

    This matches the pattern used in data_flow_analyzer.py:
        async def async_llm_request(system_prompt, messages):
            loop = asyncio.get_event_loop()
            def _sync_call():
                full_messages = [{"role": "system", "content": system_prompt}] + messages
                payload = provider.create_payload(full_messages, stream=False)
                response = provider.make_request(payload)
                ...
            return await loop.run_in_executor(None, _sync_call)

    Args:
        provider: An LLM provider instance with create_payload() and
            make_request() methods (e.g., AWSBedrockProvider, ClaudeProvider).
        system_prompt: The system prompt to prepend to every request.

    Returns:
        An async function with signature:
            async def fn(messages: List[Dict[str, str]]) -> str
        where messages is a list of {"role": ..., "content": ...} dicts.
        Returns the response text, or empty string on error.
    """

    async def _async_llm_fn(messages: List[Dict]) -> str:
        """Async wrapper around synchronous provider.make_request()."""
        loop = asyncio.get_running_loop()

        def _sync_call() -> str:
            full_messages = [{"role": "system", "content": system_prompt}] + messages
            payload = provider.create_payload(full_messages, stream=False)
            response = provider.make_request(payload)

            if response is None:
                logger.debug("LLM provider returned None")
                return ""
            if "error" in response:
                logger.debug(f"LLM provider returned error: {response['error']}")
                return ""
            choices = response.get("choices", [])
            if not choices:
                logger.debug("LLM provider returned no choices")
                return ""
            return choices[0].get("message", {}).get("content", "")

        return await loop.run_in_executor(None, _sync_call)

    return _async_llm_fn
