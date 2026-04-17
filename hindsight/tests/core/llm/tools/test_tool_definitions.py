#!/usr/bin/env python3
"""
Tests for hindsight/core/llm/tools/tool_definitions.py - Tool definitions and parameter normalization.

These tests verify that:
1. OpenAI function schemas are generated correctly
2. Parameter aliases work correctly (e.g., paths -> path for file tools)
"""
import pytest

from hindsight.core.llm.tools.tool_definitions import (
    TOOL_DEFINITIONS,
    get_tool_definition,
    get_parameter_aliases,
    normalize_parameters,
    get_openai_function_schema,
    get_all_openai_function_schemas,
    get_tool_names,
    validate_tool_parameters,
)


class TestOpenAISchemaGeneration:
    """Tests for OpenAI function schema generation."""

    def test_read_file_schema_generated(self):
        """OpenAI schema is generated for readFile."""
        schema = get_openai_function_schema('readFile')
        assert schema is not None
        assert schema['type'] == 'function'
        assert schema['function']['name'] == 'readFile'

    def test_all_schemas_exclude_knowledge_tools(self):
        """get_all_openai_function_schemas does not include knowledge tools."""
        schemas = get_all_openai_function_schemas()
        tool_names = [s['function']['name'] for s in schemas]
        assert 'lookup_knowledge' not in tool_names
        assert 'store_knowledge' not in tool_names


class TestToolNames:
    """Tests for get_tool_names function."""

    def test_knowledge_tools_not_in_tool_names(self):
        """Knowledge tools are NOT included in get_tool_names() after removal."""
        names = get_tool_names()
        assert 'lookup_knowledge' not in names
        assert 'store_knowledge' not in names

    def test_standard_tools_in_tool_names(self):
        """Standard tools are present in get_tool_names()."""
        names = get_tool_names()
        assert 'readFile' in names
        assert 'runTerminalCmd' in names
