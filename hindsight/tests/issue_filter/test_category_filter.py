#!/usr/bin/env python3
"""
Tests for hindsight.issue_filter.category_filter module.
"""

import os
import sys
from pathlib import Path
from typing import List, Dict, Any

import pytest

# Add project root to path
project_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(project_root))

from hindsight.issue_filter.category_filter import CategoryBasedFilter


class TestCategoryBasedFilterInit:
    """Tests for CategoryBasedFilter initialization."""

    def test_init_default_categories(self):
        """Test initialization with default allowed categories."""
        filter_instance = CategoryBasedFilter()
        
        allowed = filter_instance.get_allowed_categories()
        
        assert 'logicBug' in allowed
        assert 'performance' in allowed
        assert len(allowed) == 2

    def test_init_with_additional_categories(self):
        """Test initialization with additional allowed categories."""
        additional = ['security', 'concurrency']
        filter_instance = CategoryBasedFilter(additional_allowed_categories=additional)
        
        allowed = filter_instance.get_allowed_categories()
        
        assert 'logicBug' in allowed
        assert 'performance' in allowed
        assert 'security' in allowed
        assert 'concurrency' in allowed
        assert len(allowed) == 4

    def test_init_with_empty_additional_categories(self):
        """Test initialization with empty additional categories list."""
        filter_instance = CategoryBasedFilter(additional_allowed_categories=[])
        
        allowed = filter_instance.get_allowed_categories()
        
        assert len(allowed) == 2  # Only default categories


class TestCategoryBasedFilterFilterIssues:
    """Tests for CategoryBasedFilter.filter_issues() method."""

    def test_filter_issues_keeps_allowed_categories(self, issues_with_various_categories):
        """Test that filter keeps issues with allowed categories."""
        filter_instance = CategoryBasedFilter()
        
        filtered = filter_instance.filter_issues(issues_with_various_categories)
        
        # Should only keep logicBug and performance
        assert len(filtered) == 2
        categories = [issue['category'] for issue in filtered]
        assert 'logicBug' in categories
        assert 'performance' in categories

    def test_filter_issues_removes_disallowed_categories(self, issues_with_various_categories):
        """Test that filter removes issues with disallowed categories."""
        filter_instance = CategoryBasedFilter()
        
        filtered = filter_instance.filter_issues(issues_with_various_categories)
        
        categories = [issue['category'] for issue in filtered]
        assert 'memory' not in categories
        assert 'codeQuality' not in categories
        assert 'divisionByZero' not in categories
        assert 'nilAccess' not in categories
        assert 'noIssue' not in categories
        assert 'general' not in categories

    def test_filter_issues_empty_list(self):
        """Test filtering empty list returns empty list."""
        filter_instance = CategoryBasedFilter()
        
        filtered = filter_instance.filter_issues([])
        
        assert filtered == []

    def test_filter_issues_none_input(self):
        """Test filtering None input returns None."""
        filter_instance = CategoryBasedFilter()
        
        filtered = filter_instance.filter_issues(None)
        
        assert filtered is None

    def test_filter_issues_all_allowed(self):
        """Test filtering when all issues have allowed categories."""
        filter_instance = CategoryBasedFilter()
        issues = [
            {'issue': 'Bug 1', 'category': 'logicBug', 'severity': 'high'},
            {'issue': 'Bug 2', 'category': 'performance', 'severity': 'medium'},
            {'issue': 'Bug 3', 'category': 'logicBug', 'severity': 'low'}
        ]
        
        filtered = filter_instance.filter_issues(issues)
        
        assert len(filtered) == 3

    def test_filter_issues_none_allowed(self):
        """Test filtering when no issues have allowed categories."""
        filter_instance = CategoryBasedFilter()
        issues = [
            {'issue': 'Issue 1', 'category': 'memory', 'severity': 'high'},
            {'issue': 'Issue 2', 'category': 'codeQuality', 'severity': 'low'},
            {'issue': 'Issue 3', 'category': 'general', 'severity': 'medium'}
        ]
        
        filtered = filter_instance.filter_issues(issues)
        
        assert len(filtered) == 0

    def test_filter_issues_missing_category(self):
        """Test filtering issues with missing category field."""
        filter_instance = CategoryBasedFilter()
        issues = [
            {'issue': 'Bug 1', 'category': 'logicBug', 'severity': 'high'},
            {'issue': 'Bug 2', 'severity': 'medium'},  # Missing category
            {'issue': 'Bug 3', 'category': '', 'severity': 'low'}  # Empty category
        ]
        
        filtered = filter_instance.filter_issues(issues)
        
        # Only the first issue should be kept
        assert len(filtered) == 1
        assert filtered[0]['category'] == 'logicBug'

    def test_filter_issues_non_dict_items(self):
        """Test filtering handles non-dict items gracefully."""
        filter_instance = CategoryBasedFilter()
        issues = [
            {'issue': 'Bug 1', 'category': 'logicBug', 'severity': 'high'},
            "not a dict",
            123,
            None
        ]
        
        filtered = filter_instance.filter_issues(issues)
        
        # Only the valid dict should be kept
        assert len(filtered) == 1

    def test_filter_issues_with_additional_categories(self):
        """Test filtering with additional allowed categories."""
        filter_instance = CategoryBasedFilter(additional_allowed_categories=['memory'])
        issues = [
            {'issue': 'Bug 1', 'category': 'logicBug', 'severity': 'high'},
            {'issue': 'Bug 2', 'category': 'memory', 'severity': 'medium'},
            {'issue': 'Bug 3', 'category': 'codeQuality', 'severity': 'low'}
        ]
        
        filtered = filter_instance.filter_issues(issues)
        
        assert len(filtered) == 2
        categories = [issue['category'] for issue in filtered]
        assert 'logicBug' in categories
        assert 'memory' in categories


