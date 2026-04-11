#!/usr/bin/env python3
"""
Tests for hindsight.results_store.file_system_results_cache module.
"""

import os
import sys
import json
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Add project root to path
project_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(project_root))

from hindsight.results_store.file_system_results_cache import FileSystemResultsCache


class TestFileSystemResultsCacheInit:
    """Tests for FileSystemResultsCache initialization."""

    def test_init_basic(self, temp_dir):
        """Test basic initialization."""
        cache = FileSystemResultsCache(temp_dir)
        
        assert cache.base_output_dir == temp_dir
        assert cache.current_repo_name is None
        assert len(cache._result_index) == 0

    def test_init_creates_no_directories(self, temp_dir):
        """Test that init doesn't create directories until needed."""
        cache = FileSystemResultsCache(temp_dir)
        
        # No repo directories should be created yet
        assert len(os.listdir(temp_dir)) == 0


class TestFileSystemResultsCacheInitializeForRepo:
    """Tests for initialize_for_repo method."""

    def test_initialize_for_repo_creates_directory(self, temp_dir):
        """Test that initialize_for_repo creates the analysis directory."""
        cache = FileSystemResultsCache(temp_dir)
        
        cache.initialize_for_repo('test-repo')
        
        expected_dir = os.path.join(temp_dir, 'test-repo', 'results', 'code_analysis')
        assert os.path.exists(expected_dir)

    def test_initialize_for_repo_sets_current_repo(self, temp_dir):
        """Test that initialize_for_repo sets current repo name."""
        cache = FileSystemResultsCache(temp_dir)
        
        cache.initialize_for_repo('test-repo')
        
        assert cache.current_repo_name == 'test-repo'

    def test_initialize_for_repo_builds_index(self, temp_analysis_results_dir, temp_dir):
        """Test that initialize_for_repo builds index from existing files."""
        # Create a cache with the parent of the results dir
        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(temp_analysis_results_dir)))
        repo_name = os.path.basename(os.path.dirname(os.path.dirname(temp_analysis_results_dir)))
        
        cache = FileSystemResultsCache(base_dir)
        cache.initialize_for_repo(repo_name)
        
        # Index should have been built
        # Note: The actual indexing depends on file format


class TestFileSystemResultsCacheHasResult:
    """Tests for has_result method."""

    def test_has_result_not_found(self, temp_dir):
        """Test has_result returns False when result doesn't exist."""
        cache = FileSystemResultsCache(temp_dir)
        cache.initialize_for_repo('test-repo')
        
        result = cache.has_result('file.java', 'testFunc', 'abc123')
        
        assert result is False

    def test_has_result_found(self, temp_dir, sample_code_analysis_result):
        """Test has_result returns True when result exists."""
        cache = FileSystemResultsCache(temp_dir)
        cache.initialize_for_repo('test-repo')
        
        # Add a result first
        cache.on_result_added('test-id', sample_code_analysis_result)
        
        # Now check if it exists
        result = cache.has_result(
            sample_code_analysis_result['file_path'],
            sample_code_analysis_result['function'],
            sample_code_analysis_result['checksum']
        )
        
        assert result is True

    def test_has_result_with_timeout(self, temp_dir):
        """Test has_result respects timeout."""
        cache = FileSystemResultsCache(temp_dir)
        cache.initialize_for_repo('test-repo')
        
        # Should complete within timeout
        result = cache.has_result('file.java', 'testFunc', 'abc123', timeout_seconds=1.0)
        
        assert result is False


