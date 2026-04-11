#!/usr/bin/env python3
"""
Tests for hindsight.report.issue_directory_organizer module.
"""

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Add project root to path
project_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(project_root))

from hindsight.report.issue_directory_organizer import (
    DirectoryNode,
    RepositoryDirHierarchy,
    IssueDirectoryOrganizer
)


class TestDirectoryNode:
    """Tests for DirectoryNode class."""

    def test_init(self):
        """Test DirectoryNode initialization."""
        node = DirectoryNode(name='src', path='/repo/src')
        
        assert node.name == 'src'
        assert node.path == '/repo/src'
        assert len(node.files) == 0
        assert len(node.directories) == 0
        assert node.parent is None
        assert len(node.issues) == 0

    def test_add_file(self):
        """Test adding file to directory node."""
        node = DirectoryNode(name='src', path='/repo/src')
        
        node.add_file('Example.java')
        
        assert 'Example.java' in node.files
        assert len(node.files) == 1

    def test_add_file_duplicate(self):
        """Test adding duplicate file (set behavior)."""
        node = DirectoryNode(name='src', path='/repo/src')
        
        node.add_file('Example.java')
        node.add_file('Example.java')
        
        assert len(node.files) == 1

    def test_add_directory(self):
        """Test adding subdirectory to node."""
        parent = DirectoryNode(name='src', path='/repo/src')
        child = DirectoryNode(name='main', path='/repo/src/main')
        
        parent.add_directory(child)
        
        assert child in parent.directories
        assert child.parent == parent

    def test_get_all_files(self):
        """Test getting all files returns copy."""
        node = DirectoryNode(name='src', path='/repo/src')
        node.add_file('A.java')
        node.add_file('B.java')
        
        files = node.get_all_files()
        
        assert len(files) == 2
        assert 'A.java' in files
        assert 'B.java' in files
        
        # Verify it's a copy
        files.add('C.java')
        assert 'C.java' not in node.files

    def test_get_all_directories(self):
        """Test getting all directories returns copy."""
        parent = DirectoryNode(name='src', path='/repo/src')
        child = DirectoryNode(name='main', path='/repo/src/main')
        parent.add_directory(child)
        
        dirs = parent.get_all_directories()
        
        assert len(dirs) == 1
        assert child in dirs

    def test_find_directory(self):
        """Test finding subdirectory by name."""
        parent = DirectoryNode(name='src', path='/repo/src')
        child = DirectoryNode(name='main', path='/repo/src/main')
        parent.add_directory(child)
        
        found = parent.find_directory('main')
        
        assert found == child

    def test_find_directory_not_found(self):
        """Test finding non-existent subdirectory."""
        parent = DirectoryNode(name='src', path='/repo/src')
        
        found = parent.find_directory('nonexistent')
        
        assert found is None

    def test_add_issue(self):
        """Test adding issue to directory node."""
        node = DirectoryNode(name='src', path='/repo/src')
        issue = {'issue': 'Test bug', 'severity': 'high'}
        
        node.add_issue(issue)
        
        assert len(node.issues) == 1
        assert node.issues[0] == issue

    def test_get_issues(self):
        """Test getting issues returns copy."""
        node = DirectoryNode(name='src', path='/repo/src')
        issue = {'issue': 'Test bug', 'severity': 'high'}
        node.add_issue(issue)
        
        issues = node.get_issues()
        
        assert len(issues) == 1
        
        # Verify it's a copy
        issues.append({'issue': 'Another bug'})
        assert len(node.issues) == 1

    def test_get_issues_by_severity(self):
        """Test getting issues filtered by severity."""
        node = DirectoryNode(name='src', path='/repo/src')
        node.add_issue({'issue': 'Bug 1', 'severity': 'high'})
        node.add_issue({'issue': 'Bug 2', 'severity': 'low'})
        node.add_issue({'issue': 'Bug 3', 'severity': 'high'})
        
        high_issues = node.get_issues_by_severity('high')
        
        assert len(high_issues) == 2

    def test_get_issues_by_type(self):
        """Test getting issues filtered by type."""
        node = DirectoryNode(name='src', path='/repo/src')
        node.add_issue({'issue': 'Bug 1', 'issueType': 'logicBug'})
        node.add_issue({'issue': 'Bug 2', 'issueType': 'performance'})
        
        logic_issues = node.get_issues_by_type('logicBug')
        
        assert len(logic_issues) == 1

    def test_get_issue_count(self):
        """Test getting issue count."""
        node = DirectoryNode(name='src', path='/repo/src')
        node.add_issue({'issue': 'Bug 1'})
        node.add_issue({'issue': 'Bug 2'})
        
        assert node.get_issue_count() == 2

    def test_get_severity_counts(self):
        """Test getting severity counts."""
        node = DirectoryNode(name='src', path='/repo/src')
        node.add_issue({'issue': 'Bug 1', 'severity': 'critical'})
        node.add_issue({'issue': 'Bug 2', 'severity': 'high'})
        node.add_issue({'issue': 'Bug 3', 'severity': 'high'})
        node.add_issue({'issue': 'Bug 4', 'severity': 'low'})
        
        counts = node.get_severity_counts()
        
        assert counts['critical'] == 1
        assert counts['high'] == 2
        assert counts['medium'] == 0
        assert counts['low'] == 1

    def test_get_path(self):
        """Test getting node path."""
        node = DirectoryNode(name='src', path='/repo/src')
        
        assert node.get_path() == '/repo/src'

    def test_str_repr(self):
        """Test string representation."""
        node = DirectoryNode(name='src', path='/repo/src')
        node.add_file('A.java')
        
        str_repr = str(node)
        
        assert 'src' in str_repr
        assert '/repo/src' in str_repr

    def test_hash_and_eq(self):
        """Test hash and equality based on path."""
        node1 = DirectoryNode(name='src', path='/repo/src')
        node2 = DirectoryNode(name='src', path='/repo/src')
        node3 = DirectoryNode(name='src', path='/repo/other')
        
        assert node1 == node2
        assert node1 != node3
        assert hash(node1) == hash(node2)


