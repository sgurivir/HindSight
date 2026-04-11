"""
Integration tests for list_files tool recursive functionality.

File: hindsight/tests/integration/test_list_files_recursive.py
"""

import pytest
import tempfile
from pathlib import Path

from hindsight.core.llm.tools.tools import Tools
from hindsight.core.llm.tools.tool_definitions import get_openai_function_schema
from hindsight.utils.directory_tree_util import DirectoryTreeUtil


class TestListFilesRecursiveIntegration:
    """Integration tests for recursive list_files functionality."""
    
    def test_openai_schema_includes_recursive_parameter(self):
        """Test that OpenAI function schema includes recursive parameter."""
        schema = get_openai_function_schema("list_files")
        
        assert schema is not None
        properties = schema["function"]["parameters"]["properties"]
        
        assert "recursive" in properties
        assert properties["recursive"]["type"] == "boolean"
    
    def test_full_tool_execution_flow(self):
        """Test complete tool execution flow with recursive parameter."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir)
            
            # Create structure (using .java extension which is in ALL_SUPPORTED_EXTENSIONS)
            (repo / "src" / "nested").mkdir(parents=True)
            (repo / "src" / "nested" / "deep.java").write_text("// Deep file")
            (repo / "src" / "top.java").write_text("// Top file")
            
            # Create tools instance
            tools = Tools(
                repo_path=str(repo),
                directory_tree_util=DirectoryTreeUtil()
            )
            
            # Execute with recursive=True
            result = tools.execute_tool_use({
                "name": "list_files",
                "input": {"path": "src", "recursive": True}
            })
            
            # Verify nested file is visible
            assert "deep.java" in result
            assert "top.java" in result
    
    def test_full_tool_execution_flow_non_recursive(self):
        """Test complete tool execution flow without recursive parameter."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir)
            
            # Create structure
            (repo / "src" / "nested").mkdir(parents=True)
            (repo / "src" / "nested" / "deep.java").write_text("// Deep file")
            (repo / "src" / "top.java").write_text("// Top file")
            
            # Create tools instance
            tools = Tools(
                repo_path=str(repo),
                directory_tree_util=DirectoryTreeUtil()
            )
            
            # Execute without recursive parameter (default)
            result = tools.execute_tool_use({
                "name": "list_files",
                "input": {"path": "src"}
            })
            
            # Verify nested file is NOT visible (default is non-recursive)
            assert "deep.java" not in result
            assert "top.java" in result
            assert "nested/" in result
    
    def test_tool_execution_with_reason(self):
        """Test tool execution with reason parameter."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir)
            
            # Create structure
            (repo / "src").mkdir(parents=True)
            (repo / "src" / "main.java").write_text("// Main file")
            
            # Create tools instance
            tools = Tools(
                repo_path=str(repo),
                directory_tree_util=DirectoryTreeUtil()
            )
            
            # Execute with all parameters
            result = tools.execute_tool_use({
                "name": "list_files",
                "input": {
                    "path": "src",
                    "recursive": True,
                    "reason": "Exploring source directory structure"
                }
            })
            
            # Should work without error
            assert "main.java" in result
            assert "error" not in result.lower()
    
    def test_directory_tree_util_integration(self):
        """Test DirectoryTreeUtil integration with recursive parameter."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir)
            
            # Create multi-level structure
            (repo / "level1" / "level2" / "level3").mkdir(parents=True)
            (repo / "level1" / "file1.java").write_text("// Level 1")
            (repo / "level1" / "level2" / "file2.java").write_text("// Level 2")
            (repo / "level1" / "level2" / "level3" / "file3.java").write_text("// Level 3")
            
            # Test non-recursive
            result_non_recursive = DirectoryTreeUtil.get_directory_listing(
                repo_path=str(repo),
                relative_path="level1",
                recursive=False
            )
            
            assert "file1.java" in result_non_recursive
            assert "file2.java" not in result_non_recursive
            assert "file3.java" not in result_non_recursive
            
            # Test recursive
            result_recursive = DirectoryTreeUtil.get_directory_listing(
                repo_path=str(repo),
                relative_path="level1",
                recursive=True
            )
            
            assert "file1.java" in result_recursive
            assert "file2.java" in result_recursive
            assert "file3.java" in result_recursive
    
    def test_max_depth_integration(self):
        """Test max_depth parameter integration."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir)
            
            # Create deep structure
            (repo / "a" / "b" / "c" / "d").mkdir(parents=True)
            (repo / "a" / "file_a.java").write_text("// A")
            (repo / "a" / "b" / "file_b.java").write_text("// B")
            (repo / "a" / "b" / "c" / "file_c.java").write_text("// C")
            (repo / "a" / "b" / "c" / "d" / "file_d.java").write_text("// D")
            
            # Test with max_depth=1 (should show a and b level)
            result_depth_1 = DirectoryTreeUtil.get_directory_listing(
                repo_path=str(repo),
                relative_path="a",
                recursive=True,
                max_depth=1
            )
            
            assert "file_a.java" in result_depth_1
            assert "file_b.java" in result_depth_1
            assert "file_c.java" not in result_depth_1
            assert "file_d.java" not in result_depth_1
            
            # Test with max_depth=3 (should show all)
            result_depth_3 = DirectoryTreeUtil.get_directory_listing(
                repo_path=str(repo),
                relative_path="a",
                recursive=True,
                max_depth=3
            )
            
            assert "file_a.java" in result_depth_3
            assert "file_b.java" in result_depth_3
            assert "file_c.java" in result_depth_3
            assert "file_d.java" in result_depth_3


class TestListFilesRecursiveErrorHandling:
    """Integration tests for error handling with recursive parameter."""
    
    def test_invalid_path_with_recursive(self):
        """Test error handling for invalid path with recursive parameter."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir)
            
            tools = Tools(
                repo_path=str(repo),
                directory_tree_util=DirectoryTreeUtil()
            )
            
            result = tools.execute_tool_use({
                "name": "list_files",
                "input": {"path": "nonexistent", "recursive": True}
            })
            
            assert "not found" in result.lower()
    
    def test_file_path_with_recursive(self):
        """Test that file paths work correctly with recursive parameter."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir)
            (repo / "test.java").write_text("// Test file")
            
            tools = Tools(
                repo_path=str(repo),
                directory_tree_util=DirectoryTreeUtil()
            )
            
            # Recursive should be ignored for files
            result = tools.execute_tool_use({
                "name": "list_files",
                "input": {"path": "test.java", "recursive": True}
            })
            
            # Should return file info
            assert "test.java" in result
            assert "chars" in result
