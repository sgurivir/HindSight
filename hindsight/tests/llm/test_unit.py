"""Unit tests for hindsight.llm — pure-Python pieces, no network.

Covers: JSON extraction, tool-protocol parsing, conversation state, rate
limiter, and every StageSpec factory's extract/validate/fallback behavior.
"""

from __future__ import annotations

import asyncio
import json
import time

import pytest

from hindsight.llm import (
    AsyncRateLimiter,
    ConversationState,
    extract_tool_requests,
    find_all_json_arrays,
    find_all_json_objects,
    stage_4a_context_collection,
    stage_4b_analysis,
    stage_call_tree_code,
    stage_call_tree_diff,
    stage_da_diff_context,
    stage_db_diff_analysis,
    stage_perf_analysis,
    stage_perf_context,
    stage_response_challenger,
    stage_ta_trace_context,
    stage_tb_trace_analysis,
    stage_tc_trace_validator,
    stage_trivial_filter,
)


# ----------------------------------------------------------------------
# json_extract
# ----------------------------------------------------------------------


def test_find_all_json_objects_largest_first_with_nested():
    text = 'prelude {"a": 1, "b": {"c": "hi"}} middle {"x": 2}'
    objs = find_all_json_objects(text)
    assert objs[0] == '{"a": 1, "b": {"c": "hi"}}'
    assert '{"x": 2}' in objs
    assert '{"c": "hi"}' in objs


def test_find_all_json_objects_handles_braces_inside_strings():
    text = '{"k": "value with } and { inside"}'
    assert find_all_json_objects(text) == [text]


def test_find_all_json_arrays_basic():
    arrs = find_all_json_arrays('prelude [1, 2, 3] middle [{"a": 1}]')
    assert "[1, 2, 3]" in arrs
    assert '[{"a": 1}]' in arrs


# ----------------------------------------------------------------------
# tool_protocol
# ----------------------------------------------------------------------


def test_extract_tool_requests_fenced_and_bare():
    text = """
    ```json
    {"tool": "readFile", "path": "src/foo.py", "reason": "explore"}
    ```
    Then {"tool": "checkFileSize", "path": "src/bar.py"}.
    """
    calls = extract_tool_requests(text)
    assert len(calls) == 2
    assert calls[0].name == "readFile"
    assert calls[0].args == {"path": "src/foo.py", "reason": "explore"}
    assert calls[1].name == "checkFileSize"


def test_extract_tool_requests_returns_empty_when_no_tool_key():
    assert extract_tool_requests("just prose, no JSON") == []
    assert extract_tool_requests('{"foo": "bar"}') == []  # no "tool" key


# ----------------------------------------------------------------------
# conversation
# ----------------------------------------------------------------------


def test_conversation_state_tool_result_format():
    s = ConversationState()
    s.add_user("hello")
    s.add_assistant("hi")
    s.add_tool_result("tool_abc", "result body")
    payload = s.as_payload()
    assert payload[-1]["role"] == "user"
    assert payload[-1]["content"].startswith("[TOOL_RESULT: tool_abc]")
    assert "result body" in payload[-1]["content"]


def test_conversation_state_last_assistant_text():
    s = ConversationState()
    s.add_assistant("first")
    s.add_user("u")
    s.add_assistant("second")
    assert s.last_assistant_text() == "second"


# ----------------------------------------------------------------------
# rate_limit
# ----------------------------------------------------------------------


def test_rate_limiter_gates_third_request():
    async def _run():
        rl = AsyncRateLimiter(max_requests=2, window_seconds=0.5)
        t0 = time.monotonic()
        await rl.acquire()
        await rl.acquire()
        await rl.acquire()  # must wait ~0.5s
        return time.monotonic() - t0

    elapsed = asyncio.run(_run())
    assert elapsed >= 0.45, f"rate limiter did not gate; elapsed={elapsed:.3f}"


def test_rate_limiter_rejects_invalid_config():
    with pytest.raises(ValueError):
        AsyncRateLimiter(max_requests=0, window_seconds=1)
    with pytest.raises(ValueError):
        AsyncRateLimiter(max_requests=1, window_seconds=0)


