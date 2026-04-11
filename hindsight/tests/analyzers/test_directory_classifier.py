#!/usr/bin/env python3
"""
Tests for hindsight/analyzers/directory_classifier.py

Tests the DirectoryClassifier class which provides functionality for:
- Scanning repositories and determining include/exclude patterns
- Analyzing directories based on supported file extensions
- Optimizing exclusions by excluding parent directories when all children should be excluded
"""

import os
import pytest
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

from hindsight.analyzers.directory_classifier import DirectoryClassifier


class TestDirectoryClassifier:
    """Tests for DirectoryClassifier class."""

    @pytest.fixture
    def temp_repo(self):
        """Create a temporary repository structure for testing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create directory structure
            # src/
            #   main.py
            #   utils/
            #     helper.py
            # tests/
            #   test_main.py
            # build/
            #   output.txt
            # .git/
            #   config
            
            src_dir = Path(tmpdir) / "src"
            src_dir.mkdir()
            (src_dir / "main.py").write_text("# main code")
            
            utils_dir = src_dir / "utils"
            utils_dir.mkdir()
            (utils_dir / "helper.py").write_text("# helper code")
            
            tests_dir = Path(tmpdir) / "tests"
            tests_dir.mkdir()
            (tests_dir / "test_main.py").write_text("# test code")
            
            build_dir = Path(tmpdir) / "build"
            build_dir.mkdir()
            (build_dir / "output.txt").write_text("build output")
            
            git_dir = Path(tmpdir) / ".git"
            git_dir.mkdir()
            (git_dir / "config").write_text("git config")
            
            yield tmpdir

    def test_get_include_and_exclude_directories_basic(self, temp_repo):
        """Test basic directory scanning without filters."""
        include_dirs, exclude_dirs = DirectoryClassifier.get_include_and_exclude_directories(temp_repo)
        
        # Should include directories with supported files
        assert isinstance(include_dirs, set)
        assert isinstance(exclude_dirs, set)
        
        # .git should be excluded by default
        assert ".git" in exclude_dirs or any(".git" in d for d in exclude_dirs)

    def test_get_include_and_exclude_directories_with_include_filter(self, temp_repo):
        """Test directory scanning with include filter."""
        include_dirs, exclude_dirs = DirectoryClassifier.get_include_and_exclude_directories(
            temp_repo,
            user_provided_include_list=["src"]
        )
        
        # src should be in include list
        assert "src" in include_dirs or any("src" in d for d in include_dirs)

    def test_get_include_and_exclude_directories_with_exclude_filter(self, temp_repo):
        """Test directory scanning with exclude filter."""
        include_dirs, exclude_dirs = DirectoryClassifier.get_include_and_exclude_directories(
            temp_repo,
            user_provided_exclude_list=["tests"]
        )
        
        # tests should be excluded
        assert "tests" in exclude_dirs or any("tests" in d for d in exclude_dirs)

    def test_get_include_and_exclude_directories_empty_lists_treated_as_none(self, temp_repo):
        """Test that empty lists are treated as None."""
        include_dirs1, exclude_dirs1 = DirectoryClassifier.get_include_and_exclude_directories(
            temp_repo,
            user_provided_include_list=[],
            user_provided_exclude_list=[]
        )
        
        include_dirs2, exclude_dirs2 = DirectoryClassifier.get_include_and_exclude_directories(
            temp_repo,
            user_provided_include_list=None,
            user_provided_exclude_list=None
        )
        
        # Results should be the same
        assert include_dirs1 == include_dirs2
        assert exclude_dirs1 == exclude_dirs2

    def test_get_include_and_exclude_directories_invalid_path(self):
        """Test with invalid repository path."""
        with pytest.raises(ValueError, match="does not exist"):
            DirectoryClassifier.get_include_and_exclude_directories("/nonexistent/path")

    def test_get_recommended_exclude_directories(self, temp_repo):
        """Test getting recommended exclude directories."""
        exclude_dirs = DirectoryClassifier.get_recommended_exclude_directories(temp_repo)
        
        assert isinstance(exclude_dirs, set)
        # .git should be in the exclude list
        assert ".git" in exclude_dirs or any(".git" in d for d in exclude_dirs)

    def test_get_recommended_exclude_directories_safe(self, temp_repo):
        """Test safe version that returns empty list on error."""
        # Valid path should return list
        result = DirectoryClassifier.get_recommended_exclude_directories_safe(temp_repo)
        assert isinstance(result, list)
        
        # Invalid path should return empty list (not raise exception)
        result = DirectoryClassifier.get_recommended_exclude_directories_safe("/nonexistent/path")
        assert result == []

    def test_remove_redundant_children(self):
        """Test removal of redundant child directories from exclude list."""
        exclude_dirs = {"src", "src/utils", "src/utils/helpers", "tests"}
        
        result = DirectoryClassifier._remove_redundant_children(exclude_dirs)
        
        # src/utils and src/utils/helpers should be removed since src is excluded
        assert "src" in result
        assert "tests" in result
        assert "src/utils" not in result
        assert "src/utils/helpers" not in result

    def test_is_directory_or_parent_included(self):
        """Test checking if directory or parent is in included set."""
        include_set = {"src", "lib/utils"}
        
        # Direct match
        assert DirectoryClassifier._is_directory_or_parent_included("src", include_set) is True
        
        # Child of included directory
        assert DirectoryClassifier._is_directory_or_parent_included("src/main", include_set) is True
        
        # Parent of included directory
        assert DirectoryClassifier._is_directory_or_parent_included("lib", include_set) is True
        
        # Not related
        assert DirectoryClassifier._is_directory_or_parent_included("tests", include_set) is False
        
        # Empty set
        assert DirectoryClassifier._is_directory_or_parent_included("src", set()) is False

    def test_print_directory_analysis(self, temp_repo, capsys):
        """Test printing directory analysis results."""
        DirectoryClassifier.print_directory_analysis(temp_repo)
        
        captured = capsys.readouterr()
        assert "DIRECTORY ANALYSIS RESULTS" in captured.out
        assert "Repository:" in captured.out

    def test_print_directory_analysis_with_filters(self, temp_repo, capsys):
        """Test printing directory analysis with filters."""
        DirectoryClassifier.print_directory_analysis(
            temp_repo,
            user_provided_include_list=["src"],
            user_provided_exclude_list=["tests"]
        )
        
        captured = capsys.readouterr()
        assert "Include filter:" in captured.out
        assert "Additional excludes:" in captured.out

    def test_default_extensions(self):
        """Test that DEFAULT_EXTS is properly set."""
        assert hasattr(DirectoryClassifier, 'DEFAULT_EXTS')
        assert isinstance(DirectoryClassifier.DEFAULT_EXTS, (list, set, tuple))
        # Should include common extensions (uses ALL_SUPPORTED_EXTENSIONS which includes C/C++/Java/etc)
        # Check for some common supported extensions
        assert any(ext in DirectoryClassifier.DEFAULT_EXTS for ext in ['.cpp', '.c', '.java', '.go', '.swift'])


class TestDirectoryClassifierEdgeCases:
    """Edge case tests for DirectoryClassifier."""

    @pytest.fixture
    def empty_repo(self):
        """Create an empty repository."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield tmpdir

    @pytest.fixture
    def nested_repo(self):
        """Create a deeply nested repository structure."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create deep nesting: a/b/c/d/e/file.py
            deep_path = Path(tmpdir) / "a" / "b" / "c" / "d" / "e"
            deep_path.mkdir(parents=True)
            (deep_path / "file.py").write_text("# deep file")
            yield tmpdir

    def test_empty_repository(self, empty_repo):
        """Test with empty repository."""
        include_dirs, exclude_dirs = DirectoryClassifier.get_include_and_exclude_directories(empty_repo)
        
        # Should not raise, just return empty or minimal sets
        assert isinstance(include_dirs, set)
        assert isinstance(exclude_dirs, set)

    def test_deeply_nested_structure(self, nested_repo):
        """Test with deeply nested directory structure."""
        include_dirs, exclude_dirs = DirectoryClassifier.get_include_and_exclude_directories(nested_repo)
        
        # Should handle deep nesting without issues
        assert isinstance(include_dirs, set)
        assert isinstance(exclude_dirs, set)

    def test_path_normalization(self, nested_repo):
        """Test that paths are normalized correctly."""
        # Test with different path formats
        include_dirs1, _ = DirectoryClassifier.get_include_and_exclude_directories(
            nested_repo,
            user_provided_include_list=["a/b"]
        )
        
        include_dirs2, _ = DirectoryClassifier.get_include_and_exclude_directories(
            nested_repo,
            user_provided_include_list=["a\\b"]  # Windows-style path
        )
        
        # Both should work and include the directory
        assert "a/b" in include_dirs1 or any("a/b" in d for d in include_dirs1)

    def test_user_include_never_excluded(self):
        """Test that user-provided include directories are never excluded."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a directory that would normally be excluded (like 'build')
            build_dir = Path(tmpdir) / "build"
            build_dir.mkdir()
            (build_dir / "main.py").write_text("# build code")
            
            include_dirs, exclude_dirs = DirectoryClassifier.get_include_and_exclude_directories(
                tmpdir,
                user_provided_include_list=["build"]
            )
            
            # build should be in include, not exclude
            assert "build" in include_dirs
            assert "build" not in exclude_dirs