class TestCategoryBasedFilterCategoryChecks:
    """Tests for category checking methods."""

    def test_is_category_allowed_true(self):
        """Test is_category_allowed returns True for allowed categories."""
        filter_instance = CategoryBasedFilter()
        
        assert filter_instance.is_category_allowed('logicBug') is True
        assert filter_instance.is_category_allowed('performance') is True

    def test_is_category_allowed_false(self):
        """Test is_category_allowed returns False for disallowed categories."""
        filter_instance = CategoryBasedFilter()
        
        assert filter_instance.is_category_allowed('memory') is False
        assert filter_instance.is_category_allowed('codeQuality') is False
        assert filter_instance.is_category_allowed('unknown') is False

    def test_is_category_allowed_empty_string(self):
        """Test is_category_allowed returns False for empty string."""
        filter_instance = CategoryBasedFilter()
        
        assert filter_instance.is_category_allowed('') is False

    def test_is_category_allowed_none(self):
        """Test is_category_allowed returns False for None."""
        filter_instance = CategoryBasedFilter()
        
        assert filter_instance.is_category_allowed(None) is False

    def test_is_category_allowed_with_whitespace(self):
        """Test is_category_allowed handles whitespace."""
        filter_instance = CategoryBasedFilter()
        
        assert filter_instance.is_category_allowed(' logicBug ') is True
        assert filter_instance.is_category_allowed('  performance  ') is True

    def test_is_category_filtered_true(self):
        """Test is_category_filtered returns True for filtered categories."""
        filter_instance = CategoryBasedFilter()
        
        assert filter_instance.is_category_filtered('memory') is True
        assert filter_instance.is_category_filtered('codeQuality') is True

    def test_is_category_filtered_false(self):
        """Test is_category_filtered returns False for allowed categories."""
        filter_instance = CategoryBasedFilter()
        
        assert filter_instance.is_category_filtered('logicBug') is False
        assert filter_instance.is_category_filtered('performance') is False


