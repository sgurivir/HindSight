#!/usr/bin/env python3
"""
Reproduces and verifies the fix for:
    "Context Collection: LLM returned a list with no valid context bundle inside"

ROOT CAUSE
----------
Stage 4a (context collection) fails with the above error because:
1. The LLM sometimes returns a JSON *array* as its final response instead of a
   JSON *object* that matches the context-bundle schema.
2. `run_iterative_analysis` in llm.py accepts ANY valid JSON (including arrays)
   as a terminal condition and immediately returns it.
3. `run_context_collection` in code_analysis.py then gets a list, finds no
   'primary_function' key inside it, logs the error and returns None.

FIX
---
`run_iterative_analysis` gains a `json_validator` callable parameter.
When the LLM returns JSON that fails the validator (e.g. a list when a dict is
required) the response is treated as "not yet valid" — the fallback guidance
is sent and the loop continues.

`run_context_collection` passes `json_validator=lambda p: isinstance(p, dict)`
so that any list response causes one more iteration (with the "no arrays, use
a dict" re-prompt already embedded in the existing `fallback_json_guidance`).

HOW TO RUN
----------
    python3 -m pytest dev/test/test_context_collection_list_bug.py -v

    # Or run directly:
    cd /path/to/Hindsight
    python3 dev/test/test_context_collection_list_bug.py

REPRO / FIX STRATEGY
---------------------
* test_repro_* tests assert the old (broken) behaviour and must pass BEFORE
  the fix is applied.  After the fix is applied they will FAIL, which confirms
  the fix changed the behaviour as intended.

* test_fix_* tests assert the new (correct) behaviour and must FAIL before the
  fix, then PASS after.
"""

import json
import os
import sys
import shutil
import tempfile
from typing import Any, Dict
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Make sure the project root is on sys.path when running directly
# ---------------------------------------------------------------------------
_project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from hindsight.core.llm.llm import Claude, ClaudeConfig
from hindsight.core.llm.code_analysis import CodeAnalysis, AnalysisConfig

# ---------------------------------------------------------------------------
# Shared fixtures / helpers
# ---------------------------------------------------------------------------

# Minimal function record (the json_data passed to run_context_collection)
SAMPLE_FUNCTION_DATA = {
    "function": "injectSamples:",
    "file": "apps/StudyApp/AlmanacWriterCommon/SensorWriter.m",
    "body": "- (void)injectSamples:(int) count { dispatch_async(self.queue, ^{ ... }); }",
    "start_line": 157,
    "end_line": 182,
}
SAMPLE_CHECKSUM = "a39f124e"

# JSON array the LLM sometimes returns instead of a dict — triggers the bug
_LIST_RESPONSE_OBJ = [
    {
        "function_name": "injectSamples:",
        "file": "apps/StudyApp/AlmanacWriterCommon/SensorWriter.m",
        "source": "- (void)injectSamples:(int) count { ... }",
        "callees": [],
        "callers": [],
    }
]
LIST_RESPONSE_STR = json.dumps(_LIST_RESPONSE_OBJ)

# The correct context bundle the LLM should return
VALID_BUNDLE = {
    "schema_version": "1.0",
    "primary_function": {
        "function_name": "injectSamples:",
        "class_name": "MotionAlarmSensorKitWriter",
        "file_path": "apps/StudyApp/AlmanacWriterCommon/SensorWriter.m",
        "file_name": "SensorWriter.m",
        "language": "objc",
        "start_line": 157,
        "end_line": 182,
        "source": "- (void)injectSamples:(int) count { dispatch_async(self.queue, ^{ ... }); }",
    },
    "callees": [],
    "callers": [],
    "data_types": [],
    "constants_and_globals": [],
    "knowledge_cache_hits": [],
    "collection_notes": [],
}
VALID_BUNDLE_STR = json.dumps(VALID_BUNDLE)


def _api_response(content: str) -> Dict[str, Any]:
    """Build a minimal API response dict that matches the AWS Bedrock shape."""
    return {
        "choices": [{"message": {"content": content}}],
        "usage": {
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "total_tokens": 150,
        },
    }


# ---------------------------------------------------------------------------
# Helper: build a Claude instance with a fully mocked provider
# ---------------------------------------------------------------------------

def _make_claude(provider_responses):
    """
    Create a real Claude instance whose provider is mocked.

    ``provider_responses`` is a list of dicts (or exceptions) that will be
    returned by the provider's ``make_request`` in order.
    """
    mock_provider = MagicMock()
    mock_provider.create_payload.return_value = {"mocked": True}
    mock_provider.make_request.side_effect = provider_responses

    with patch("hindsight.core.llm.llm.create_llm_provider", return_value=mock_provider):
        claude = Claude(
            ClaudeConfig(
                api_key="test-key",
                api_url="https://api.test.com",
                model="test-model",
                provider_type="aws_bedrock",
            )
        )
    # Keep a reference so tests can inspect calls
    claude._mock_provider = mock_provider
    return claude


