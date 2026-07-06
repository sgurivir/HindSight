"""Async LLM stack for Hindsight.

Replaces `hindsight/core/llm/` at the end of the migration. Step 1 has landed
the modules but no orchestration consumes them yet — that's Step 3+.

Public surface:

    AsyncLLMClient, LLMClientConfig, LLMResponse  — async HTTP + token usage
    LLMError, LLMTokenLimitExceeded,              — typed errors
        LLMTransientError, LLMFatalError,
        LLMResponseShapeError
    AsyncRateLimiter                              — per-pipeline rate limiting
    ConversationLogger                            — instance-scoped markdown logger
    ConversationState                             — per-stage message history
    PromptBuilder                                 — facade over core.prompts
    IterativeRunner, IterationOutcome             — async tool-using LLM loop
    StageSpec, stage_*                            — 11 stage factories
    ToolCall, extract_tool_requests               — JSON-embedded tool protocol
    one_shot_text, one_shot_json                  — single-call helpers

`PromptBuilder` is intentionally NOT re-exported at the package level: the
legacy `hindsight/core/prompts/` package has a circular import with the legacy
`hindsight/core/llm/` package, and eagerly loading it here would force that
cycle to resolve through our new package. Pipelines that need prompts should
`from hindsight.llm.prompts import PromptBuilder` directly — by that point
the legacy stack has already initialized.
"""

from .callsite import one_shot_json, one_shot_text
from .client import AsyncLLMClient, DEFAULT_RETRY_DELAYS, LLMResponse
from .bedrock import LLMClientConfig, build_payload, check_token_limit, estimate_tokens
from .conversation import ConversationState, Message
from .errors import (
    LLMError,
    LLMFatalError,
    LLMResponseShapeError,
    LLMTokenLimitExceeded,
    LLMTransientError,
)
from .iterate import IterativeRunner, IterationOutcome, ToolExecutor
from .json_extract import find_all_json_arrays, find_all_json_objects
from .logger import ConversationLogger
from .rate_limit import AsyncRateLimiter
from .stages import (
    StageSpec,
    stage_4a_context_collection,
    stage_4b_analysis,
    stage_call_tree_code,
    stage_call_tree_context,
    stage_call_tree_diff,
    stage_da_diff_context,
    stage_db_diff_analysis,
    stage_file_summary,
    stage_perf_analysis,
    stage_perf_context,
    stage_response_challenger,
    stage_ta_trace_context,
    stage_tb_trace_analysis,
    stage_tc_trace_validator,
    stage_trivial_filter,
)
from .sync_bridge import (
    SyncStageRunner,
    make_client_config_from_dict,
    one_shot_json_sync,
    one_shot_text_sync,
)
from .tool_protocol import ToolCall, extract_tool_requests, make_legacy_tool_use_block

__all__ = [
    "AsyncLLMClient",
    "AsyncRateLimiter",
    "ConversationLogger",
    "ConversationState",
    "DEFAULT_RETRY_DELAYS",
    "IterationOutcome",
    "IterativeRunner",
    "LLMClientConfig",
    "LLMError",
    "LLMFatalError",
    "LLMResponse",
    "LLMResponseShapeError",
    "LLMTokenLimitExceeded",
    "LLMTransientError",
    "Message",
    "StageSpec",
    "SyncStageRunner",
    "ToolCall",
    "ToolExecutor",
    "build_payload",
    "check_token_limit",
    "estimate_tokens",
    "extract_tool_requests",
    "find_all_json_arrays",
    "find_all_json_objects",
    "make_client_config_from_dict",
    "make_legacy_tool_use_block",
    "one_shot_json",
    "one_shot_json_sync",
    "one_shot_text",
    "one_shot_text_sync",
    "stage_4a_context_collection",
    "stage_4b_analysis",
    "stage_call_tree_code",
    "stage_call_tree_context",
    "stage_call_tree_diff",
    "stage_da_diff_context",
    "stage_db_diff_analysis",
    "stage_file_summary",
    "stage_perf_analysis",
    "stage_perf_context",
    "stage_response_challenger",
    "stage_ta_trace_context",
    "stage_tb_trace_analysis",
    "stage_tc_trace_validator",
    "stage_trivial_filter",
]
