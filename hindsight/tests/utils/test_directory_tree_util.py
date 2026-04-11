"""
Tests for DirectoryTreeUtil recursive directory listing functionality.

File: hindsight/tests/utils/test_directory_tree_util.py
"""

import pytest
import tempfile
from pathlib import Path

from hindsight.utils.directory_tree_util import DirectoryTreeUtil


@pytest.fixture
def temp_repo_structure():
    """Create a temporary directory structure for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create nested directory structure
        # repo/
        #   src/
        #     components/
        #       Button.java
        #       Header.java
        #     utils/
        #       helpers.java
        #     main.java
        #   tests/
        #     test_main.java
        #   README.md
        
        repo = Path(tmpdir)
        
        # Create directories
        (repo / "src" / "components").mkdir(parents=True)
        (repo / "src" / "utils").mkdir(parents=True)
        (repo / "tests").mkdir(parents=True)
        
        # Create files with content (using .java extension which is in ALL_SUPPORTED_EXTENSIONS)
        (repo / "src" / "components" / "Button.java").write_text("// Button component\nclass Button {}\n")
        (repo / "src" / "components" / "Header.java").write_text("// Header component\nclass Header {}\n")
        (repo / "src" / "utils" / "helpers.java").write_text("// Helper functions\nclass Helpers {}\n")
        (repo / "src" / "main.java").write_text("// Main entry point\nclass Main {}\n")
        (repo / "tests" / "test_main.java").write_text("// Tests\nclass TestMain {}\n")
        (repo / "README.md").write_text("# Test Repository\n")
        
        yield repo


class TestDirectoryTreeUtilBackwardCompatibility:
    """Tests to ensure backward compatibility with existing callers."""
    
    def test_get_directory_listing_without_new_params(self, temp_repo_structure):
        """Test that calling without new parameters works (backward compatibility)."""
        # This is how existing code calls the method
        result = DirectoryTreeUtil.get_directory_listing(
            repo_path=str(temp_repo_structure),
            relative_path="src"
        )
        
        assert result is not None
        assert "src/" in result
        assert "|-- " in result
    
    def test_get_directory_listing_default_is_non_recursive(self, temp_repo_structure):
        """Test that default behavior is non-recursive."""
        result = DirectoryTreeUtil.get_directory_listing(
            repo_path=str(temp_repo_structure),
            relative_path="src"
        )
        
        # Should show immediate children
        assert "|-- components/" in result
        assert "|-- utils/" in result
        assert "main.java" in result
        
        # Should NOT show nested content (files inside components/)
        assert "Button.java" not in result
        assert "Header.java" not in result
    
    def test_get_directory_listing_explicit_false_matches_default(self, temp_repo_structure):
        """Test that explicit recursive=False matches default behavior."""
        default_result = DirectoryTreeUtil.get_directory_listing(
            repo_path=str(temp_repo_structure),
            relative_path="src"
        )
        
        explicit_result = DirectoryTreeUtil.get_directory_listing(
            repo_path=str(temp_repo_structure),
            relative_path="src",
            recursive=False
        )
        
        assert default_result == explicit_result


class TestDirectoryTreeUtilRecursive:
    """Tests for new recursive directory listing functionality."""
    
    def test_get_directory_listing_recursive_shows_nested(self, temp_repo_structure):
        """Test recursive listing shows nested directories and files."""
        result = DirectoryTreeUtil.get_directory_listing(
            repo_path=str(temp_repo_structure),
            relative_path="src",
            recursive=True
        )
        
        # Should show nested structure
        assert "src/" in result
        assert "components/" in result
        assert "Button.java" in result
        assert "Header.java" in result
        assert "utils/" in result
        assert "helpers.java" in result
        assert "main.java" in result
    
    def test_get_directory_listing_recursive_tree_formatting(self, temp_repo_structure):
        """Test recursive listing has proper tree formatting."""
        result = DirectoryTreeUtil.get_directory_listing(
            repo_path=str(temp_repo_structure),
            relative_path="src",
            recursive=True
        )
        
        # Should have tree connectors for nested items
        assert "|   " in result or "    " in result  # Indentation for nested items
    
    def test_get_directory_listing_max_depth_limits_recursion(self, temp_repo_structure):
        """Test max_depth parameter limits recursion depth."""
        shallow_result = DirectoryTreeUtil.get_directory_listing(
            repo_path=str(temp_repo_structure),
            relative_path="src",
            recursive=True,
            max_depth=0
        )
        
        deep_result = DirectoryTreeUtil.get_directory_listing(
            repo_path=str(temp_repo_structure),
            relative_path="src",
            recursive=True,
            max_depth=10
        )
        
        # Shallow result should have less content
        assert len(shallow_result) <= len(deep_result)
        
        # Deep result should include nested files
        assert "Button.java" in deep_result
    
    def test_get_directory_listing_file_path_ignores_recursive(self, temp_repo_structure):
        """Test that file paths work correctly with recursive parameter."""
        result = DirectoryTreeUtil.get_directory_listing(
            repo_path=str(temp_repo_structure),
            relative_path="src/main.java",
            recursive=True  # Should be ignored for files
        )
        
        # Should return file info, not directory listing
        assert "chars" in result
        assert "main.java" in result
    
    def test_get_directory_listing_invalid_path(self, temp_repo_structure):
        """Test handling of invalid paths."""
        result = DirectoryTreeUtil.get_directory_listing(
            repo_path=str(temp_repo_structure),
            relative_path="nonexistent",
            recursive=True
        )
        
        assert "not found" in result.lower()
    
    def test_get_directory_listing_root_directory(self, temp_repo_structure):
        """Test recursive listing from root directory."""
        result = DirectoryTreeUtil.get_directory_listing(
            repo_path=str(temp_repo_structure),
            relative_path=".",
            recursive=True
        )
        
        # Should include all directories
        assert "src/" in result
        assert "tests/" in result


class TestDirectoryTreeUtilEdgeCases:
    """Tests for edge cases and error handling."""
    
    def test_empty_directory(self, temp_repo_structure):
        """Test handling of empty directories."""
        # Create empty directory
        empty_dir = temp_repo_structure / "empty"
        empty_dir.mkdir()
        
        result = DirectoryTreeUtil.get_directory_listing(
            repo_path=str(temp_repo_structure),
            relative_path="empty",
            recursive=True
        )
        
        assert "empty/" in result
        assert "no supported files" in result.lower() or len(result.split('\n')) <= 2
    
    def test_directory_with_unsupported_extensions(self, temp_repo_structure):
        """Test that unsupported file extensions are filtered."""
        # Create file with unsupported extension
        (temp_repo_structure / "src" / "data.xyz").write_text('some data')
        
        result = DirectoryTreeUtil.get_directory_listing(
            repo_path=str(temp_repo_structure),
            relative_path="src",
            recursive=True
        )
        
        # .xyz files should not appear (not in DEFAULT_EXTS)
        assert "data.xyz" not in result
    
    def test_deeply_nested_structure(self, temp_repo_structure):
        """Test handling of deeply nested directory structures."""
        # Create deeply nested structure
        deep_path = temp_repo_structure / "level1" / "level2" / "level3" / "level4"
        deep_path.mkdir(parents=True)
        (deep_path / "deep_file.java").write_text("// Deep file")
        
        result = DirectoryTreeUtil.get_directory_listing(
            repo_path=str(temp_repo_structure),
            relative_path="level1",
            recursive=True,
            max_depth=10
        )
        
        # Should show all levels
        assert "level1/" in result
        assert "level2/" in result
        assert "level3/" in result
        assert "level4/" in result
        assert "deep_file.java" in result
    
    def test_max_depth_zero_shows_only_immediate(self, temp_repo_structure):
        """Test that max_depth=0 shows only immediate children."""
        result = DirectoryTreeUtil.get_directory_listing(
            repo_path=str(temp_repo_structure),
            relative_path="src",
            recursive=True,
            max_depth=0
        )
        
        # Should show immediate children (directories and files)
        assert "components/" in result
        assert "utils/" in result
        assert "main.java" in result
        
        # Should NOT show nested content
        assert "Button.java" not in result
        assert "helpers.java" not in result