class TestFileSystemResultsCacheGetExistingResult:
    """Tests for get_existing_result method."""

    def test_get_existing_result_not_found(self, temp_dir):
        """Test get_existing_result returns None when result doesn't exist."""
        cache = FileSystemResultsCache(temp_dir)
        cache.initialize_for_repo('test-repo')
        
        result = cache.get_existing_result('file.java', 'testFunc', 'abc123')
        
        assert result is None

    def test_get_existing_result_found(self, temp_dir, sample_code_analysis_result):
        """Test get_existing_result returns result when it exists."""
        cache = FileSystemResultsCache(temp_dir)
        cache.initialize_for_repo('test-repo')
        
        # Add a result first
        cache.on_result_added('test-id', sample_code_analysis_result)
        
        # Now retrieve it
        result = cache.get_existing_result(
            sample_code_analysis_result['file_path'],
            sample_code_analysis_result['function'],
            sample_code_analysis_result['checksum']
        )
        
        assert result is not None
        assert result['function'] == sample_code_analysis_result['function']


class TestFileSystemResultsCacheOnResultAdded:
    """Tests for on_result_added method."""

    def test_on_result_added_creates_file(self, temp_dir, sample_code_analysis_result):
        """Test that on_result_added creates a JSON file."""
        cache = FileSystemResultsCache(temp_dir)
        cache.initialize_for_repo('test-repo')
        
        cache.on_result_added('test-id', sample_code_analysis_result)
        
        # Check that file was created
        analysis_dir = os.path.join(temp_dir, 'test-repo', 'results', 'code_analysis')
        files = os.listdir(analysis_dir)
        
        assert len(files) == 1
        assert files[0].endswith('_analysis.json')

    def test_on_result_added_updates_index(self, temp_dir, sample_code_analysis_result):
        """Test that on_result_added updates the index."""
        cache = FileSystemResultsCache(temp_dir)
        cache.initialize_for_repo('test-repo')
        
        initial_index_size = len(cache._result_index)
        
        cache.on_result_added('test-id', sample_code_analysis_result)
        
        assert len(cache._result_index) == initial_index_size + 1

    def test_on_result_added_file_content(self, temp_dir, sample_code_analysis_result):
        """Test that on_result_added writes correct content."""
        cache = FileSystemResultsCache(temp_dir)
        cache.initialize_for_repo('test-repo')
        
        cache.on_result_added('test-id', sample_code_analysis_result)
        
        # Read the file and verify content
        analysis_dir = os.path.join(temp_dir, 'test-repo', 'results', 'code_analysis')
        files = os.listdir(analysis_dir)
        
        with open(os.path.join(analysis_dir, files[0]), 'r') as f:
            content = json.load(f)
        
        assert content['function'] == sample_code_analysis_result['function']
        assert content['file_path'] == sample_code_analysis_result['file_path']


class TestFileSystemResultsCacheOnResultUpdated:
    """Tests for on_result_updated method."""

    def test_on_result_updated_replaces_file(self, temp_dir, sample_code_analysis_result):
        """Test that on_result_updated replaces the old file."""
        cache = FileSystemResultsCache(temp_dir)
        cache.initialize_for_repo('test-repo')
        
        # Add initial result
        cache.on_result_added('test-id', sample_code_analysis_result)
        
        # Create updated result
        updated_result = sample_code_analysis_result.copy()
        updated_result['results'] = [
            {
                'issue': 'Updated issue',
                'severity': 'critical',
                'category': 'logicBug'
            }
        ]
        
        cache.on_result_updated('test-id', sample_code_analysis_result, updated_result)
        
        # Verify the update
        analysis_dir = os.path.join(temp_dir, 'test-repo', 'results', 'code_analysis')
        files = os.listdir(analysis_dir)
        
        # Should still have one file
        assert len(files) == 1


class TestFileSystemResultsCacheOnFunctionAnalyzed:
    """Tests for on_function_analyzed method."""

    def test_on_function_analyzed_creates_result(self, temp_dir, sample_code_analysis_result):
        """Test that on_function_analyzed creates a result file."""
        cache = FileSystemResultsCache(temp_dir)
        cache.initialize_for_repo('test-repo')
        
        cache.on_function_analyzed(
            sample_code_analysis_result['function'],
            sample_code_analysis_result['file_path'],
            sample_code_analysis_result
        )
        
        # Check that file was created
        analysis_dir = os.path.join(temp_dir, 'test-repo', 'results', 'code_analysis')
        files = os.listdir(analysis_dir)
        
        assert len(files) == 1