# ----------------------------------------------------------------------
# Stage factories
# ----------------------------------------------------------------------


def test_stage_4a_extract_primary_function():
    stage = stage_4a_context_collection("SYS")
    extracted = stage.extract_json(
        'prelude {"primary_function": {"name": "f"}, "callees": []} trailing'
    )
    assert extracted is not None
    parsed = json.loads(extracted)
    assert parsed["primary_function"]["name"] == "f"
    assert stage.validate_json(parsed) is True


def test_stage_4a_unwraps_array_wrapped_bundle():
    stage = stage_4a_context_collection("SYS")
    extracted = stage.extract_json('[{"primary_function": {"name": "f"}}]')
    assert extracted is not None
    assert "primary_function" in json.loads(extracted)


def test_stage_4a_returns_none_when_missing_key():
    stage = stage_4a_context_collection("SYS")
    assert stage.extract_json('{"foo": "bar"}') is None


def test_stage_4b_handles_empty_array():
    stage = stage_4b_analysis("SYS")
    assert stage.extract_json("nothing []") == "[]"
    assert stage.validate_json([]) is True


def test_stage_4b_extracts_dict_items_from_mixed_array():
    stage = stage_4b_analysis("SYS")
    extracted = stage.extract_json('["note1", {"issue": "x"}, "note2"]')
    assert json.loads(extracted) == [{"issue": "x"}]


def test_stage_4b_fallback_to_results_key():
    stage = stage_4b_analysis("SYS")
    extracted = stage.extract_json('{"results": [{"issue": "z"}]}')
    assert json.loads(extracted) == [{"issue": "z"}]


def test_stage_da_accepts_either_key():
    stage = stage_da_diff_context("SYS")
    assert stage.validate_json({"changed_functions": []}) is True
    assert stage.validate_json({"primary_function": {}}) is True
    assert stage.validate_json({"foo": "bar"}) is False


def test_stage_db_validates_issue_array():
    stage = stage_db_diff_analysis("SYS")
    assert stage.validate_json([{"issue": "x"}]) is True
    assert stage.validate_json([]) is True
    assert stage.validate_json(["not a dict"]) is False
    assert stage.validate_json("not a list") is False


def test_stage_response_challenger_normalizes_aliases():
    stage = stage_response_challenger("SYS")
    assert json.loads(stage.extract_json('{"is_trivial": true}'))["result"] is True
    assert json.loads(stage.extract_json('{"should_filter": false}'))["result"] is False
    assert json.loads(stage.extract_json('{"result": true, "reason": "ok"}'))["result"] is True


def test_stage_trivial_filter_normalizes_aliases():
    stage = stage_trivial_filter("SYS")
    assert json.loads(stage.extract_json('{"trivial": true}'))["result"] is True
    assert json.loads(stage.extract_json('{"is_trivial": false}'))["result"] is False
    assert json.loads(stage.extract_json('{"result": true}'))["result"] is True


def test_stage_perf_context_accepts_either_key():
    stage = stage_perf_context("SYS")
    assert stage.validate_json({"functions": {}}) is True
    assert stage.validate_json({"call_path": []}) is True


def test_stage_tc_validator_requires_bool_valid():
    stage = stage_tc_trace_validator("SYS")
    assert stage.validate_json({"valid": True}) is True
    assert stage.validate_json({"valid": "yes"}) is False
    assert stage.validate_json({"foo": "bar"}) is False


@pytest.mark.parametrize(
    "factory",
    [
        stage_4a_context_collection,
        stage_4b_analysis,
        stage_da_diff_context,
        stage_db_diff_analysis,
        stage_ta_trace_context,
        stage_tb_trace_analysis,
        stage_tc_trace_validator,
        stage_perf_context,
        stage_perf_analysis,
        stage_trivial_filter,
        stage_response_challenger,
        stage_call_tree_code,
        stage_call_tree_diff,
    ],
)
def test_every_stage_has_fallback_guidance(factory):
    spec = factory("SYS")
    msg = spec.fallback_guidance(None)
    assert isinstance(msg, str) and len(msg) > 50
    msg2 = spec.fallback_guidance("got a list, expected an object")
    assert "got a list, expected an object" in msg2