# ---------------------------------------------------------------------------
# Helper: build a CodeAnalysis instance with all heavy dependencies mocked,
# but with a real Claude instance (whose provider is under our control).
# ---------------------------------------------------------------------------

class _CodeAnalysisFactory:
    """Context manager that creates a CodeAnalysis with controlled mocks."""

    def __init__(self, provider_responses, knowledge_store=None):
        self._provider_responses = provider_responses
        self._knowledge_store = knowledge_store
        self._temp_dir = None
        self._patches = []

    def __enter__(self):
        self._temp_dir = tempfile.mkdtemp()
        json_file = os.path.join(self._temp_dir, "input.json")
        with open(json_file, "w") as f:
            json.dump(SAMPLE_FUNCTION_DATA, f)

        # Mock the output-directory singleton
        mock_output = MagicMock()
        mock_output.get_custom_base_dir.return_value = self._temp_dir
        mock_output.get_repo_artifacts_dir.return_value = os.path.join(
            self._temp_dir, "artifacts"
        )

        # Create the mock provider once; we'll inject it into Claude
        self._mock_provider = MagicMock()
        self._mock_provider.create_payload.return_value = {"mocked": True}
        self._mock_provider.make_request.side_effect = list(self._provider_responses)

        # Start all patches
        patches = [
            patch(
                "hindsight.core.llm.code_analysis.get_output_directory_provider",
                return_value=mock_output,
            ),
            patch("hindsight.core.llm.code_analysis.Tools"),
            patch("hindsight.core.llm.code_analysis.RepoAstIndex"),
            patch(
                "hindsight.core.llm.llm.create_llm_provider",
                return_value=self._mock_provider,
            ),
            # TTLManager is imported inside run_iterative_analysis; patch at source
            patch(
                "hindsight.core.llm.ttl_manager.TTLManager.should_resend_system_prompt",
                return_value=True,
            ),
            patch(
                "hindsight.core.llm.ttl_manager.TTLManager.record_system_prompt_sent",
            ),
            # Avoid file-system writes for prompt logging
            patch.object(Claude, "log_complete_conversation"),
        ]
        for p in patches:
            p.start()
            self._patches.append(p)

        # Mock PromptBuilder to return minimal but syntactically correct prompts
        # so we don't need real AST data on disk
        self._prompt_patch = patch(
            "hindsight.core.llm.code_analysis.PromptBuilder.build_context_collection_prompt",
            return_value=("System prompt for context collection.", "Collect context for function."),
        )
        self._prompt_patch.start()
        self._patches.append(self._prompt_patch)

        cfg = AnalysisConfig(
            json_file_path=json_file,
            api_key="test-key",
            api_url="https://api.test.com",
            model="test-model",
            repo_path=self._temp_dir,
            output_file=os.path.join(self._temp_dir, "output.json"),
            config={"llm_provider_type": "aws_bedrock"},
            knowledge_store=self._knowledge_store,
        )
        self.code_analysis = CodeAnalysis(cfg)
        # Expose mock provider for assertions
        self.code_analysis._mock_provider = self._mock_provider
        return self.code_analysis

    def __exit__(self, *_):
        for p in reversed(self._patches):
            p.stop()
        if self._temp_dir and os.path.exists(self._temp_dir):
            shutil.rmtree(self._temp_dir)


# ===========================================================================
# SECTION 1 — Unit-level repro: run_iterative_analysis returns a list string
# ===========================================================================

class TestRunIterativeAnalysisReturnsList:
    """
    Lowest-level repro: confirm that *before* the fix, run_iterative_analysis
    blindly returns a JSON-array string when the LLM outputs one.
    """

    def test_repro_iterative_returns_list_string_when_llm_outputs_array(self):
        """
        REPRO (before fix): run_iterative_analysis returns the raw JSON-array
        string when the LLM's response contains a JSON array and there are no
        embedded tool calls.

        After the fix (json_validator added), this test must FAIL because
        run_iterative_analysis will no longer accept a list as a terminal value
        when json_validator=isinstance(p, dict) is passed.
        """
        claude = _make_claude([_api_response(LIST_RESPONSE_STR)])

        result = claude.run_iterative_analysis(
            system_prompt="Collect context.",
            user_prompt="Analyze function X.",
            max_iterations=1,
        )

        # Before fix: the array string is returned as-is
        assert result is not None
        parsed = json.loads(result)
        assert isinstance(parsed, list), (
            "Expected run_iterative_analysis to return the raw JSON array string "
            "(demonstrating the bug). Got: %r" % result
        )

    def test_fix_iterative_continues_when_llm_outputs_array_with_validator(self):
        """
        FIX (after fix): when json_validator=lambda p: isinstance(p, dict) is
        supplied, a JSON-array response does NOT satisfy the validator and the
        loop continues.  The second LLM call returns a valid dict, so the
        function ultimately returns the dict string.
        """
        claude = _make_claude(
            [
                _api_response(LIST_RESPONSE_STR),   # iteration 1 → array → validator fails → continue
                _api_response(VALID_BUNDLE_STR),    # iteration 2 → dict  → validator passes → return
            ]
        )

        result = claude.run_iterative_analysis(
            system_prompt="Collect context.",
            user_prompt="Analyze function X.",
            max_iterations=2,
            json_validator=lambda p: isinstance(p, dict),
            fallback_json_guidance=(
                "CRITICAL: You returned a JSON array. "
                "Return a JSON OBJECT (dict) starting with { and ending with }."
            ),
        )

        assert result is not None, "Expected a non-None result after retry with valid dict"
        parsed = json.loads(result)
        assert isinstance(parsed, dict), (
            "Expected run_iterative_analysis to return a dict string after retry. Got: %r" % result
        )
        assert parsed.get("schema_version") == "1.0"
        assert "primary_function" in parsed

    def test_fix_validator_accepts_dict_immediately(self):
        """
        FIX: when the LLM immediately returns a valid dict, json_validator
        passes on the first iteration with no retry needed.
        """
        claude = _make_claude([_api_response(VALID_BUNDLE_STR)])

        result = claude.run_iterative_analysis(
            system_prompt="Collect context.",
            user_prompt="Analyze function X.",
            max_iterations=2,
            json_validator=lambda p: isinstance(p, dict),
        )

        assert result is not None
        parsed = json.loads(result)
        assert isinstance(parsed, dict)
        assert "primary_function" in parsed

        # Exactly one LLM call was made (no retry needed)
        assert claude._mock_provider.make_request.call_count == 1


