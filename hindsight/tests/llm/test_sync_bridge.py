"""Tests for `SyncStageRunner` — sync→async bridge for legacy filter callsites."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

import pytest

from hindsight.llm import (
    AsyncLLMClient,
    LLMResponse,
    SyncStageRunner,
    make_client_config_from_dict,
    one_shot_json_sync,
    one_shot_text_sync,
    stage_trivial_filter,
)
from hindsight.llm.bedrock import LLMClientConfig


class _ScriptedClient(AsyncLLMClient):
    """Test double that returns canned text in order. Skips httpx setup."""

    def __init__(self, responses: List[str]):
        self.config = LLMClientConfig(api_url="http://fake", model="claude-sonnet-4-5", max_tokens=1000)
        self._responses = list(responses)
        self.sends: List[Dict[str, Any]] = []
        # Sentinel so we can assert that the bridge actually built a client.
        self.opened = False
        self.closed = False

    async def __aenter__(self) -> "_ScriptedClient":
        self.opened = True
        return self

    async def __aexit__(self, *_a, **_k) -> None:
        self.closed = True

    async def send(self, system_prompt, messages, *, enable_system_cache=True, cache_ttl="1h") -> LLMResponse:
        self.sends.append({"system": system_prompt, "messages": messages})
        text = self._responses.pop(0) if self._responses else "{}"
        return LLMResponse(
            text=text,
            input_tokens=10,
            output_tokens=5,
            raw={"choices": [{"message": {"content": text}}], "usage": {"input_tokens": 10, "output_tokens": 5}},
        )

    async def aclose(self) -> None:
        self.closed = True


def test_make_client_config_from_dict_uses_defaults():
    cfg = make_client_config_from_dict(
        api_key="K",
        config={},
        default_api_url="http://default",
        default_model="default-model",
        default_max_tokens=42,
    )
    assert cfg.api_key == "K"
    assert cfg.api_url == "http://default"
    assert cfg.model == "default-model"
    assert cfg.max_tokens == 42


def test_make_client_config_from_dict_uses_config_overrides():
    cfg = make_client_config_from_dict(
        api_key="K",
        config={"api_end_point": "http://x", "model": "m", "max_tokens": 99},
        default_api_url="http://default",
        default_model="default-model",
        default_max_tokens=42,
    )
    assert cfg.api_url == "http://x"
    assert cfg.model == "m"
    assert cfg.max_tokens == 99


def test_sync_stage_runner_runs_single_verdict(monkeypatch):
    """End-to-end: SyncStageRunner.run executes one stage call and parses verdict."""
    fake = _ScriptedClient(['{"result": true, "reason": "test"}'])

    # Patch the AsyncLLMClient constructor so SyncStageRunner picks up our fake.
    import hindsight.llm.sync_bridge as sb

    monkeypatch.setattr(sb, "AsyncLLMClient", lambda _cfg: fake)

    cfg = LLMClientConfig(api_url="http://fake", model="m", max_tokens=100, api_key="K")
    runner = SyncStageRunner(cfg)
    verdict = runner.run(stage_trivial_filter("SYS", max_iterations=2), "user message")
    assert verdict == {"result": True, "reason": "test"}
    # Client lifecycle is symmetric.
    assert fake.opened is True
    assert fake.closed is True


def test_sync_stage_runner_run_many_returns_one_per_prompt(monkeypatch):
    fake = _ScriptedClient([
        '{"result": false}',
        '{"result": true}',
    ])

    import hindsight.llm.sync_bridge as sb

    monkeypatch.setattr(sb, "AsyncLLMClient", lambda _cfg: fake)

    cfg = LLMClientConfig(api_url="http://fake", model="m", max_tokens=100, api_key="K")
    runner = SyncStageRunner(cfg)
    verdicts = runner.run_many(
        stage_trivial_filter("SYS", max_iterations=2),
        ["issue 1", "issue 2"],
        max_iterations=2,
    )
    assert verdicts == [{"result": False}, {"result": True}]


def test_one_shot_text_sync_returns_assistant_text(monkeypatch):
    fake = _ScriptedClient(["hello world"])

    import hindsight.llm.sync_bridge as sb

    monkeypatch.setattr(sb, "AsyncLLMClient", lambda _cfg: fake)

    cfg = LLMClientConfig(api_url="http://fake", model="m", max_tokens=100, api_key="K")
    text = one_shot_text_sync(cfg, system_prompt="SYS", user_prompt="hi")
    assert text == "hello world"
    assert fake.opened and fake.closed


def test_one_shot_json_sync_parses_json(monkeypatch):
    fake = _ScriptedClient(['{"answer": 42, "ok": true}'])

    import hindsight.llm.sync_bridge as sb

    monkeypatch.setattr(sb, "AsyncLLMClient", lambda _cfg: fake)

    cfg = LLMClientConfig(api_url="http://fake", model="m", max_tokens=100, api_key="K")
    parsed = one_shot_json_sync(cfg, system_prompt="SYS", user_prompt="give me a fact")
    assert parsed == {"answer": 42, "ok": True}


def test_one_shot_json_sync_strips_markdown_fences(monkeypatch):
    fake = _ScriptedClient(['```json\n[1, 2, 3]\n```'])

    import hindsight.llm.sync_bridge as sb

    monkeypatch.setattr(sb, "AsyncLLMClient", lambda _cfg: fake)

    cfg = LLMClientConfig(api_url="http://fake", model="m", max_tokens=100, api_key="K")
    parsed = one_shot_json_sync(cfg, system_prompt="SYS", user_prompt="list")
    assert parsed == [1, 2, 3]