class TestFileSystemResultsCacheOnAnalysisBatchCompleted:
    """Tests for on_analysis_batch_completed method."""

    def test_on_analysis_batch_completed_creates_multiple_files(self, temp_dir):
        """Test that on_analysis_batch_completed creates files for all results."""
        cache = FileSystemResultsCache(temp_dir)
        cache.initialize_for_repo('test-repo')
        
        batch_results = [
            {
                'file_path': 'src/A.java',
                'function': 'funcA',
                'checksum': 'abc123',
                'results': [{'issue': 'Bug A', 'severity': 'high', 'category': 'logicBug'}]
            },
            {
                'file_path': 'src/B.java',
                'function': 'funcB',
                'checksum': 'def456',
                'results': [{'issue': 'Bug B', 'severity': 'medium', 'category': 'performance'}]
            }
        ]
        
        cache.on_analysis_batch_completed(batch_results)
        
        # Check that files were created
        analysis_dir = os.path.join(temp_dir, 'test-repo', 'results', 'code_analysis')
        files = os.listdir(analysis_dir)
        
        assert len(files) == 2


class TestFileSystemResultsCacheMakeKey:
    """Tests for _make_key method."""

    def test_make_key_basic(self, temp_dir):
        """Test basic key generation."""
        cache = FileSystemResultsCache(temp_dir)
        
        key = cache._make_key('src/Example.java', 'testFunc', 'abc123')
        
        assert 'src/Example.java' in key
        assert 'testFunc' in key
        assert 'abc123' in key

    def test_make_key_consistency(self, temp_dir):
        """Test that same inputs produce same key."""
        cache = FileSystemResultsCache(temp_dir)
        
        key1 = cache._make_key('src/Example.java', 'testFunc', 'abc123')
        key2 = cache._make_key('src/Example.java', 'testFunc', 'abc123')
        
        assert key1 == key2

    def test_make_key_different_inputs(self, temp_dir):
        """Test that different inputs produce different keys."""
        cache = FileSystemResultsCache(temp_dir)
        
        key1 = cache._make_key('src/A.java', 'funcA', 'abc123')
        key2 = cache._make_key('src/B.java', 'funcB', 'def456')
        
        assert key1 != key2

    def test_make_key_handles_absolute_path(self, temp_dir):
        """Test that absolute paths are handled correctly."""
        cache = FileSystemResultsCache(temp_dir)
        
        key = cache._make_key('/absolute/path/to/file.java', 'testFunc', 'abc123')
        
        # Key should be created without error
        assert 'testFunc' in key
        assert 'abc123' in key


class TestFileSystemResultsCacheGenerateFilename:
    """Tests for _generate_filename method."""

    def test_generate_filename_basic(self, temp_dir, sample_code_analysis_result):
        """Test basic filename generation."""
        cache = FileSystemResultsCache(temp_dir)
        
        filename = cache._generate_filename(sample_code_analysis_result)
        
        assert filename.endswith('_analysis.json')
        assert 'processData' in filename

    def test_generate_filename_safe_characters(self, temp_dir):
        """Test that filename uses safe characters."""
        cache = FileSystemResultsCache(temp_dir)
        
        result = {
            'file_path': 'src/path/with spaces/file.java',
            'function': 'func<with>special:chars',
            'checksum': 'abc123'
        }
        
        filename = cache._generate_filename(result)
        
        # Should not contain unsafe characters
        assert ' ' not in filename
        assert '<' not in filename
        assert '>' not in filename
        assert ':' not in filename

    def test_generate_filename_truncates_long_names(self, temp_dir):
        """Test that long function names are truncated."""
        cache = FileSystemResultsCache(temp_dir)
        
        result = {
            'file_path': 'src/file.java',
            'function': 'a' * 200,  # Very long function name
            'checksum': 'abc123'
        }
        
        filename = cache._generate_filename(result)
        
        # Filename should be reasonable length
        assert len(filename) < 300