# ===========================================================================
# SECTION 2 — End-to-end repro: run_context_collection returns None
# ===========================================================================

class TestContextCollectionReturnsList:
    """
    End-to-end repro: run_context_collection returns None when the LLM
    outputs a JSON array, mimicking the real-world failure.
    """

    def test_repro_context_collection_returns_none_on_list_response(self):
        """
        REPRO (before fix): run_context_collection returns None when the LLM
        persistently returns a JSON array (3 responses, all arrays).

        After the fix the behaviour depends on retry count — if all retries
        exhaust without a valid dict, None is still returned.  So this test
        passes both before and after the fix (it tests the fallback path).
        """
        responses = [_api_response(LIST_RESPONSE_STR)] * 3

        with _CodeAnalysisFactory(responses) as ca:
            result = ca.run_context_collection(SAMPLE_FUNCTION_DATA, SAMPLE_CHECKSUM)

        assert result is None, (
            "Expected None when LLM consistently returns a JSON array, "
            "but got: %r" % result
        )

    def test_fix_context_collection_succeeds_after_list_then_dict(self):
        """
        FIX (after fix): run_context_collection retries when the LLM returns a
        JSON array and succeeds when the subsequent call returns the valid bundle.

        Before fix: result is None (loop exits immediately on list response).
        After fix:  result is the valid context bundle dict.
        """
        responses = [
            _api_response(LIST_RESPONSE_STR),   # call 1 → array → validator rejects → continue
            _api_response(VALID_BUNDLE_STR),    # call 2 → dict  → validator accepts → return
        ]

        with _CodeAnalysisFactory(responses) as ca:
            result = ca.run_context_collection(SAMPLE_FUNCTION_DATA, SAMPLE_CHECKSUM)

        assert result is not None, (
            "Expected a valid context bundle after retry, but got None. "
            "Did you apply the json_validator fix?"
        )
        assert isinstance(result, dict), "Expected dict, got %r" % type(result)
        assert "primary_function" in result, (
            "Context bundle missing 'primary_function' key: %r" % list(result.keys())
        )
        assert result["primary_function"]["function_name"] == "injectSamples:"

    def test_fix_context_collection_passes_dict_validator_on_first_try(self):
        """
        FIX: when LLM immediately returns a valid bundle, one LLM call is made.
        """
        responses = [_api_response(VALID_BUNDLE_STR)]

        with _CodeAnalysisFactory(responses) as ca:
            result = ca.run_context_collection(SAMPLE_FUNCTION_DATA, SAMPLE_CHECKSUM)

        assert result is not None
        assert isinstance(result, dict)
        assert "primary_function" in result
        assert ca._mock_provider.make_request.call_count == 1


# ===========================================================================
# Direct runner (so the file can be executed without pytest)
# ===========================================================================

if __name__ == "__main__":
    import traceback

    suites = [
        TestRunIterativeAnalysisReturnsList,
        TestContextCollectionReturnsList,
    ]

    passed = failed = 0
    for suite_cls in suites:
        suite = suite_cls()
        for name in [n for n in dir(suite_cls) if n.startswith("test_")]:
            method = getattr(suite, name)
            try:
                method()
                print(f"  PASS  {suite_cls.__name__}::{name}")
                passed += 1
            except Exception as exc:
                print(f"  FAIL  {suite_cls.__name__}::{name}")
                traceback.print_exc()
                failed += 1

    print(f"\n{'='*60}")
    print(f"Results: {passed} passed, {failed} failed")
    sys.exit(0 if failed == 0 else 1)
