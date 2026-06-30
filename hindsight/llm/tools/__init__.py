"""Async tool registry for Hindsight.

This package replaces `hindsight/core/llm/tools/` at the end of the migration.
Public surface:

    ToolRegistry, ToolContext, ToolStats, ToolHandler — registry primitives
    build_default_registry(ctx)                       — builds a registry with
                                                        all 8 standard tools
                                                        pre-registered
    Tool functions (async):
        read_file_tool, get_file_content_by_lines_tool,
        check_file_size_tool, list_files_tool,
        inspect_directory_hierarchy_tool, run_terminal_cmd_tool,
        get_implementation_tool, get_summary_of_file_tool
    Schema helpers (re-exported from .schemas)

A pipeline instantiates one `ToolRegistry(ctx)` per session and reuses it
across all iterative stage runs. The per-call `allowed` filter lets the same
registry serve full-toolset stages (Stage A) and reduced-toolset stages
(Stage B) without cloning.
"""

from __future__ import annotations

from .dir import inspect_directory_hierarchy_tool, list_files_tool
from .fs import (
    check_file_size_tool,
    get_file_content_by_lines_tool,
    read_file_tool,
)
from .registry import (
    ALLOWED_TERMINAL_COMMANDS,
    MAX_FILE_CHARACTERS,
    MAX_FILE_SIZE_BYTES,
    ToolContext,
    ToolHandler,
    ToolRegistry,
    ToolStats,
)
from .schemas import (
    TOOL_DEFINITIONS,
    get_all_openai_function_schemas,
    get_openai_function_schema,
    get_parameter_aliases,
    get_tool_definition,
    get_tool_names,
    normalize_parameters,
    validate_tool_parameters,
)
from .shell import run_terminal_cmd_tool
from .summary import get_summary_of_file_tool
from .symbols import get_implementation_tool


def build_default_registry(ctx: ToolContext) -> ToolRegistry:
    """Construct a `ToolRegistry` with every standard tool pre-registered.

    Pipelines typically just need this — they pass the registry to the
    `IterativeRunner` and rely on the per-call `allowed` set to gate which
    tools each stage can use.
    """
    registry = ToolRegistry(ctx)
    registry.register("readFile", read_file_tool)
    registry.register("getFileContentByLines", get_file_content_by_lines_tool)
    # `getFileContent` is a legacy alias accepted by the same handler. The
    # legacy executor treated `getFileContent` and `getFileContentByLines`
    # identically; preserve that.
    registry.register("getFileContent", get_file_content_by_lines_tool)
    registry.register("checkFileSize", check_file_size_tool)
    registry.register("list_files", list_files_tool)
    registry.register("inspectDirectoryHierarchy", inspect_directory_hierarchy_tool)
    registry.register("runTerminalCmd", run_terminal_cmd_tool)
    registry.register("getImplementation", get_implementation_tool)
    registry.register("getSummaryOfFile", get_summary_of_file_tool)
    return registry


__all__ = [
    "ALLOWED_TERMINAL_COMMANDS",
    "MAX_FILE_CHARACTERS",
    "MAX_FILE_SIZE_BYTES",
    "TOOL_DEFINITIONS",
    "ToolContext",
    "ToolHandler",
    "ToolRegistry",
    "ToolStats",
    "build_default_registry",
    "check_file_size_tool",
    "get_all_openai_function_schemas",
    "get_file_content_by_lines_tool",
    "get_implementation_tool",
    "get_openai_function_schema",
    "get_parameter_aliases",
    "get_summary_of_file_tool",
    "get_tool_definition",
    "get_tool_names",
    "inspect_directory_hierarchy_tool",
    "list_files_tool",
    "normalize_parameters",
    "read_file_tool",
    "run_terminal_cmd_tool",
    "validate_tool_parameters",
]
