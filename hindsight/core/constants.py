#!/usr/bin/env python3
# Created by Sridhar Gurivireddy
"""
Core constants used across the Hindsight analysis system
"""

# Common constants for analysis
DEFAULT_MAX_TOKENS = 64000
LLM_PROVIDER_RATE_LIMIT = 40  # Maximum LLM requests per window across all analyzers
LLM_PROVIDER_RATE_WINDOW_SECONDS = 240  # Rate limit window in seconds (4 minutes)
DEFAULT_API_RATE_LIMIT = LLM_PROVIDER_RATE_LIMIT  # Legacy alias
DEFAULT_RATE_LIMIT_WINDOW = LLM_PROVIDER_RATE_WINDOW_SECONDS  # Legacy alias
DEFAULT_LOGS_DIR = "logs"
DEFAULT_DIFF_DAYS = 21  # Default number of days to look back for recently modified files
MAX_TOOL_ITERATIONS = 20  # Maximum iterations for LLM tool usage
SOFT_REMINDER_ITERATION = 16  # Iteration at which to send soft reminder to generate output
RESPONSE_CHALLENGER_MAX_ITERATIONS = 12  # Maximum iterations for response challenger tool usage
MAX_FILE_CHARACTERS_FOR_READ_FILE = 30000  # Maximum characters for read_file tool before pruning
MAX_CHARACTERS_PER_DIFF_ANALYSIS = 20000  # Maximum characters per diff analysis conversation

# LLM filtering constants
LLM_FILTER_BATCH_SIZE = 20  # Number of issues to process in a single LLM batch for trivial filtering

# Function analysis constants
MIN_FUNCTION_BODY_LENGTH = 7  # Minimum lines for a function to be analyzed
MAX_FUNCTION_BODY_LENGTH = 1000  # Maximum lines for a function to be analyzed
DEFAULT_NUM_FUNCTIONS_TO_ANALYZE = 10  # Default number of functions to analyze
DEFAULT_NUM_BLOCKS_TO_ANALYZE = 10  # Default number of blocks to analyze for diff analysis
MAX_FILES_PER_DIFF_CHUNK = 8  # Maximum number of files per diff analysis chunk
MAX_SUPPORTED_FILE_COUNT = 14000  # Maximum number of files with supported extensions to analyze
CANCELLATION_CHECK_INTERVAL = 1  # Check for cancellation every N functions during analysis

# External input analysis constants
EXTERNAL_INPUT_RATE_LIMIT = LLM_PROVIDER_RATE_LIMIT  # Legacy alias
EXTERNAL_INPUT_DEFAULT_WORKERS = 3  # Default parallel workers for external input analysis
EXTERNAL_INPUT_MAX_TOOL_ITERATIONS = 10  # Max tool iterations per function analysis
EXTERNAL_INPUT_BATCH_SIZE = 8  # Functions per batch in batched analysis mode
EXTERNAL_INPUT_TOKEN_BUDGET_RATIO = 0.5  # Use at most 50% of context window for input (leaves room for output)
EXTERNAL_INPUT_CHARS_PER_TOKEN = 4  # Approximate chars per token for budget estimation

# Code analysis parallel worker constants
CODE_ANALYZER_DEFAULT_WORKERS = 3  # Default parallel workers for code analysis
CODE_ANALYZER_RATE_LIMIT = LLM_PROVIDER_RATE_LIMIT  # Legacy alias
CODE_ANALYZER_RATE_WINDOW_SECONDS = LLM_PROVIDER_RATE_WINDOW_SECONDS  # Legacy alias

# Sink analysis constants (mirrors external input analysis)
SINK_ANALYSIS_RATE_LIMIT = LLM_PROVIDER_RATE_LIMIT  # Legacy alias
SINK_ANALYSIS_RATE_WINDOW_SECONDS = LLM_PROVIDER_RATE_WINDOW_SECONDS  # Legacy alias
SINK_ANALYSIS_DEFAULT_WORKERS = 3  # Default parallel workers for sink analysis
SINK_ANALYSIS_MAX_TOOL_ITERATIONS = 10  # Max tool iterations per function analysis
SINK_ANALYSIS_BATCH_SIZE = 8  # Functions per batch in batched analysis mode
SINK_ANALYSIS_TOKEN_BUDGET_RATIO = 0.5  # Use at most 50% of context window for input
SINK_ANALYSIS_CHARS_PER_TOKEN = 4  # Approximate chars per token for budget estimation

