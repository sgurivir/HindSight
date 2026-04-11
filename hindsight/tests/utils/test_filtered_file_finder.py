#!/usr/bin/env python3
"""
Tests for hindsight/utils/filtered_file_finder.py

Tests the FilteredFileFinder class which provides:
- File enumeration with filtering by extensions, directories, and files
- Include/exclude directory filtering
- Tree structure generation
- Static utility methods for directory filtering
"""

import os
import pytest
import tempfile
from pathlib import Path

from hindsight.utils.filtered_file_finder import (
    FilteredFileFinder,
    load_config_filters,
)


class TestFilteredFileFinder:
    """Tests for FilteredFileFinder class."""

    @pytest.fixture
    def temp_repo(self):
        """Create a temporary repository structure for testing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create directory structure:
            # src/
            #   main.py
            #   utils/
            #     helper.py
            # tests/
            #   test_main.py
            # build/
            #   output.txt
            # docs/
            #   readme.md
            
            src_dir = Path(tmpdir) / "src"
            src_dir.mkdir()
            (src_dir / "main.py").write_text("# main")
            
            utils_dir = src_dir / "utils"
            utils_dir.mkdir()
            (utils_dir / "helper.py").write_text("# helper")
            
            tests_dir = Path(tmpdir) / "tests"
            tests_dir.mkdir()
            (tests_dir / "test_main.py").write_text("# test")
            
            build_dir = Path(tmpdir) / "build"
            build_dir.mkdir()
            (build_dir / "output.txt").write_text("output")
            
            docs_dir = Path(tmpdir) / "docs"
            docs_dir.mkdir()
            (docs_dir / "readme.md").write_text("# readme")
            
            yield tmpdir

    def test_enumerate_all_files(self, temp_repo):
        """Test enumerating all files without filters."""
        finder = FilteredFileFinder(temp_repo)
        
        files = list(finder.enumerate())
        
        assert len(files) > 0
        # Should include files from all directories
        assert any("main.py" in f for f in files)
        assert any("helper.py" in f for f in files)

    def test_enumerate_with_extension_filter(self, temp_repo):
        """Test enumerating files with extension filter."""
        finder = FilteredFileFinder(
            temp_repo,
            extensions=['.py']
        )
        
        files = list(finder.enumerate())
        
        # Should only include .py files
        assert all(f.endswith('.py') for f in files)
        assert any("main.py" in f for f in files)
        assert not any("readme.md" in f for f in files)

    def test_enumerate_with_include_directories(self, temp_repo):
        """Test enumerating files with include directories filter."""
        finder = FilteredFileFinder(
            temp_repo,
            include_directories=["src"]
        )
        
        files = list(finder.enumerate())
        
        # Should only include files from src directory
        assert all("src" in f for f in files)
        assert not any("tests" in f for f in files)

    def test_enumerate_with_exclude_directories(self, temp_repo):
        """Test enumerating files with exclude directories filter."""
        finder = FilteredFileFinder(
            temp_repo,
            exclude_directories=["tests", "build"]
        )
        
        files = list(finder.enumerate())
        
        # Should not include files from excluded directories
        assert not any("tests" in f for f in files)
        assert not any("build" in f for f in files)
        assert any("src" in f for f in files)

    def test_enumerate_with_exclude_files(self, temp_repo):
        """Test enumerating files with exclude files filter."""
        finder = FilteredFileFinder(
            temp_repo,
            exclude_files=["src/main.py"]
        )
        
        files = list(finder.enumerate())
        
        # Should not include the excluded file
        assert not any(f == "src/main.py" for f in files)
        assert any("helper.py" in f for f in files)

    def test_get_returns_list(self, temp_repo):
        """Test that get() returns a list."""
        finder = FilteredFileFinder(temp_repo)
        
        files = finder.get()
        
        assert isinstance(files, list)
        assert len(files) > 0

    def test_get_tree_structure(self, temp_repo):
        """Test generating tree structure."""
        finder = FilteredFileFinder(
            temp_repo,
            extensions=['.py']
        )
        
        tree = finder.get_tree_structure()
        
        assert isinstance(tree, str)
        # Should contain directory markers
        assert "├──" in tree or "└──" in tree
        # Should contain file counts
        assert "(" in tree and ")" in tree

    def test_get_tree_structure_empty(self):
        """Test tree structure with no matching files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            finder = FilteredFileFinder(
                tmpdir,
                extensions=['.nonexistent']
            )
            
            tree = finder.get_tree_structure()
            
            assert "No files found" in tree


