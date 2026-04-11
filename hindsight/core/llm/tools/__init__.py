#!/usr/bin/env python3
# Created by Sridhar Gurivireddy
"""
Tools Package - OpenAI-Compatible Tool Implementation for LLM Interactions

This package provides a modular implementation of tools for LLM interactions
with OpenAI-compatible function calling support:

Core Modules:
- tool_definitions.py: Single source of truth for all tool definitions
- base.py: Base class with OpenAI-compatible execution framework
- tools.py: Main Tools class combining all implementations

Tool Modules:
- file_tools.py: File reading tools (readFile, getFileContentByLines, checkFileSize)
- terminal_tools.py: Terminal command tools (runTerminalCmd) - also supports grep for searching
- directory_tools.py: Directory tools (list_files, inspectDirectoryHierarchy)
- implementation_tools.py: Implementation tools (getImplementation, getSummaryOfFile)

Usage:
    from hindsight.core.llm.tools import Tools
    
    # Initialize tools
    tools = Tools(repo_path="/path/to/repo")
    
    # Get OpenAI-compatible schema
    schema = tools.get_tools_schema()
    
    # Execute OpenAI-format tool calls
    results = tools.execute_openai_tool_calls(tool_calls)
    
    # Or execute Claude-format tool_use blocks
    result = tools.execute_tool_use(tool_use_block)
"""

from .tools import Tools
from .tool_definitions import (
    TOOL_DEFINITIONS,
    get_tool_definition,
    get_parameter_aliases,
    normalize_parameters,
    get_openai_function_schema,
    get_all_openai_function_schemas,
    get_tool_names,
    generate_markdown_documentation,
    validate_tool_parameters,
)

__all__ = [
    'Tools',
    'TOOL_DEFINITIONS',
    'get_tool_definition',
    'get_parameter_aliases',
    'normalize_parameters',
    'get_openai_function_schema',
    'get_all_openai_function_schemas',
    'get_tool_names',
    'generate_markdown_documentation',
    'validate_tool_parameters',
]
