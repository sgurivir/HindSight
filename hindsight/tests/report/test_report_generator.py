#!/usr/bin/env python3
"""
Tests for hindsight.report.report_generator module.
"""

import os
import sys
import json
import tempfile
from pathlib import Path
from datetime import datetime

import pytest

# Add project root to path
project_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(project_root))

from hindsight.report.report_generator import (
    read_llm_output_files,
    extract_directory_from_file,
    build_directory_tree,
    calculate_stats,
    generate_html_report,
    format_issue_type_name,
    generate_directory_tree_html,
    generate_sidebar_items,
    format_directory_name
)


class TestExtractDirectoryFromFile:
    """Tests for extract_directory_from_file function."""

    def test_extract_directory_unix_path(self):
        """Test extracting directory from Unix-style path."""
        result = extract_directory_from_file('src/main/java/Example.java')
        assert result == 'src/main/java'

    def test_extract_directory_windows_path(self):
        """Test extracting directory from Windows-style path."""
        result = extract_directory_from_file('src\\main\\java\\Example.java')
        assert result == 'src\\main\\java'

    def test_extract_directory_root_file(self):
        """Test extracting directory for file in root."""
        result = extract_directory_from_file('Example.java')
        assert result == 'root'

    def test_extract_directory_empty_path(self):
        """Test extracting directory from empty path."""
        result = extract_directory_from_file('')
        assert result == 'uncategorized'

    def test_extract_directory_none_path(self):
        """Test extracting directory from None path."""
        result = extract_directory_from_file(None)
        assert result == 'uncategorized'

    def test_extract_directory_unknown(self):
        """Test extracting directory for Unknown path."""
        result = extract_directory_from_file('Unknown')
        assert result == 'Unknown'

    def test_extract_directory_name_only(self):
        """Test extracting directory when path is just a directory name."""
        result = extract_directory_from_file('common')
        assert result == 'common'

    def test_extract_directory_with_extension(self):
        """Test extracting directory for file with extension but no path."""
        result = extract_directory_from_file('file.c')
        assert result == 'root'


class TestBuildDirectoryTree:
    """Tests for build_directory_tree function."""

    def test_build_directory_tree_basic(self, issues_for_report):
        """Test building directory tree from issues."""
        tree, counts = build_directory_tree(issues_for_report)
        
        assert isinstance(tree, dict)
        assert isinstance(counts, dict)

    def test_build_directory_tree_empty_issues(self):
        """Test building directory tree from empty issues list."""
        tree, counts = build_directory_tree([])
        
        assert tree == {}
        assert counts == {}

    def test_build_directory_tree_counts(self):
        """Test that directory counts are correct."""
        issues = [
            {'file_path': 'src/main/java/A.java'},
            {'file_path': 'src/main/java/B.java'},
            {'file_path': 'src/test/java/C.java'},
        ]
        
        tree, counts = build_directory_tree(issues)
        
        assert counts['src/main/java'] == 2
        assert counts['src/test/java'] == 1

    def test_build_directory_tree_nested_structure(self):
        """Test that nested directory structure is built correctly."""
        issues = [
            {'file_path': 'src/main/java/Example.java'},
        ]
        
        tree, counts = build_directory_tree(issues)
        
        # Tree should have nested structure
        assert 'src' in tree


class TestCalculateStats:
    """Tests for calculate_stats function."""

    def test_calculate_stats_basic(self, issues_for_report):
        """Test calculating statistics from issues."""
        stats = calculate_stats(issues_for_report)
        
        assert 'total' in stats
        assert 'critical' in stats
        assert 'high' in stats
        assert 'medium' in stats
        assert 'low' in stats
        assert 'directories' in stats
        assert 'directory_tree' in stats

    def test_calculate_stats_total_count(self, issues_for_report):
        """Test that total count is correct."""
        stats = calculate_stats(issues_for_report)
        
        assert stats['total'] == len(issues_for_report)

    def test_calculate_stats_severity_counts(self):
        """Test that severity counts are correct."""
        issues = [
            {'severity': 'critical', 'file_path': 'a.java'},
            {'severity': 'critical', 'file_path': 'b.java'},
            {'severity': 'high', 'file_path': 'c.java'},
            {'severity': 'medium', 'file_path': 'd.java'},
            {'severity': 'low', 'file_path': 'e.java'},
            {'severity': 'low', 'file_path': 'f.java'},
        ]
        
        stats = calculate_stats(issues)
        
        assert stats['critical'] == 2
        assert stats['high'] == 1
        assert stats['medium'] == 1
        assert stats['low'] == 2

    def test_calculate_stats_empty_issues(self):
        """Test calculating stats from empty issues list."""
        stats = calculate_stats([])
        
        assert stats['total'] == 0
        assert stats['critical'] == 0
        assert stats['high'] == 0
        assert stats['medium'] == 0
        assert stats['low'] == 0


