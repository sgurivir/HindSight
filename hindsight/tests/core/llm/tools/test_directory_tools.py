"""
Tests for list_files tool with recursive parameter.

File: hindsight/tests/core/llm/tools/test_directory_tools.py
"""

import pytest
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

from hindsight.core.llm.tools.tools import Tools
from hindsight.core.llm.tools.directory_tools import DirectoryToolsMixin
from hindsight.utils.directory_tree_util import DirectoryTreeUtil


@pytest.fixture
def temp_repo_with_structure():
    """Create a temporary repository with directory structure."""
    with tempfile.TemporaryDirectory() as tmpdir:
        repo = Path(tmpdir)
        
        # Create nested structure (using .java extension which is in ALL_SUPPORTED_EXTENSIONS)
        (repo / "src" / "components").mkdir(parents=True)
        (repo / "src" / "components" / "Button.java").write_text("class Button {}")
        (repo / "src" / "main.java").write_text("class Main { public static void main(String[] args) {} }")
        
        yield repo


@pytest.fixture
def tools_instance(temp_repo_with_structure):
    """Create a Tools instance for testing."""
    directory_tree_util = DirectoryTreeUtil()
    
    tools = Tools(
        repo_path=str(temp_repo_with_structure),
        directory_tree_util=directory_tree_util
    )
    
    return tools


class TestListFilesToolRecursiveParameter:
    """Tests for list_files tool recursive parameter support."""
    
    def test_list_files_accepts_recursive_parameter(self, tools_instance):
        """Test that recursive parameter is accepted without error."""
        result = tools_instance.execute_tool_use({
            "name": "list_files",
            "input": {"path": "src", "recursive": True}
        })
        
        # Should not contain error about unexpected keyword argument
        assert "unexpected keyword argument" not in result.lower()
        assert "error" not in result.lower() or "path not found" in result.lower()
    
    def test_list_files_recursive_true_shows_nested(self, tools_instance):
        """Test that recursive=True shows nested content."""
        result = tools_instance.execute_tool_use({
            "name": "list_files",
            "input": {"path": "src", "recursive": True}
        })
        
        # Should show nested files
        assert "Button.java" in result
    
    def test_list_files_recursive_false_hides_nested(self, tools_instance):
        """Test that recursive=False hides nested content."""
        result = tools_instance.execute_tool_use({
            "name": "list_files",
            "input": {"path": "src", "recursive": False}
        })
        
        # Should NOT show nested files
        assert "Button.java" not in result
        # Should show immediate children
        assert "components/" in result
    
    def test_list_files_without_recursive_parameter(self, tools_instance):
        """Test backward compatibility without recursive parameter."""
        result = tools_instance.execute_tool_use({
            "name": "list_files",
            "input": {"path": "src"}
        })
        
        # Should work without error
        assert "unexpected keyword argument" not in result.lower()
        # Should default to non-recursive (no nested files)
        assert "Button.java" not in result
    
    def test_list_files_handler_signature(self, tools_instance):
        """Test that _handle_list_files accepts recursive parameter."""
        # Direct call to handler
        result = tools_instance._handle_list_files(
            path="src",
            recursive=True,
            reason="Testing recursive parameter"
        )
        
        assert "Button.java" in result


class TestListFilesToolBackwardCompatibility:
    """Tests to ensure backward compatibility with existing tool calls."""
    
    def test_existing_tool_call_format_works(self, tools_instance):
        """Test that existing tool call format continues to work."""
        # Simulate existing tool call without recursive parameter
        result = tools_instance.execute_tool_use({
            "name": "list_files",
            "input": {"path": "src", "reason": "Exploring directory"}
        })
        
        assert "src" in result
        assert "error" not in result.lower() or "path not found" in result.lower()
    
    def test_execute_list_files_tool_backward_compatible(self, tools_instance):
        """Test that execute_list_files_tool works with old signature."""
        # Old signature: execute_list_files_tool(path, reason)
        # New signature: execute_list_files_tool(path, recursive=False, reason=None)
        # Both should work
        
        # New way with recursive
        result_new = tools_instance.execute_list_files_tool("src", recursive=True, reason="test")
        assert "Button.java" in result_new
        
        # Old way without recursive (using default)
        result_old = tools_instance.execute_list_files_tool("src", reason="test")
        assert "Button.java" not in result_old


class TestDirectoryToolsMixinRecursive:
    """Tests for DirectoryToolsMixin with recursive parameter."""
    
    def test_execute_list_files_tool_recursive_true(self, tools_instance):
        """Test execute_list_files_tool with recursive=True."""
        result = tools_instance.execute_list_files_tool(
            path="src",
            recursive=True,
            reason="Testing recursive listing"
        )
        
        # Should show nested content
        assert "Button.java" in result
        assert "recursive" in result.lower()
    
    def test_execute_list_files_tool_recursive_false(self, tools_instance):
        """Test execute_list_files_tool with recursive=False."""
        result = tools_instance.execute_list_files_tool(
            path="src",
            recursive=False,
            reason="Testing non-recursive listing"
        )
        
        # Should NOT show nested content
        assert "Button.java" not in result
        assert "single level" in result.lower()
    
    def test_execute_list_files_tool_default_recursive(self, tools_instance):
        """Test execute_list_files_tool with default recursive value."""
        result = tools_instance.execute_list_files_tool(
            path="src",
            reason="Testing default behavior"
        )
        
        # Default should be non-recursive
        assert "Button.java" not in result
        assert "single level" in result.lower()


class TestToolDefinitionRecursiveParameter:
    """Tests for tool definition with recursive parameter."""
    
    def test_tool_definition_includes_recursive(self):
        """Test that tool definition includes recursive parameter."""
        from hindsight.core.llm.tools.tool_definitions import get_tool_definition
        
        tool_def = get_tool_definition("list_files")
        
        assert tool_def is not None
        assert "recursive" in tool_def["parameters"]
        assert tool_def["parameters"]["recursive"]["type"] == "boolean"
        assert tool_def["parameters"]["recursive"]["required"] is False
    
    def test_openai_schema_includes_recursive(self):
        """Test that OpenAI function schema includes recursive parameter."""
        from hindsight.core.llm.tools.tool_definitions import get_openai_function_schema
        
        schema = get_openai_function_schema("list_files")
        
        assert schema is not None
        properties = schema["function"]["parameters"]["properties"]
        
        assert "recursive" in properties
        assert properties["recursive"]["type"] == "boolean"