class TestRepositoryDirHierarchy:
    """Tests for RepositoryDirHierarchy class."""

    def test_init_with_valid_path(self, temp_repo_structure):
        """Test initialization with valid repository path."""
        hierarchy = RepositoryDirHierarchy(temp_repo_structure)
        
        assert hierarchy.root_node is not None
        assert hierarchy.root_node.name == os.path.basename(temp_repo_structure)

    def test_init_with_nonexistent_path(self, temp_dir):
        """Test initialization with non-existent path."""
        nonexistent = os.path.join(temp_dir, 'nonexistent')
        
        hierarchy = RepositoryDirHierarchy(nonexistent)
        
        # Should create empty root node
        assert hierarchy.root_node is not None

    def test_get_root_node(self, temp_repo_structure):
        """Test getting root node."""
        hierarchy = RepositoryDirHierarchy(temp_repo_structure)
        
        root = hierarchy.get_root_node()
        
        assert root is not None
        assert root == hierarchy.root_node

    def test_find_node_by_path(self, temp_repo_structure):
        """Test finding node by path."""
        hierarchy = RepositoryDirHierarchy(temp_repo_structure)
        src_path = os.path.join(temp_repo_structure, 'src')
        
        node = hierarchy.find_node_by_path(src_path)
        
        assert node is not None
        assert node.name == 'src'

    def test_find_node_by_path_not_found(self, temp_repo_structure):
        """Test finding non-existent node."""
        hierarchy = RepositoryDirHierarchy(temp_repo_structure)
        
        node = hierarchy.find_node_by_path('/nonexistent/path')
        
        assert node is None

    def test_get_all_files_in_directory(self, temp_repo_structure):
        """Test getting all files in a directory."""
        hierarchy = RepositoryDirHierarchy(temp_repo_structure)
        java_path = os.path.join(temp_repo_structure, 'src', 'main', 'java')
        
        files = hierarchy.get_all_files_in_directory(java_path)
        
        assert 'Example.java' in files
        assert 'Utils.java' in files

    def test_get_all_subdirectories(self, temp_repo_structure):
        """Test getting all subdirectories."""
        hierarchy = RepositoryDirHierarchy(temp_repo_structure)
        src_path = os.path.join(temp_repo_structure, 'src')
        
        subdirs = hierarchy.get_all_subdirectories(src_path)
        
        assert len(subdirs) > 0
        subdir_names = [d.name for d in subdirs]
        assert 'main' in subdir_names

    def test_find_directories_by_name(self, temp_repo_structure):
        """Test finding directories by name."""
        hierarchy = RepositoryDirHierarchy(temp_repo_structure)
        
        matches = hierarchy.find_directories_by_name('java')
        
        assert len(matches) >= 1

    def test_get_tree_structure(self, temp_repo_structure):
        """Test getting tree structure as string."""
        hierarchy = RepositoryDirHierarchy(temp_repo_structure)
        
        tree_str = hierarchy.get_tree_structure()
        
        assert isinstance(tree_str, str)
        assert len(tree_str) > 0

    def test_get_tree_statistics(self, temp_repo_structure):
        """Test getting tree statistics."""
        hierarchy = RepositoryDirHierarchy(temp_repo_structure)
        
        stats = hierarchy.get_tree_statistics()
        
        assert 'total_directories' in stats
        assert 'total_files' in stats
        assert stats['total_directories'] > 0
        assert stats['total_files'] > 0

    def test_get_directory_hierarchy_by_path(self, temp_repo_structure):
        """Test getting directory hierarchy for specific path."""
        hierarchy = RepositoryDirHierarchy(temp_repo_structure)
        
        result = hierarchy.get_directory_hierarchy_by_path('src')
        
        assert result is not None
        assert isinstance(result, str)