class TestCategoryBasedFilterCategoryManagement:
    """Tests for category management methods."""

    def test_get_allowed_categories(self):
        """Test get_allowed_categories returns copy of allowed categories."""
        filter_instance = CategoryBasedFilter()
        
        allowed1 = filter_instance.get_allowed_categories()
        allowed2 = filter_instance.get_allowed_categories()
        
        # Should be equal but not the same object
        assert allowed1 == allowed2
        
        # Modifying one should not affect the other
        allowed1.add('test')
        assert 'test' not in allowed2

    def test_get_filtered_categories(self):
        """Test get_filtered_categories returns allowed categories (backward compat)."""
        filter_instance = CategoryBasedFilter()
        
        filtered = filter_instance.get_filtered_categories()
        
        # For backward compatibility, returns allowed categories
        assert 'logicBug' in filtered
        assert 'performance' in filtered

    def test_add_allowed_category(self):
        """Test add_allowed_category adds new category."""
        filter_instance = CategoryBasedFilter()
        
        filter_instance.add_allowed_category('security')
        
        assert filter_instance.is_category_allowed('security') is True

    def test_add_allowed_category_empty_string(self):
        """Test add_allowed_category ignores empty string."""
        filter_instance = CategoryBasedFilter()
        initial_count = len(filter_instance.get_allowed_categories())
        
        filter_instance.add_allowed_category('')
        
        assert len(filter_instance.get_allowed_categories()) == initial_count

    def test_add_allowed_category_none(self):
        """Test add_allowed_category ignores None."""
        filter_instance = CategoryBasedFilter()
        initial_count = len(filter_instance.get_allowed_categories())
        
        filter_instance.add_allowed_category(None)
        
        assert len(filter_instance.get_allowed_categories()) == initial_count

    def test_add_allowed_category_with_whitespace(self):
        """Test add_allowed_category strips whitespace."""
        filter_instance = CategoryBasedFilter()
        
        filter_instance.add_allowed_category('  security  ')
        
        assert filter_instance.is_category_allowed('security') is True

    def test_add_filtered_category(self):
        """Test add_filtered_category removes from allowed (backward compat)."""
        filter_instance = CategoryBasedFilter()
        
        # Initially logicBug is allowed
        assert filter_instance.is_category_allowed('logicBug') is True
        
        # Add to filtered (removes from allowed)
        filter_instance.add_filtered_category('logicBug')
        
        assert filter_instance.is_category_allowed('logicBug') is False

    def test_remove_filtered_category(self):
        """Test remove_filtered_category adds to allowed (backward compat)."""
        filter_instance = CategoryBasedFilter()
        
        # Initially memory is not allowed
        assert filter_instance.is_category_allowed('memory') is False
        
        # Remove from filtered (adds to allowed)
        filter_instance.remove_filtered_category('memory')
        
        assert filter_instance.is_category_allowed('memory') is True


class TestCategoryBasedFilterEdgeCases:
    """Tests for edge cases and special scenarios."""

    def test_filter_preserves_issue_data(self):
        """Test that filtering preserves all issue data."""
        filter_instance = CategoryBasedFilter()
        original_issue = {
            'issue': 'Test bug',
            'category': 'logicBug',
            'severity': 'high',
            'file_path': '/path/to/file.py',
            'line_number': 42,
            'description': 'Detailed description',
            'suggestion': 'Fix suggestion',
            'custom_field': 'custom_value'
        }
        
        filtered = filter_instance.filter_issues([original_issue])
        
        assert len(filtered) == 1
        assert filtered[0] == original_issue

    def test_filter_large_list(self):
        """Test filtering a large list of issues."""
        filter_instance = CategoryBasedFilter()
        
        # Create 1000 issues with mixed categories
        issues = []
        categories = ['logicBug', 'performance', 'memory', 'codeQuality', 'general']
        for i in range(1000):
            issues.append({
                'issue': f'Issue {i}',
                'category': categories[i % len(categories)],
                'severity': 'medium'
            })
        
        filtered = filter_instance.filter_issues(issues)
        
        # Should keep only logicBug and performance (2 out of 5 categories)
        # 1000 / 5 * 2 = 400
        assert len(filtered) == 400

    def test_filter_case_sensitive(self):
        """Test that category matching is case-sensitive."""
        filter_instance = CategoryBasedFilter()
        issues = [
            {'issue': 'Bug 1', 'category': 'logicBug', 'severity': 'high'},
            {'issue': 'Bug 2', 'category': 'LogicBug', 'severity': 'high'},
            {'issue': 'Bug 3', 'category': 'LOGICBUG', 'severity': 'high'}
        ]
        
        filtered = filter_instance.filter_issues(issues)
        
        # Only exact match should be kept
        assert len(filtered) == 1
        assert filtered[0]['category'] == 'logicBug'