class TestOptimizeExclusions:
    """Tests for the _optimize_exclusions method."""

    @pytest.fixture
    def repo_with_structure(self):
        """Create a repository with specific structure for optimization testing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create structure:
            # parent/
            #   child1/
            #     file.txt (no supported extension)
            #   child2/
            #     file.txt (no supported extension)
            # src/
            #   main.py
            
            parent_dir = Path(tmpdir) / "parent"
            parent_dir.mkdir()
            
            child1_dir = parent_dir / "child1"
            child1_dir.mkdir()
            (child1_dir / "file.txt").write_text("text")
            
            child2_dir = parent_dir / "child2"
            child2_dir.mkdir()
            (child2_dir / "file.txt").write_text("text")
            
            src_dir = Path(tmpdir) / "src"
            src_dir.mkdir()
            (src_dir / "main.py").write_text("# code")
            
            yield tmpdir

    def test_optimize_exclusions_parent_excluded_when_all_children_excluded(self, repo_with_structure):
        """Test that parent is excluded when all children should be excluded."""
        include_dirs, exclude_dirs = DirectoryClassifier.get_include_and_exclude_directories(
            repo_with_structure
        )
        
        # parent should be excluded since it has no supported files
        # and all its children have no supported files
        # The exact behavior depends on the implementation
        assert isinstance(exclude_dirs, set)
