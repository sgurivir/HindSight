"""Tool schemas — single source of truth for tool parameters.

The TOOL_DEFINITIONS dict + helpers are preserved verbatim from the legacy
`hindsight.core.llm.tools.tool_definitions` so the JSON-embedded tool protocol
the model already knows continues to work without prompt changes.

Public API:
  TOOL_DEFINITIONS               — the dict
  get_tool_definition(name)      — fetch one
  get_parameter_aliases(name)    — fetch aliases for one tool
  normalize_parameters(name, p)  — rename aliases to canonical
  validate_tool_parameters(...)  — type & required-field check
  get_openai_function_schema(n)  — OpenAI-style schema for one tool
  get_all_openai_function_schemas() — all of them
  get_tool_names()               — list of tool names
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple


TOOL_DEFINITIONS: Dict[str, Dict[str, Any]] = {
    "readFile": {
        "description": "Read file contents with automatic pruning for large files. Use this to examine source code files.",
        "parameters": {
            "path": {
                "type": "string",
                "required": True,
                "description": "Path to the file to read (relative to repository root)",
            },
        },
        "aliases": {"filePath": "path", "file_path": "path", "file": "path"},
    },
    "getFileContentByLines": {
        "description": "Read specific line ranges from a file. IMPORTANT: Call checkFileSize first to get the total line count before using this tool - this prevents requesting lines beyond the file's end. The checkFileSize tool returns 'line_count' which tells you the valid range (1 to line_count). Response header also includes total line count for subsequent calls.",
        "parameters": {
            "path": {"type": "string", "required": True, "description": "Path to the file (relative to repository root)"},
            "startLine": {"type": "integer", "required": True, "description": "Starting line number (1-based, inclusive)"},
            "endLine": {"type": "integer", "required": True, "description": "Ending line number (1-based, inclusive)"},
            "reason": {"type": "string", "required": False, "description": "Reason for reading these specific lines"},
        },
        "aliases": {
            "filePath": "path",
            "file": "path",
            "file_path": "path",
            "start_line": "startLine",
            "end_line": "endLine",
        },
    },
    "checkFileSize": {
        "description": "Check if a file exists and get its size and LINE COUNT. ALWAYS use this before getFileContentByLines to know the valid line range (1 to line_count). Returns: file_available, size_bytes, size_characters, line_count. Use the line_count value to ensure your endLine parameter doesn't exceed the file length.",
        "parameters": {
            "path": {"type": "string", "required": True, "description": "Path to the file to check"},
            "reason": {"type": "string", "required": False, "description": "Reason for checking file size"},
        },
        "aliases": {"filePath": "path", "file_path": "path", "file": "path"},
    },
    "runTerminalCmd": {
        "description": "Execute safe terminal commands with validation. Only read-only commands are allowed. For grep: use SINGLE WORD patterns only. DON'T use multi-word patterns, regex (.*), OR patterns (\\|), or wildcard paths (*.swift).",
        "parameters": {
            "command": {
                "type": "string",
                "required": True,
                "description": "Command to execute. For grep: use single distinctive words only, avoid multi-word patterns and regex.",
            },
            "reason": {"type": "string", "required": False, "description": "Reason for executing this command"},
        },
        "aliases": {"cmd": "command"},
    },
    "list_files": {
        "description": "List directory contents with file sizes. Use this to explore the repository structure. Set recursive=true to get a full tree view of nested directories.",
        "parameters": {
            "path": {"type": "string", "required": True, "description": "Path to the directory to list (relative to repository root)"},
            "recursive": {
                "type": "boolean",
                "required": False,
                "description": "If true, list files recursively in a tree format showing nested directories. Default is false (single level only).",
            },
            "reason": {"type": "string", "required": False, "description": "Reason for listing this directory"},
        },
        "aliases": {"directory": "path"},
    },
    "inspectDirectoryHierarchy": {
        "description": "Get directory structure information. Returns a hierarchical view of the directory.",
        "parameters": {
            "path": {
                "type": "string",
                "required": False,
                "description": "Path to the directory to inspect (defaults to repository root)",
            },
            "reason": {"type": "string", "required": False, "description": "Reason for inspecting this directory"},
        },
        "aliases": {"directory_path": "path", "directory": "path"},
    },
    "getImplementation": {
        "description": "Retrieve class or function implementations from the code registry. Use this to get the full implementation of a specific class or function.",
        "parameters": {
            "name": {"type": "string", "required": True, "description": "Name of the class or function to retrieve"},
            "reason": {"type": "string", "required": False, "description": "Reason for retrieving this implementation"},
        },
        "aliases": {"class_name": "name", "function_name": "name"},
    },
    "getSummaryOfFile": {
        "description": "Generate file summary using CodeContextPruner. Returns a pruned version showing signatures and structure.",
        "parameters": {
            "path": {"type": "string", "required": True, "description": "Path to the file to summarize"},
            "reason": {"type": "string", "required": False, "description": "Reason for getting summary"},
        },
        "aliases": {"paths": "path", "file_path": "path"},
    },
    "lookup_knowledge": {
        "description": (
            "Search the persistent knowledge store for prior technical knowledge about this project — "
            "function summaries, file/module roles, cross-cutting invariants (threading, ownership, "
            "lifecycle, ordering rules). Returns a JSON array ranked by relevance (may be empty).\n\n"
            "ALWAYS call this before `readFile`, `getFileContentByLines`, `getImplementation`, or "
            "`getSummaryOfFile` for any function, file, or topic you don't already have context on. "
            "If a fresh matching entry is returned, use its summary and skip the file read.\n\n"
            "One tool, one query — pass a function name, file path, or free-text topic (e.g. "
            "'FooManager threading', 'parseConfig', 'src/Core.swift'). FTS5 ranks across summary, "
            "details, entity_key, function_name, and file_path in one pass."
        ),
        "parameters": {
            "query": {
                "type": "string",
                "required": True,
                "description": (
                    "Function name, file path, or free-text topic. Examples: 'parseConfig', "
                    "'src/Cache.swift', 'main-queue threading FooManager', 'reference counting in dispatch handlers'"
                ),
            },
            "kind": {"type": "string", "required": False, "description": "Optional filter: 'summary' | 'invariant'"},
            "max_results": {"type": "integer", "required": False, "description": "Maximum number of results (default 5)"},
            "reason": {"type": "string", "required": False, "description": "Why you're looking this up"},
        },
        "aliases": {
            "q": "query",
            "topic": "query",
            "function_name": "query",
            "file_path": "query",
            "name": "query",
        },
    },
    "store_knowledge": {
        "description": (
            "Persist what you have learned about a function, file, or cross-cutting rule so future "
            "analyses can recall it without re-reading source.\n\n"
            "ALWAYS call this after you have understood a function's contract for the first time, "
            "OR after you confirm a cross-cutting rule the codebase relies on. Record BEFORE moving "
            "on to the next callee. Skipping this step forces future runs to redo the same work.\n\n"
            "Store only general technical knowledge — NOT bug findings or defects. Defects belong "
            "in your final output JSON.\n\n"
            "Example (function summary with line-anchored behavior):\n"
            "```json\n"
            "{\n"
            "  \"tool\": \"store_knowledge\",\n"
            "  \"kind\": \"summary\",\n"
            "  \"entity_key\": \"src/Cache.swift::Cache.evict\",\n"
            "  \"function_name\": \"Cache.evict\",\n"
            "  \"file_path\": \"src/Cache.swift\",\n"
            "  \"summary\": \"Removes the LRU entry from _entries and returns it; caller owns lifetime.\",\n"
            "  \"behavior\": \"LINE 82: _entries.removeLast() returns the entry without retaining. LINE 87: caller must use the return value before the next evict() call — no strong reference held.\",\n"
            "  \"tags\": [\"cache\", \"lifecycle\"],\n"
            "  \"confidence\": 0.9\n"
            "}\n"
            "```\n"
            "Example (cross-cutting invariant):\n"
            "```json\n"
            "{\n"
            "  \"tool\": \"store_knowledge\",\n"
            "  \"kind\": \"invariant\",\n"
            "  \"entity_key\": \"FooManager-main-queue-only\",\n"
            "  \"summary\": \"All writes to FooManager state must happen on the main queue; read APIs are thread-safe but writes are not.\",\n"
            "  \"tags\": [\"threading\", \"FooManager\"],\n"
            "  \"confidence\": 0.9\n"
            "}\n"
            "```"
        ),
        "parameters": {
            "entity_key": {"type": "string", "required": True, "description": "Identity: 'file::function' for function-level, 'file' for file-level, or free-form for cross-cutting rules (e.g. 'main-queue-only-FooManager')"},
            "summary": {"type": "string", "required": True, "description": "1-2 sentence learning. Describe behavior/contract/invariant — not a defect."},
            "kind": {"type": "string", "required": False, "description": "'summary' (default) | 'invariant'. Use 'invariant' for cross-cutting rules that span call sites."},
            "confidence": {"type": "number", "required": False, "description": "Float in [0.0, 1.0]. Default 0.8 if omitted."},
            "file_path": {"type": "string", "required": False, "description": "Repo-relative file path"},
            "function_name": {"type": "string", "required": False, "description": "Exact function name"},
            "checksum": {"type": "string", "required": False, "description": "Function source checksum — pass when the learning is tied to a specific source revision"},
            "behavior": {"type": "string", "required": False, "description": "Concrete, line-anchored behavior notes (e.g. 'LINE 42: allocates X per call, no pooling'). Merged into `details` for storage."},
            "details": {"type": "string", "required": False, "description": "Longer evidence / reasoning / context. If `behavior` is also provided, both are concatenated."},
            "tags": {"type": "array", "required": False, "description": "Free-form tags for later topic search (e.g. 'threading', 'json', 'lifecycle')", "items": {"type": "string"}},
            "reason": {"type": "string", "required": False, "description": "Why you're recording this"},
        },
        "aliases": {"filePath": "file_path", "name": "function_name"},
    },
}


def get_tool_definition(tool_name: str) -> Optional[Dict[str, Any]]:
    return TOOL_DEFINITIONS.get(tool_name)


def get_parameter_aliases(tool_name: str) -> Dict[str, str]:
    return TOOL_DEFINITIONS.get(tool_name, {}).get("aliases", {})


def normalize_parameters(tool_name: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """Rename alias keys to their canonical form (e.g. `file_path` → `path`).

    Unknown keys pass through unchanged so callers can include extra context
    fields like `reason` that aren't aliased.
    """
    aliases = get_parameter_aliases(tool_name)
    return {aliases.get(k, k): v for k, v in params.items()}


def validate_tool_parameters(tool_name: str, params: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
    """Check that required params are present and have the right primitive type.

    Returns (True, None) on success, (False, error_message) otherwise.
    """
    tool_def = TOOL_DEFINITIONS.get(tool_name)
    if not tool_def:
        return False, f"Unknown tool: {tool_name}"

    normalized = normalize_parameters(tool_name, params)
    for param_name, param_def in tool_def["parameters"].items():
        if not param_def.get("required", False):
            continue
        if param_name not in normalized:
            return False, f"Missing required parameter: {param_name}"
        value = normalized[param_name]
        expected = param_def["type"]
        if expected == "string" and not isinstance(value, str):
            return False, f"Parameter '{param_name}' must be a string"
        if expected == "integer" and not isinstance(value, int):
            return False, f"Parameter '{param_name}' must be an integer"
        if expected == "number" and not isinstance(value, (int, float)):
            return False, f"Parameter '{param_name}' must be a number"
        if expected == "array" and not isinstance(value, list):
            return False, f"Parameter '{param_name}' must be an array"
    return True, None


def get_openai_function_schema(tool_name: str) -> Optional[Dict[str, Any]]:
    tool_def = TOOL_DEFINITIONS.get(tool_name)
    if not tool_def:
        return None

    properties: dict[str, Any] = {}
    required: list[str] = []
    for param_name, param_def in tool_def["parameters"].items():
        param_schema: dict[str, Any] = {
            "type": param_def["type"],
            "description": param_def["description"],
        }
        if param_def["type"] == "array" and "items" in param_def:
            param_schema["items"] = param_def["items"]
        properties[param_name] = param_schema
        if param_def.get("required", False):
            required.append(param_name)

    return {
        "type": "function",
        "function": {
            "name": tool_name,
            "description": tool_def["description"],
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        },
    }


def get_all_openai_function_schemas() -> List[Dict[str, Any]]:
    return [schema for name in TOOL_DEFINITIONS if (schema := get_openai_function_schema(name)) is not None]


def get_tool_names() -> List[str]:
    return list(TOOL_DEFINITIONS.keys())