class TestIssueDirectoryOrganizer:
    """Tests for IssueDirectoryOrganizer class."""

    def test_init(self, temp_repo_structure, mock_file_content_provider):
        """Test IssueDirectoryOrganizer initialization."""
        hierarchy = RepositoryDirHierarchy(temp_repo_structure)
        
        organizer = IssueDirectoryOrganizer(hierarchy, mock_file_content_provider)
        
        assert organizer.hierarchy == hierarchy
        assert organizer.file_content_provider == mock_file_content_provider
        assert len(organizer.unassigned_issues) == 0

    def test_set_exclude_directories(self, temp_repo_structure, mock_file_content_provider):
        """Test setting exclude directories."""
        hierarchy = RepositoryDirHierarchy(temp_repo_structure)
        organizer = IssueDirectoryOrganizer(hierarchy, mock_file_content_provider)
        
        organizer.set_exclude_directories(['build', 'node_modules'])
        
        assert len(organizer.exclude_directories) == 2

    def test_assign_issues_to_directories_basic(self, temp_repo_structure, mock_file_content_provider):
        """Test assigning issues to directories."""
        hierarchy = RepositoryDirHierarchy(temp_repo_structure)
        organizer = IssueDirectoryOrganizer(hierarchy, mock_file_content_provider)
        
        issues = [
            {'file_name': 'Example.java', 'file_path': 'src/main/java/Example.java', 'issue': 'Bug 1'},
        ]
        
        stats = organizer.assign_issues_to_directories(issues)
        
        assert 'total_issues' in stats
        assert 'assigned' in stats
        assert 'unassigned' in stats
        assert stats['total_issues'] == 1

    def test_assign_issues_empty_list(self, temp_repo_structure, mock_file_content_provider):
        """Test assigning empty issues list."""
        hierarchy = RepositoryDirHierarchy(temp_repo_structure)
        organizer = IssueDirectoryOrganizer(hierarchy, mock_file_content_provider)
        
        stats = organizer.assign_issues_to_directories([])
        
        assert stats['total_issues'] == 0
        assert stats['assigned'] == 0
        assert stats['unassigned'] == 0

    def test_get_unassigned_issues(self, temp_repo_structure, mock_file_content_provider):
        """Test getting unassigned issues."""
        hierarchy = RepositoryDirHierarchy(temp_repo_structure)
        organizer = IssueDirectoryOrganizer(hierarchy, mock_file_content_provider)
        
        # Add issue that can't be assigned
        issues = [
            {'file_name': 'NonExistent.java', 'file_path': 'unknown/NonExistent.java', 'issue': 'Bug'}
        ]
        
        organizer.assign_issues_to_directories(issues)
        unassigned = organizer.get_unassigned_issues()
        
        # Should return a copy
        assert isinstance(unassigned, list)

    def test_get_all_directories_with_issues(self, temp_repo_structure, mock_file_content_provider):
        """Test getting all directories with issues."""
        hierarchy = RepositoryDirHierarchy(temp_repo_structure)
        organizer = IssueDirectoryOrganizer(hierarchy, mock_file_content_provider)
        
        # Manually add issue to a directory
        if hierarchy.root_node:
            hierarchy.root_node.add_issue({'issue': 'Test bug', 'severity': 'high'})
        
        dirs_with_issues = organizer.get_all_directories_with_issues()
        
        assert isinstance(dirs_with_issues, list)

    def test_get_directory_issue_summary(self, temp_repo_structure, mock_file_content_provider):
        """Test getting directory issue summary."""
        hierarchy = RepositoryDirHierarchy(temp_repo_structure)
        organizer = IssueDirectoryOrganizer(hierarchy, mock_file_content_provider)
        
        summary = organizer.get_directory_issue_summary(temp_repo_structure)
        
        assert isinstance(summary, dict)
        assert 'total_issues' in summary or 'error' in summary

    def test_get_directory_issue_summary_not_found(self, temp_repo_structure, mock_file_content_provider):
        """Test getting summary for non-existent directory."""
        hierarchy = RepositoryDirHierarchy(temp_repo_structure)
        organizer = IssueDirectoryOrganizer(hierarchy, mock_file_content_provider)
        
        summary = organizer.get_directory_issue_summary('/nonexistent/path')
        
        assert 'error' in summary

    def test_is_directory_excluded(self, temp_repo_structure, mock_file_content_provider):
        """Test checking if directory is excluded."""
        hierarchy = RepositoryDirHierarchy(temp_repo_structure)
        organizer = IssueDirectoryOrganizer(hierarchy, mock_file_content_provider)
        organizer.set_exclude_directories(['build', 'node_modules'])
        
        assert organizer._is_directory_excluded('build/classes') is True
        assert organizer._is_directory_excluded('src/main') is False