class TestFilteredFileFinderStaticMethods:
    """Tests for static methods of FilteredFileFinder."""

    def test_should_analyze_by_directory_filters_no_filters(self):
        """Test with no filters - should include all files."""
        result = FilteredFileFinder.should_analyze_by_directory_filters(
            "src/main.py"
        )
        
        assert result is True

    def test_should_analyze_by_directory_filters_include_match(self):
        """Test with include filter that matches."""
        result = FilteredFileFinder.should_analyze_by_directory_filters(
            "src/main.py",
            include_directories=["src"]
        )
        
        assert result is True

    def test_should_analyze_by_directory_filters_include_no_match(self):
        """Test with include filter that doesn't match."""
        result = FilteredFileFinder.should_analyze_by_directory_filters(
            "tests/test_main.py",
            include_directories=["src"]
        )
        
        assert result is False

    def test_should_analyze_by_directory_filters_exclude_match(self):
        """Test with exclude filter that matches."""
        result = FilteredFileFinder.should_analyze_by_directory_filters(
            "tests/test_main.py",
            exclude_directories=["tests"]
        )
        
        assert result is False

    def test_should_analyze_by_directory_filters_exclude_no_match(self):
        """Test with exclude filter that doesn't match."""
        result = FilteredFileFinder.should_analyze_by_directory_filters(
            "src/main.py",
            exclude_directories=["tests"]
        )
        
        assert result is True

    def test_should_analyze_by_directory_filters_exclude_files(self):
        """Test with exclude files filter."""
        result = FilteredFileFinder.should_analyze_by_directory_filters(
            "src/main.py",
            exclude_files=["src/main.py"]
        )
        
        assert result is False

    def test_should_analyze_include_parent_exclude_child(self):
        """Test include parent, exclude child - child should be excluded."""
        result = FilteredFileFinder.should_analyze_by_directory_filters(
            "src/tests/test_main.py",
            include_directories=["src"],
            exclude_directories=["src/tests"]
        )
        
        assert result is False

    def test_should_analyze_include_child_exclude_parent(self):
        """Test include child, exclude parent - child should be included."""
        result = FilteredFileFinder.should_analyze_by_directory_filters(
            "src/main/app.py",
            include_directories=["src/main"],
            exclude_directories=["src"]
        )
        
        assert result is True

    def test_should_analyze_same_include_exclude(self):
        """Test same directory in include and exclude - include takes precedence."""
        result = FilteredFileFinder.should_analyze_by_directory_filters(
            "statistics/data.py",
            include_directories=["statistics"],
            exclude_directories=["statistics"]
        )
        
        assert result is True

    def test_is_file_in_directory(self):
        """Test _is_file_in_directory helper method."""
        assert FilteredFileFinder._is_file_in_directory("src/main.py", "src") is True
        assert FilteredFileFinder._is_file_in_directory("src/utils/helper.py", "src") is True
        assert FilteredFileFinder._is_file_in_directory("tests/test.py", "src") is False

    def test_matches_directory_component(self):
        """Test _matches_directory_component helper method."""
        assert FilteredFileFinder._matches_directory_component("src/utils/helper.py", "utils") is True
        assert FilteredFileFinder._matches_directory_component("src/main.py", "utils") is False
        assert FilteredFileFinder._matches_directory_component("main.py", "src") is False


