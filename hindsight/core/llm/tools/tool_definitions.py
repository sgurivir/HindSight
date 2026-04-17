#!/usr/bin/env python3
# Created by Sridhar Gurivireddy
"""
Tool Definitions - Single source of truth for tool parameters.

This file defines all tool parameters in one place to ensure
documentation and implementation stay in sync. It provides:
- Tool definitions with descriptions and parameters
- OpenAI-compatible function schemas
- Markdown documentation generation
- Parameter aliases for backward compatibility
"""

from typing import Dict, List, Any, Optional


# Tool definitions - Single source of truth
TOOL_DEFINITIONS: Dict[str, Dict[str, Any]] = {
    "readFile": {
        "description": "Read file contents with automatic pruning for large files. Use this to examine source code files.",
        "parameters": {
            "path": {
                "type": "string",
                "required": True,
                "description": "Path to the file to read (relative to repository root)"
            }
        },
        "aliases": {}
    },
    "getFileContentByLines": {
        "description": "Read specific line ranges from a file. IMPORTANT: Call checkFileSize first to get the total line count before using this tool - this prevents requesting lines beyond the file's end. The checkFileSize tool returns 'line_count' which tells you the valid range (1 to line_count). Response header also includes total line count for subsequent calls.",
        "parameters": {
            "path": {
                "type": "string",
                "required": True,
                "description": "Path to the file (relative to repository root)"
            },
            "startLine": {
                "type": "integer",
                "required": True,
                "description": "Starting line number (1-based, inclusive)"
            },
            "endLine": {
                "type": "integer",
                "required": True,
                "description": "Ending line number (1-based, inclusive)"
            },
            "reason": {
                "type": "string",
                "required": False,
                "description": "Reason for reading these specific lines"
            }
        },
        "aliases": {
            "file": "path",
            "file_path": "path",
            "start_line": "startLine",
            "end_line": "endLine"
        }
    },
    "checkFileSize": {
        "description": "Check if a file exists and get its size and LINE COUNT. ALWAYS use this before getFileContentByLines to know the valid line range (1 to line_count). Returns: file_available, size_bytes, size_characters, line_count. Use the line_count value to ensure your endLine parameter doesn't exceed the file length.",
        "parameters": {
            "path": {
                "type": "string",
                "required": True,
                "description": "Path to the file to check"
            },
            "reason": {
                "type": "string",
                "required": False,
                "description": "Reason for checking file size"
            }
        },
        "aliases": {}
    },
    "runTerminalCmd": {
        "description": "Execute safe terminal commands with validation. Only read-only commands are allowed. For grep: use SINGLE WORD patterns only. DON'T use multi-word patterns, regex (.*), OR patterns (\\|), or wildcard paths (*.swift).",
        "parameters": {
            "command": {
                "type": "string",
                "required": True,
                "description": "Command to execute. For grep: use single distinctive words only, avoid multi-word patterns and regex."
            },
            "reason": {
                "type": "string",
                "required": False,
                "description": "Reason for executing this command"
            }
        },
        "aliases": {
            "cmd": "command"
        }
    },
    "list_files": {
        "description": "List directory contents with file sizes. Use this to explore the repository structure. Set recursive=true to get a full tree view of nested directories.",
        "parameters": {
            "path": {
                "type": "string",
                "required": True,
                "description": "Path to the directory to list (relative to repository root)"
            },
            "recursive": {
                "type": "boolean",
                "required": False,
                "description": "If true, list files recursively in a tree format showing nested directories. Default is false (single level only)."
            },
            "reason": {
                "type": "string",
                "required": False,
                "description": "Reason for listing this directory"
            }
        },
        "aliases": {
            "directory": "path"
        }
    },
    "inspectDirectoryHierarchy": {
        "description": "Get directory structure information. Returns a hierarchical view of the directory.",
        "parameters": {
            "path": {
                "type": "string",
                "required": False,
                "description": "Path to the directory to inspect (defaults to repository root)"
            },
            "reason": {
                "type": "string",
                "required": False,
                "description": "Reason for inspecting this directory"
            }
        },
        "aliases": {
            "directory_path": "path",
            "directory": "path"
        }
    },
    "getImplementation": {
        "description": "Retrieve class or function implementations from the code registry. Use this to get the full implementation of a specific class or function.",
        "parameters": {
            "name": {
                "type": "string",
                "required": True,
                "description": "Name of the class or function to retrieve"
            },
            "reason": {
                "type": "string",
                "required": False,
                "description": "Reason for retrieving this implementation"
            }
        },
        "aliases": {
            "class_name": "name",
            "function_name": "name"
        }
    },
    "getSummaryOfFile": {
        "description": "Generate file summary using CodeContextPruner. Returns a pruned version showing signatures and structure.",
        "parameters": {
            "path": {
                "type": "string",
                "required": True,
                "description": "Path to the file to summarize"
            },
            "reason": {
                "type": "string",
                "required": False,
                "description": "Reason for getting summary"
            }
        },
        "aliases": {
            "paths": "path",
            "file_path": "path"
        }
    }
}


