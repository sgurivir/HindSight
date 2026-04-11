#!/usr/bin/env python3
# Created by Sridhar Gurivireddy
"""
Core constants used across the Hindsight analysis system
"""

# Common constants for analysis
DEFAULT_MAX_TOKENS = 64000
DEFAULT_TEMPERATURE = 0.1
DEFAULT_API_RATE_LIMIT = 80  # requests per 4 minutes 10 seconds
DEFAULT_RATE_LIMIT_WINDOW = 250  # seconds (4 minutes 10 seconds)
DEFAULT_LOGS_DIR = "logs"
DEFAULT_DIFF_DAYS = 21  # Default number of days to look back for recently modified files
MAX_TOOL_ITERATIONS = 12  # Maximum iterations for LLM tool usage
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
DEFAULT_LLM_PROVIDER_TYPE = "claude"

DEFAULT_ISSUE_FILTERING_MODEL = ""
DEFAULT_RESPONSE_CHALLENGE_MODEL = ""

DEFAULT_EXCLUDE_DIRECTORY_NAMES = [
    "Tests", "Test", "bin", "third_party", "protobuf", "protobufs", ".git", "docs"
]