class TestFilteredFileFinderCountFiles:
    """Tests for count_files_with_supported_extensions static method."""

    @pytest.fixture
    def temp_repo_with_extensions(self):
        """Create a repository with various file extensions."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create files with different extensions
            # Note: ALL_SUPPORTED_EXTENSIONS includes .java, .c, .cpp, .go, etc. but NOT .py
            (Path(tmpdir) / "main.c").write_text("// c code")
            (Path(tmpdir) / "app.java").write_text("// java")
            (Path(tmpdir) / "readme.md").write_text("# readme")
            (Path(tmpdir) / "config.json").write_text("{}")
            
            src_dir = Path(tmpdir) / "src"
            src_dir.mkdir()
            (src_dir / "utils.cpp").write_text("// cpp utils")
            (src_dir / "helper.java").write_text("// helper")
            
            yield tmpdir

    def test_count_files_basic(self, temp_repo_with_extensions):
        """Test counting files with supported extensions."""
        count = FilteredFileFinder.count_files_with_supported_extensions(
            temp_repo_with_extensions
        )
        
        # Should count .c, .java, .cpp files (supported extensions)
        # main.c, app.java, utils.cpp, helper.java
        assert count >= 4

    def test_count_files_with_include_directories(self, temp_repo_with_extensions):
        """Test counting files with include directories filter."""
        count = FilteredFileFinder.count_files_with_supported_extensions(
            temp_repo_with_extensions,
            include_directories=["src"]
        )
        
        # Should only count files in src directory
        assert count == 2  # utils.cpp, helper.java

    def test_count_files_with_exclude_directories(self, temp_repo_with_extensions):
        """Test counting files with exclude directories filter."""
        count = FilteredFileFinder.count_files_with_supported_extensions(
            temp_repo_with_extensions,
            exclude_directories=["src"]
        )
        
        # Should exclude files in src directory
        assert count == 2  # main.c, app.java


class TestLoadConfigFilters:
    """Tests for load_config_filters function."""

    def test_load_valid_config(self):
        """Test loading valid config file."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            import json
            json.dump({
                "include_directories": ["src", "lib"],
                "exclude_directories": ["tests", "build"],
                "exclude_files": ["debug.py"]
            }, f)
            temp_path = f.name
        
        try:
            result = load_config_filters(temp_path)
            
            assert result["include_directories"] == ["src", "lib"]
            assert result["exclude_directories"] == ["tests", "build"]
            assert result["exclude_files"] == ["debug.py"]
        finally:
            os.unlink(temp_path)

    def test_load_partial_config(self):
        """Test loading config with only some fields."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            import json
            json.dump({
                "exclude_directories": ["tests"]
            }, f)
            temp_path = f.name
        
        try:
            result = load_config_filters(temp_path)
            
            assert result["include_directories"] == []
            assert result["exclude_directories"] == ["tests"]
            assert result["exclude_files"] == []
        finally:
            os.unlink(temp_path)

    def test_load_nonexistent_config(self):
        """Test loading nonexistent config file."""
        result = load_config_filters("/nonexistent/config.json")
        
        assert result["include_directories"] == []
        assert result["exclude_directories"] == []
        assert result["exclude_files"] == []

    def test_load_invalid_json_config(self):
        """Test loading invalid JSON config file."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            f.write("not valid json {")
            temp_path = f.name
        
        try:
            result = load_config_filters(temp_path)
            
            # Should return empty defaults on error
            assert result["include_directories"] == []
            assert result["exclude_directories"] == []
            assert result["exclude_files"] == []
        finally:
            os.unlink(temp_path)


class TestFilteredFileFinderPathNormalization:
    """Tests for path normalization in FilteredFileFinder."""

    def test_normalize_path_forward_slashes(self):
        """Test path normalization with forward slashes."""
        finder = FilteredFileFinder("/tmp")
        
        result = finder._normalize_path("src/utils/helper.py")
        
        assert result == "src/utils/helper.py"

    def test_normalize_path_backslashes(self):
        """Test path normalization with backslashes."""
        finder = FilteredFileFinder("/tmp")
        
        result = finder._normalize_path("src\\utils\\helper.py")
        
        assert result == "src/utils/helper.py"

    def test_normalize_path_leading_slash(self):
        """Test path normalization removes leading slash."""
        finder = FilteredFileFinder("/tmp")
        
        result = finder._normalize_path("/src/main.py")
        
        assert result == "src/main.py"


class TestFilteredFileFinderDirectoryExclusion:
    """Tests for directory exclusion logic."""

    @pytest.fixture
    def finder_with_excludes(self):
        """Create a finder with exclude directories."""
        with tempfile.TemporaryDirectory() as tmpdir:
            finder = FilteredFileFinder(
                tmpdir,
                exclude_directories=["tests", "Daemon/Shared"]
            )
            yield finder

    def test_is_directory_excluded_direct_match(self, finder_with_excludes):
        """Test direct match exclusion."""
        assert finder_with_excludes._is_directory_excluded("tests") is True

    def test_is_directory_excluded_subdirectory(self, finder_with_excludes):
        """Test subdirectory of excluded path."""
        assert finder_with_excludes._is_directory_excluded("tests/unit") is True

    def test_is_directory_excluded_relative_path(self, finder_with_excludes):
        """Test relative path exclusion."""
        assert finder_with_excludes._is_directory_excluded("Daemon/Shared") is True
        assert finder_with_excludes._is_directory_excluded("Daemon/Shared/Utils") is True

    def test_is_directory_excluded_no_match(self, finder_with_excludes):
        """Test non-excluded directory."""
        assert finder_with_excludes._is_directory_excluded("src") is False
        assert finder_with_excludes._is_directory_excluded("Daemon/Other") is False