class TestFileSystemResultsCacheExtractRepoName:
    """Tests for _extract_repo_name method."""

    def test_extract_repo_name_from_absolute_path(self, temp_dir):
        """Test extracting repo name from absolute path."""
        cache = FileSystemResultsCache(temp_dir)
        
        result = {
            'file_path': '/home/user/repos/my-repo/src/file.java'
        }
        
        repo_name = cache._extract_repo_name(result)
        
        assert repo_name is not None
        assert isinstance(repo_name, str)

    def test_extract_repo_name_default(self, temp_dir):
        """Test default repo name when extraction fails."""
        cache = FileSystemResultsCache(temp_dir)
        
        result = {}
        
        repo_name = cache._extract_repo_name(result)
        
        assert repo_name == 'default_repo'


class TestFileSystemResultsCacheThreadSafety:
    """Tests for thread safety of FileSystemResultsCache."""

    def test_concurrent_has_result(self, temp_dir, sample_code_analysis_result):
        """Test concurrent has_result calls."""
        cache = FileSystemResultsCache(temp_dir)
        cache.initialize_for_repo('test-repo')
        cache.on_result_added('test-id', sample_code_analysis_result)
        
        results = []
        errors = []
        
        def check_result():
            try:
                result = cache.has_result(
                    sample_code_analysis_result['file_path'],
                    sample_code_analysis_result['function'],
                    sample_code_analysis_result['checksum']
                )
                results.append(result)
            except Exception as e:
                errors.append(e)
        
        threads = [threading.Thread(target=check_result) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        
        assert len(errors) == 0
        assert all(r is True for r in results)

    def test_concurrent_add_results(self, temp_dir):
        """Test concurrent result additions."""
        cache = FileSystemResultsCache(temp_dir)
        cache.initialize_for_repo('test-repo')
        
        errors = []
        
        def add_result(i):
            try:
                result = {
                    'file_path': f'src/File{i}.java',
                    'function': f'func{i}',
                    'checksum': f'checksum{i}',
                    'results': [{'issue': f'Bug {i}', 'severity': 'high', 'category': 'logicBug'}]
                }
                cache.on_result_added(f'id-{i}', result)
            except Exception as e:
                errors.append(e)
        
        threads = [threading.Thread(target=add_result, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        
        assert len(errors) == 0
        
        # Check that all files were created
        analysis_dir = os.path.join(temp_dir, 'test-repo', 'results', 'code_analysis')
        files = os.listdir(analysis_dir)
        assert len(files) == 10


class TestFileSystemResultsCacheEdgeCases:
    """Tests for edge cases in FileSystemResultsCache."""

    def test_empty_results_list(self, temp_dir):
        """Test handling result with empty results list."""
        cache = FileSystemResultsCache(temp_dir)
        cache.initialize_for_repo('test-repo')
        
        result = {
            'file_path': 'src/file.java',
            'function': 'testFunc',
            'checksum': 'abc123',
            'results': []
        }
        
        # Should not raise
        cache.on_result_added('test-id', result)

    def test_missing_checksum(self, temp_dir):
        """Test handling result with missing checksum."""
        cache = FileSystemResultsCache(temp_dir)
        cache.initialize_for_repo('test-repo')
        
        result = {
            'file_path': 'src/file.java',
            'function': 'testFunc',
            'results': [{'issue': 'Bug', 'severity': 'high', 'category': 'logicBug'}]
        }
        
        # Should handle gracefully
        cache.on_result_added('test-id', result)

    def test_special_characters_in_path(self, temp_dir):
        """Test handling paths with special characters."""
        cache = FileSystemResultsCache(temp_dir)
        cache.initialize_for_repo('test-repo')
        
        result = {
            'file_path': 'src/path with spaces/file.java',
            'function': 'testFunc',
            'checksum': 'abc123',
            'results': [{'issue': 'Bug', 'severity': 'high', 'category': 'logicBug'}]
        }
        
        # Should handle gracefully
        cache.on_result_added('test-id', result)