# Diff analyzer constants (async worker pool)
DIFF_ANALYZER_DEFAULT_WORKERS = 3  # Default parallel workers for diff analysis
DIFF_ANALYZER_RATE_LIMIT = LLM_PROVIDER_RATE_LIMIT  # Legacy alias
DIFF_ANALYZER_RATE_WINDOW_SECONDS = LLM_PROVIDER_RATE_WINDOW_SECONDS  # Legacy alias

# Trace analysis constants
TRACE_ANALYZER_DEFAULT_WORKERS = 3  # Default parallel workers for trace analysis
TRACE_ANALYZER_RATE_LIMIT = LLM_PROVIDER_RATE_LIMIT  # Legacy alias
TRACE_ANALYZER_RATE_WINDOW_SECONDS = LLM_PROVIDER_RATE_WINDOW_SECONDS  # Legacy alias

# Path discovery constants
PATH_DISCOVERY_MAX_DEPTH = 10  # Maximum hops from source to sink
PATH_DISCOVERY_MAX_PATHS_PER_PAIR = 3  # Maximum paths per (source, sink) pair
PATH_DISCOVERY_MAX_TOTAL_PATHS = 5000  # Hard cap on total candidate paths

# Flow vulnerability analysis constants (CWE anti-pattern detection)
FLOW_VULN_RATE_LIMIT = 40  # Maximum LLM requests per minute
FLOW_VULN_DEFAULT_WORKERS = 3  # Default parallel workers
FLOW_VULN_MAX_TOOL_ITERATIONS = 15  # Max tool iterations per flow analysis
FLOW_VULN_MAX_FLOWS_TO_ANALYZE = 200  # Hard cap on flows to analyze (prioritize shorter paths)

# AST generation constants
AST_GENERATION_TIMEOUT_SECS = 3600  # Timeout for AST generation subprocess (1 hour)

# AST parallel processing configuration
AST_DEFAULT_PARALLEL_ENABLED = True  # Enable parallel processing by default
AST_DEFAULT_MAX_WORKERS = 4  # Default number of worker processes
AST_MIN_FILES_FOR_PARALLEL = 10  # Minimum files required to use parallel processing

# Directory constants
REPO_IQ_ARTIFACTS_DIR = "llm_artifacts"

# Constants for AST and analysis files
DEFINED_FUNCTIONS_FILE = "merged_functions.json"
NESTED_CALL_GRAPH_FILE = "merged_call_graph.json"
MERGED_SYMBOLS_FILE = "merged_functions.json"
MERGED_DEFINED_CLASSES_FILE = "merged_defined_classes.json"

# C/C++ files (tree-sitter based - CPPASTUtil)
CPP_DEFINED_FUNCTIONS_FILE = "cpp_defined_functions.json"
CPP_NESTED_CALL_GRAPH_FILE = "cpp_nested_call_graph.json"
CPP_DEFINED_CLASSES_FILE = "cpp_defined_classes.json"
CPP_DEFINED_CONSTANTS_FILE = "cpp_defined_constants.json"

# Objective-C/C++ files (libclang based - CASTUtil)
# Note: C_DEFINED_FUNCTIONS_FILE kept for backward compatibility, maps to CLANG_*
C_DEFINED_FUNCTIONS_FILE = "clang_defined_functions.json"
CLANG_DEFINED_FUNCTIONS_FILE = "clang_defined_functions.json"
CLANG_NESTED_CALL_GRAPH_FILE = "clang_nested_call_graph.json"
CLANG_DEFINED_CLASSES_FILE = "clang_defined_classes.json"
CLANG_DEFINED_CONSTANTS_FILE = "clang_defined_constants.json"