class TestGenerateHtmlReport:
    """Tests for generate_html_report function."""

    def test_generate_html_report_basic(self, issues_for_report, temp_dir):
        """Test generating HTML report."""
        output_file = os.path.join(temp_dir, 'report.html')
        
        result = generate_html_report(issues_for_report, output_file=output_file)
        
        assert os.path.exists(result)
        
        with open(result, 'r') as f:
            content = f.read()
        
        assert '<!DOCTYPE html>' in content
        assert 'HindSight' in content

    def test_generate_html_report_with_project_name(self, issues_for_report, temp_dir):
        """Test generating HTML report with project name."""
        output_file = os.path.join(temp_dir, 'report.html')
        
        result = generate_html_report(
            issues_for_report, 
            output_file=output_file,
            project_name='TestProject'
        )
        
        with open(result, 'r') as f:
            content = f.read()
        
        assert 'TestProject' in content

    def test_generate_html_report_with_analysis_type(self, issues_for_report, temp_dir):
        """Test generating HTML report with custom analysis type."""
        output_file = os.path.join(temp_dir, 'report.html')
        
        result = generate_html_report(
            issues_for_report,
            output_file=output_file,
            analysis_type='Trace Analysis'
        )
        
        with open(result, 'r') as f:
            content = f.read()
        
        assert 'Trace Analysis' in content

    def test_generate_html_report_empty_issues(self, temp_dir):
        """Test generating HTML report with empty issues."""
        output_file = os.path.join(temp_dir, 'report.html')
        
        result = generate_html_report([], output_file=output_file)
        
        assert os.path.exists(result)

    def test_generate_html_report_returns_content_when_no_file(self, issues_for_report):
        """Test that generate_html_report returns content when output_file is None."""
        result = generate_html_report(issues_for_report, output_file=None)
        
        assert isinstance(result, str)
        assert '<!DOCTYPE html>' in result

    def test_generate_html_report_contains_issues(self, temp_dir):
        """Test that HTML report contains issue data."""
        issues = [
            {
                'issue': 'Test issue description',
                'severity': 'high',
                'category': 'logicBug',
                'file_path': 'src/Example.java',
                'function_name': 'testFunction',
                'suggestion': 'Fix the issue'
            }
        ]
        output_file = os.path.join(temp_dir, 'report.html')
        
        generate_html_report(issues, output_file=output_file)
        
        with open(output_file, 'r') as f:
            content = f.read()
        
        # Check that issue data is in the JavaScript
        assert 'Test issue description' in content
        assert 'testFunction' in content


class TestFormatIssueTypeName:
    """Tests for format_issue_type_name function."""

    def test_format_issue_type_name_camel_case(self):
        """Test formatting camelCase issue type."""
        result = format_issue_type_name('logicBug')
        assert result == 'Logic Bug'

    def test_format_issue_type_name_unknown(self):
        """Test formatting unknown issue type."""
        result = format_issue_type_name('unknown')
        assert result == 'Unknown'

    def test_format_issue_type_name_consecutive_caps(self):
        """Test formatting issue type with consecutive capitals."""
        result = format_issue_type_name('HTTPRequest')
        assert 'HTTP' in result

    def test_format_issue_type_name_underscore(self):
        """Test formatting issue type with underscores."""
        result = format_issue_type_name('null_pointer')
        assert result == 'Null Pointer'

    def test_format_issue_type_name_single_word(self):
        """Test formatting single word issue type."""
        result = format_issue_type_name('performance')
        assert result == 'Performance'


class TestGenerateDirectoryTreeHtml:
    """Tests for generate_directory_tree_html function."""

    def test_generate_directory_tree_html_basic(self):
        """Test generating directory tree HTML."""
        tree = {
            'src': {
                '_count': 5,
                '_children': {
                    'main': {
                        '_count': 3,
                        '_children': {}
                    }
                }
            }
        }
        
        result = generate_directory_tree_html(tree)
        
        assert 'src' in result
        assert 'main' in result
        assert 'sidebar-item' in result

    def test_generate_directory_tree_html_empty(self):
        """Test generating directory tree HTML from empty tree."""
        result = generate_directory_tree_html({})
        
        assert result == ''

    def test_generate_directory_tree_html_with_counts(self):
        """Test that directory tree HTML includes counts."""
        tree = {
            'src': {
                '_count': 10,
                '_children': {}
            }
        }
        
        result = generate_directory_tree_html(tree)
        
        assert '(10)' in result


