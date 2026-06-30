"""Integration tests for IterativeRunner driven by a fake LLM client.

Verifies the runner end-to-end: tool dispatch, concurrent multi-tool turns,
fallback-guidance retry, and max-iterations termination.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List, Optional

import pytest

from hindsight.llm import (
    AsyncLLMClient,
    IterativeRunner,
    LLMResponse,
    ToolCall,
    stage_4b_analysis,
)
from hindsight.llm.bedrock import LLMClientConfig


class FakeClient(AsyncLLMClient):
    """LLM client stub: returns canned text in order, records every send."""

    def __init__(self, responses: List[str]):
        # Skip the real __init__ — we don't want an httpx client.
        self.config = LLMClientConfig(
            api_url="http://fake", model="claude-sonnet-4-5", max_tokens=64000
        )
        self._responses = list(responses)
        self.sends: list[Dict[str, Any]] = []

    async def send(
        self,
        system_prompt: Optional[str],
        messages: List[Dict[str, Any]],
        *,
        enable_system_cache: bool = True,
        cache_ttl: str = "1h",
    ) -> LLMResponse:
        self.sends.append({"system": system_prompt, "messages": messages})
        if not self._responses:
            raise RuntimeError("FakeClient out of responses")
        text = self._responses.pop(0)
        return LLMResponse(
            text=text,
            input_tokens=10,
            output_tokens=5,
            raw={
                "choices": [{"message": {"content": text}}],
                "usage": {"input_tokens": 10, "output_tokens": 5},
            },
        )

    async def aclose(self) -> None:
        pass


class FakeTools:
    def __init__(self, results: Dict[str, str]):
        self.results = results
        self.calls: list[ToolCall] = []

    async def execute(self, call: ToolCall, *, allowed) -> str:
        self.calls.append(call)
        if call.name not in allowed:
            return f"Error: tool '{call.name}' not in allowed set"
        return self.results.get(call.name, "")


@pytest.mark.asyncio
async def test_runner_happy_path():
    responses = [
        '```json\n{"tool": "readFile", "path": "src/foo.py", "reason": "explore"}\n```',
        '[{"file_path": "src/foo.py", "function_name": "foo", "issue": "x", "severity": "high"}]',
    ]
    client = FakeClient(responses)
    tools = FakeTools({"readFile": "fake content"})
    runner = IterativeRunner(client)

    outcome = await runner.run(
        stage_4b_analysis("sys"),
        user_prompt="Analyze",
        tools=tools,
    )

    assert outcome.error is None
    parsed = json.loads(outcome.text)
    assert parsed == [{"file_path": "src/foo.py", "function_name": "foo", "issue": "x", "severity": "high"}]
    assert outcome.iterations == 2
    assert outcome.input_tokens == 20
    assert outcome.output_tokens == 10
    assert len(tools.calls) == 1


@pytest.mark.asyncio
async def test_runner_dispatches_multiple_tools_in_one_turn():
    """The LLM emits 3 tool requests in one response → all 3 are dispatched."""
    responses = [
        '```json\n{"tool": "readFile", "path": "a.py", "reason": "x"}\n```\n'
        '```json\n{"tool": "readFile", "path": "b.py", "reason": "y"}\n```\n'
        '```json\n{"tool": "checkFileSize", "path": "c.py", "reason": "z"}\n```',
        "[]",
    ]
    client = FakeClient(responses)
    tools = FakeTools({"readFile": "content"})
    runner = IterativeRunner(client)

    outcome = await runner.run(
        stage_4b_analysis("sys"),
        user_prompt="analyze",
        tools=tools,
    )

    assert json.loads(outcome.text) == []
    # All 3 calls attempted (checkFileSize will be rejected by allowed-set check).
    assert len(tools.calls) == 3


@pytest.mark.asyncio
async def test_runner_falls_back_and_retries_on_invalid_json():
    responses = ["Just prose, no JSON at all.", "[]"]
    client = FakeClient(responses)
    tools = FakeTools({})
    runner = IterativeRunner(client)

    outcome = await runner.run(stage_4b_analysis("sys"), user_prompt="analyze", tools=tools)
    assert outcome.text == "[]"
    assert outcome.iterations == 2
    # The second send must include the fallback guidance somewhere.
    second_user_msgs = [m for m in client.sends[1]["messages"] if m["role"] == "user"]
    assert any("did not contain a valid issues array" in m["content"] for m in second_user_msgs)


@pytest.mark.asyncio
async def test_runner_returns_last_text_on_max_iterations():
    responses = ["nope 1", "nope 2"]
    client = FakeClient(responses)
    tools = FakeTools({})
    runner = IterativeRunner(client)

    outcome = await runner.run(
        stage_4b_analysis("sys"),
        user_prompt="analyze",
        tools=tools,
        max_iterations=2,
    )
    assert outcome.iterations == 2
    assert outcome.text == "nope 2"