# Swift files
SWIFT_DEFINED_FUNCTIONS_FILE = "swift_defined_functions.json"
SWIFT_CALL_GRAPH_FILE = "swift_call_graph.json"
SWIFT_DEFINED_CLASSES_FILE = "swift_defined_classes.json"

# Kotlin files
KOTLIN_DEFINED_FUNCTIONS_FILE = "kotlin_defined_functions.json"
KOTLIN_CALL_GRAPH_FILE = "kotlin_call_graph.json"
KOTLIN_DEFINED_CLASSES_FILE = "kotlin_defined_classes.json"

# Java files
JAVA_DEFINED_FUNCTIONS_FILE = "java_defined_functions.json"
JAVA_CALL_GRAPH_FILE = "java_call_graph.json"
JAVA_DEFINED_CLASSES_FILE = "java_defined_classes.json"

# Go files
GO_DEFINED_FUNCTIONS_FILE = "go_defined_functions.json"
GO_CALL_GRAPH_FILE = "go_call_graph.json"
GO_DEFINED_CLASSES_FILE = "go_defined_classes.json"

# JavaScript/TypeScript files
JS_TS_DEFINED_FUNCTIONS_FILE = "js_ts_functions.json"
JS_TS_CALL_GRAPH_FILE = "js_ts_nested_callgraph.json"
JS_TS_DEFINED_CLASSES_FILE = "js_ts_defined_classes.json"
PROCESSED_AST_CACHE_FILE = "processed_AST_cache.json"
PROCESSED_OUTPUT_DIR = "analysis_input"

# Call tree files (generated from call graph)
CALL_TREE_JSON_FILE = "call_tree.json"
CALL_TREE_TEXT_FILE = "call_tree.txt"

# Call tree context section constants (for LLM prompts)
CALL_TREE_MAX_ANCESTOR_DEPTH = 6      # 6 levels of callers (ancestor chain)
CALL_TREE_MAX_DESCENDANT_DEPTH = 6    # 6 levels of callees to show
CALL_TREE_MAX_CHILDREN_PER_NODE = 5   # Max children per node
CALL_TREE_MAX_TOKENS = 3000           # Max tokens for call tree section
CALL_TREE_ENABLED = True              # Feature flag for call tree context

DEFAULT_LLM_API_END_POINT = "https://api.anthropic.com/v1/messages"
DEFAULT_LLM_MODEL = "claude-sonnet-4-5"
DEFAULT_LLM_PROVIDER_TYPE = "aws_bedrock"

DEFAULT_ISSUE_FILTERING_MODEL = ""
DEFAULT_RESPONSE_CHALLENGE_MODEL = ""

# Model identifiers
MODEL_CLAUDE_OPUS_4_7 = "anthropic.claude-opus-4-7"
MODEL_CLAUDE_OPUS_4_5 = "anthropic.claude-opus-4-5-20251101-v1:0"
MODEL_CLAUDE_SONNET_4_5 = "anthropic.claude-sonnet-4-5-20250929-v1:0"
MODEL_CLAUDE_SONNET_3_5_V2 = "anthropic.claude-3-5-sonnet-20241022-v2:0"
MODEL_CLAUDE_SONNET_3_5 = "anthropic.claude-3-5-sonnet-20240620-v1:0"
MODEL_CLAUDE_HAIKU_3_5 = "anthropic.claude-3-5-haiku-20241022-v1:0"
MODEL_CLAUDE_OPUS_3 = "anthropic.claude-3-opus-20240229-v1:0"
MODEL_CLAUDE_SONNET_3 = "anthropic.claude-3-sonnet-20240229-v1:0"
MODEL_CLAUDE_HAIKU_3 = "anthropic.claude-3-haiku-20240307-v1:0"

# Data flow analyzer defaults
DATA_FLOW_ANALYZER_MODEL = MODEL_CLAUDE_OPUS_4_7
DATA_FLOW_ANALYZER_MAX_TOKENS = 84_000