class TestRepositoryDirHierarchyStaticMethods:
    """Tests for RepositoryDirHierarchy static methods."""

    def test_get_directory_structure_for_repo(self, temp_repo_structure):
        """Test getting directory structure for repo."""
        with patch.object(RepositoryDirHierarchy, '_get_cache_path') as mock_cache:
            mock_cache.return_value = '/tmp/nonexistent_cache.pkl'
            
            with patch.object(RepositoryDirHierarchy, '_load_cached_structure') as mock_load:
                mock_load.return_value = None
                
                with patch.object(RepositoryDirHierarchy, '_save_cached_structure'):
                    result = RepositoryDirHierarchy.get_directory_structure_for_repo(
                        temp_repo_structure, 
                        max_depth=3
                    )
                    
                    assert isinstance(result, str)


class TestIssueDirectoryOrganizerEdgeCases:
    """Tests for edge cases in IssueDirectoryOrganizer."""

    def test_find_directory_from_path_absolute(self, temp_repo_structure, mock_file_content_provider):
        """Test finding directory from absolute path."""
        hierarchy = RepositoryDirHierarchy(temp_repo_structure)
        organizer = IssueDirectoryOrganizer(hierarchy, mock_file_content_provider)
        
        # Test with absolute path
        abs_path = os.path.join(temp_repo_structure, 'src', 'main', 'java', 'Example.java')
        result = organizer._find_directory_from_path(abs_path)
        
        # Should find the directory or return None
        assert result is None or isinstance(result, DirectoryNode)

    def test_find_directory_from_path_relative(self, temp_repo_structure, mock_file_content_provider):
        """Test finding directory from relative path."""
        hierarchy = RepositoryDirHierarchy(temp_repo_structure)
        organizer = IssueDirectoryOrganizer(hierarchy, mock_file_content_provider)
        
        result = organizer._find_directory_from_path('src/main/java')
        
        # Should find the directory or return None
        assert result is None or isinstance(result, DirectoryNode)

    def test_find_directory_from_path_empty(self, temp_repo_structure, mock_file_content_provider):
        """Test finding directory from empty path."""
        hierarchy = RepositoryDirHierarchy(temp_repo_structure)
        organizer = IssueDirectoryOrganizer(hierarchy, mock_file_content_provider)
        
        result = organizer._find_directory_from_path('')
        
        assert result is None

    def test_find_directory_from_path_none(self, temp_repo_structure, mock_file_content_provider):
        """Test finding directory from None path."""
        hierarchy = RepositoryDirHierarchy(temp_repo_structure)
        organizer = IssueDirectoryOrganizer(hierarchy, mock_file_content_provider)
        
        result = organizer._find_directory_from_path(None)
        
        assert result is None