def get_tool_definition(tool_name: str) -> Optional[Dict[str, Any]]:
    """
    Get the definition for a specific tool.
    
    Args:
        tool_name: Name of the tool
        
    Returns:
        Tool definition dictionary or None if not found
    """
    return TOOL_DEFINITIONS.get(tool_name)


def get_parameter_aliases(tool_name: str) -> Dict[str, str]:
    """
    Get parameter aliases for a tool.
    
    Args:
        tool_name: Name of the tool
        
    Returns:
        Dictionary mapping alias names to canonical parameter names
    """
    tool_def = TOOL_DEFINITIONS.get(tool_name, {})
    return tool_def.get("aliases", {})


def normalize_parameters(tool_name: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Normalize parameters by converting aliases to canonical names.
    
    Args:
        tool_name: Name of the tool
        params: Parameters dictionary (may contain aliases)
        
    Returns:
        Parameters dictionary with canonical names
    """
    aliases = get_parameter_aliases(tool_name)
    normalized = {}
    
    for key, value in params.items():
        # Convert alias to canonical name if applicable
        canonical_key = aliases.get(key, key)
        normalized[canonical_key] = value
    
    return normalized


def get_openai_function_schema(tool_name: str) -> Optional[Dict[str, Any]]:
    """
    Generate OpenAI-compatible function schema for a tool.
    
    Args:
        tool_name: Name of the tool
        
    Returns:
        OpenAI function schema dictionary or None if tool not found
    """
    tool_def = TOOL_DEFINITIONS.get(tool_name)
    if not tool_def:
        return None
    
    # Build properties and required list
    properties = {}
    required = []
    
    for param_name, param_def in tool_def["parameters"].items():
        param_schema = {
            "type": param_def["type"],
            "description": param_def["description"]
        }
        
        # Handle array type
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
                "required": required
            }
        }
    }


def get_all_openai_function_schemas() -> List[Dict[str, Any]]:
    """
    Generate OpenAI-compatible function schemas for all tools.
    
    Returns:
        List of OpenAI function schema dictionaries
    """
    schemas = []
    for tool_name in TOOL_DEFINITIONS:
        schema = get_openai_function_schema(tool_name)
        if schema:
            schemas.append(schema)
    return schemas


def get_tool_names() -> List[str]:
    """
    Get list of all available tool names.
    
    Returns:
        List of tool names
    """
    return list(TOOL_DEFINITIONS.keys())


def generate_markdown_documentation() -> str:
    """
    Generate markdown documentation from tool definitions.
    This ensures documentation is always in sync with implementation.
    
    Returns:
        Markdown formatted documentation string
    """
    lines = [
        "# Analysis Tools Documentation",
        "",
        "This document describes the tools available for code analysis.",
        "**Note:** This documentation is auto-generated from tool_definitions.py",
        "",
    ]
    
    for tool_name, tool_def in TOOL_DEFINITIONS.items():
        lines.append(f"## {tool_name}")
        lines.append("")
        lines.append(tool_def["description"])
        lines.append("")
        lines.append("### Parameters")
        lines.append("")
        lines.append("| Parameter | Type | Required | Description |")
        lines.append("|-----------|------|----------|-------------|")
        
        for param_name, param_def in tool_def["parameters"].items():
            param_type = param_def["type"]
            if param_type == "array" and "items" in param_def:
                param_type = f"array[{param_def['items'].get('type', 'any')}]"
            
            required = "Yes" if param_def.get("required", False) else "No"
            description = param_def["description"]
            lines.append(f"| {param_name} | {param_type} | {required} | {description} |")
        
        # Add aliases section if any
        aliases = tool_def.get("aliases", {})
        if aliases:
            lines.append("")
            lines.append("### Parameter Aliases")
            lines.append("")
            lines.append("For backward compatibility, the following aliases are supported:")
            lines.append("")
            for alias, canonical in aliases.items():
                lines.append(f"- `{alias}` → `{canonical}`")
        
        lines.append("")
    
    return "\n".join(lines)


def validate_tool_parameters(tool_name: str, params: Dict[str, Any]) -> tuple[bool, Optional[str]]:
    """
    Validate parameters for a tool call.
    
    Args:
        tool_name: Name of the tool
        params: Parameters dictionary
        
    Returns:
        Tuple of (is_valid, error_message)
    """
    tool_def = TOOL_DEFINITIONS.get(tool_name)
    if not tool_def:
        return False, f"Unknown tool: {tool_name}"
    
    # Normalize parameters first
    normalized_params = normalize_parameters(tool_name, params)
    
    # Check required parameters
    for param_name, param_def in tool_def["parameters"].items():
        if param_def.get("required", False):
            if param_name not in normalized_params:
                return False, f"Missing required parameter: {param_name}"
            
            value = normalized_params[param_name]
            
            # Type validation
            expected_type = param_def["type"]
            if expected_type == "string" and not isinstance(value, str):
                return False, f"Parameter '{param_name}' must be a string"
            elif expected_type == "integer" and not isinstance(value, int):
                return False, f"Parameter '{param_name}' must be an integer"
            elif expected_type == "array" and not isinstance(value, list):
                return False, f"Parameter '{param_name}' must be an array"
    
    return True, None