class ModelLimits:
    """Provides context window length limits per model.

    Context window is the total token budget (input + output) for a single request.
    Max output tokens is the maximum response length the API will accept.
    Use get_context_window() and get_max_output_tokens() to look up limits.
    """

    _CONTEXT_WINDOWS = {
        # Claude 4.7 family — 1M context
        MODEL_CLAUDE_OPUS_4_7: 1_000_000,
        # Claude 4.5 family — 1M context
        MODEL_CLAUDE_OPUS_4_5: 1_000_000,
        MODEL_CLAUDE_SONNET_4_5: 1_000_000,
        # Claude 3.5 family — 200K context
        MODEL_CLAUDE_SONNET_3_5_V2: 200_000,
        MODEL_CLAUDE_SONNET_3_5: 200_000,
        MODEL_CLAUDE_HAIKU_3_5: 200_000,
        # Claude 3 family — 200K context
        MODEL_CLAUDE_OPUS_3: 200_000,
        MODEL_CLAUDE_SONNET_3: 200_000,
        MODEL_CLAUDE_HAIKU_3: 200_000,
    }

    _MAX_OUTPUT_TOKENS = {
        # Claude 4.7 family
        MODEL_CLAUDE_OPUS_4_7: 128_000,
        # Claude 4.5 family
        MODEL_CLAUDE_OPUS_4_5: 128_000,
        MODEL_CLAUDE_SONNET_4_5: 128_000,
        # Claude 3.5 family
        MODEL_CLAUDE_SONNET_3_5_V2: 8_192,
        MODEL_CLAUDE_SONNET_3_5: 8_192,
        MODEL_CLAUDE_HAIKU_3_5: 8_192,
        # Claude 3 family
        MODEL_CLAUDE_OPUS_3: 4_096,
        MODEL_CLAUDE_SONNET_3: 4_096,
        MODEL_CLAUDE_HAIKU_3: 4_096,
    }

    # Keyword-based fallback mapping for partial model string matching
    _FAMILY_CONTEXT_WINDOWS = {
        "opus-4": 1_000_000,
        "sonnet-4": 1_000_000,
        "claude-4": 1_000_000,
        "opus-3": 200_000,
        "sonnet-3": 200_000,
        "haiku-3": 200_000,
    }

    _FAMILY_MAX_OUTPUT_TOKENS = {
        "opus-4": 128_000,
        "sonnet-4": 128_000,
        "claude-4": 128_000,
        "opus-3": 4_096,
        "sonnet-3": 4_096,
        "haiku-3": 4_096,
    }

    DEFAULT_CONTEXT_WINDOW = 200_000
    DEFAULT_MAX_OUTPUT_TOKENS = 8_192

    @classmethod
    def _normalize_model(cls, model: str) -> str:
        """Normalize model string for lookup."""
        normalized = model.lower()
        if ":" in normalized and not normalized.startswith("anthropic"):
            normalized = normalized.split(":", 1)[1]
        return normalized

    @classmethod
    def get_context_window(cls, model: str) -> int:
        """Return the context window size in tokens for the given model string.

        Handles both full model IDs (e.g. 'anthropic.claude-opus-4-5-20251101-v1:0')
        and prefixed variants (e.g. 'aws:anthropic.claude-opus-4-5-20251101-v1:0').
        """
        normalized = cls._normalize_model(model)

        for model_id, limit in cls._CONTEXT_WINDOWS.items():
            if model_id.lower() in normalized:
                return limit

        for family_key, limit in cls._FAMILY_CONTEXT_WINDOWS.items():
            if family_key in normalized:
                return limit

        return cls.DEFAULT_CONTEXT_WINDOW

    @classmethod
    def get_max_output_tokens(cls, model: str) -> int:
        """Return the maximum output tokens the API accepts for the given model.

        This is the cap for the 'max_tokens' parameter in API requests.
        """
        normalized = cls._normalize_model(model)

        for model_id, limit in cls._MAX_OUTPUT_TOKENS.items():
            if model_id.lower() in normalized:
                return limit

        for family_key, limit in cls._FAMILY_MAX_OUTPUT_TOKENS.items():
            if family_key in normalized:
                return limit

        return cls.DEFAULT_MAX_OUTPUT_TOKENS


DEFAULT_EXCLUDE_DIRECTORY_NAMES = [
    "Tests", "Test", "bin", "third_party", "protobuf", "protobufs", ".git", "docs"
]