class TestGenerateSidebarItems:
    """Tests for generate_sidebar_items function."""

    def test_generate_sidebar_items_basic(self):
        """Test generating sidebar items."""
        directories = {
            'src/main/java': 5,
            'src/test/java': 3
        }
        tree = {
            'src': {
                '_count': 8,
                '_children': {
                    'main': {
                        '_count': 5,
                        '_children': {}
                    },
                    'test': {
                        '_count': 3,
                        '_children': {}
                    }
                }
            }
        }
        
        result = generate_sidebar_items(directories, tree)
        
        assert 'sidebar-item' in result

    def test_generate_sidebar_items_with_unknown(self):
        """Test generating sidebar items with Unknown directory."""
        directories = {
            'Unknown': 2
        }
        tree = {}
        
        result = generate_sidebar_items(directories, tree)
        
        assert 'Unknown' in result

    def test_generate_sidebar_items_with_root(self):
        """Test generating sidebar items with root directory."""
        directories = {
            'root': 3
        }
        tree = {}
        
        result = generate_sidebar_items(directories, tree)
        
        assert 'Root Directory' in result


class TestFormatDirectoryName:
    """Tests for format_directory_name function."""

    def test_format_directory_name_uncategorized(self):
        """Test formatting uncategorized directory."""
        result = format_directory_name('uncategorized')
        assert result == 'Uncategorized'

    def test_format_directory_name_root(self):
        """Test formatting root directory."""
        result = format_directory_name('root')
        assert result == 'Root Directory'

    def test_format_directory_name_unknown(self):
        """Test formatting Unknown directory."""
        result = format_directory_name('Unknown')
        assert result == 'Unknown'

    def test_format_directory_name_path(self):
        """Test formatting directory path."""
        result = format_directory_name('src/main/java')
        assert result == 'java'

    def test_format_directory_name_simple(self):
        """Test formatting simple directory name."""
        result = format_directory_name('common')
        assert result == 'common'


class TestReadLlmOutputFiles:
    """Tests for read_llm_output_files function."""

    def test_read_llm_output_files_basic(self, temp_dir):
        """Test reading LLM output files."""
        # Create test files
        result_data = {
            'file_path': 'src/Example.java',
            'function': 'test',
            'checksum': 'abc123',
            'results': [
                {
                    'issue': 'Test issue',
                    'severity': 'high',
                    'category': 'logicBug'
                }
            ]
        }
        
        file_path = os.path.join(temp_dir, 'test_analysis.json')
        with open(file_path, 'w') as f:
            json.dump(result_data, f)
        
        issues = read_llm_output_files(temp_dir)
        
        assert len(issues) == 1
        assert issues[0]['issue'] == 'Test issue'

    def test_read_llm_output_files_empty_dir(self, temp_dir):
        """Test reading from empty directory."""
        issues = read_llm_output_files(temp_dir)
        
        assert issues == []

    def test_read_llm_output_files_adds_metadata(self, temp_dir):
        """Test that metadata is added to issues."""
        result_data = {
            'file_path': 'src/Example.java',
            'function': 'testFunc',
            'checksum': 'abc123',
            'results': [
                {
                    'issue': 'Test issue',
                    'severity': 'high',
                    'category': 'logicBug'
                }
            ]
        }
        
        file_path = os.path.join(temp_dir, 'test_analysis.json')
        with open(file_path, 'w') as f:
            json.dump(result_data, f)
        
        issues = read_llm_output_files(temp_dir)
        
        assert issues[0].get('original_file_path') == 'src/Example.java'

    def test_read_llm_output_files_custom_suffix(self, temp_dir):
        """Test reading files with custom suffix."""
        result_data = {
            'file_path': 'src/Example.java',
            'function': 'test',
            'checksum': 'abc123',
            'results': [{'issue': 'Test', 'severity': 'high', 'category': 'logicBug'}]
        }
        
        # Create file with custom suffix
        file_path = os.path.join(temp_dir, 'test_custom.json')
        with open(file_path, 'w') as f:
            json.dump(result_data, f)
        
        issues = read_llm_output_files(temp_dir, file_suffix='_custom.json')
        
        assert len(issues) == 1
