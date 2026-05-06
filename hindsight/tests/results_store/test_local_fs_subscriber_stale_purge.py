#!/usr/bin/env python3
"""
Tests for stale result purging in CodeAnalysysResultsLocalFSSubscriber.
Verifies that results with mismatched checksums are deleted during load.
"""

import os
import sys
import json
import tempfile
import shutil
from pathlib import Path
from unittest.mock import MagicMock

import pytest

project_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(project_root))

from hindsight.results_store.code_analysys_results_local_fs_subscriber import (
    CodeAnalysysResultsLocalFSSubscriber,
)


@pytest.fixture
def temp_dir():
    temp_path = tempfile.mkdtemp()
    yield temp_path
    if os.path.exists(temp_path):
        shutil.rmtree(temp_path)


def _write_result(analysis_dir, function, file_path, checksum):
    """Write a fake analysis result JSON file and return its path."""
    safe_func = "".join(c for c in function if c.isalnum() or c in ('_', '-'))
    safe_file = "".join(c for c in os.path.basename(file_path) if c.isalnum() or c in ('_', '-', '.'))
    checksum_short = checksum[:8] if len(checksum) > 8 else checksum
    filename = f"{safe_func}_{safe_file}_{checksum_short}_analysis.json"
    result_data = {
        'file_path': file_path,
        'function': function,
        'checksum': checksum,
        'results': [{'issue': 'Test issue', 'severity': 'high', 'category': 'logicBug'}],
    }
    path = os.path.join(analysis_dir, filename)
    with open(path, 'w') as f:
        json.dump(result_data, f)
    return path


def _mock_publisher():
    pub = MagicMock()
    pub.index_existing_result = MagicMock()
    pub.load_existing_result_for_report = MagicMock(return_value='result-id')
    return pub


class TestIsStaleResult:
    """Tests for _is_stale_result helper."""

    def test_not_stale_when_no_checksums_provided(self, temp_dir):
        sub = CodeAnalysysResultsLocalFSSubscriber(temp_dir)
        assert sub._is_stale_result('file.py', 'func', 'abc123', None) is False

    def test_not_stale_when_empty_checksums(self, temp_dir):
        sub = CodeAnalysysResultsLocalFSSubscriber(temp_dir)
        assert sub._is_stale_result('file.py', 'func', 'abc123', {}) is False

    def test_not_stale_when_checksum_matches(self, temp_dir):
        sub = CodeAnalysysResultsLocalFSSubscriber(temp_dir)
        checksums = {('file.py', 'func'): 'abc123'}
        assert sub._is_stale_result('file.py', 'func', 'abc123', checksums) is False

    def test_stale_when_checksum_differs(self, temp_dir):
        sub = CodeAnalysysResultsLocalFSSubscriber(temp_dir)
        checksums = {('file.py', 'func'): 'newchecksum'}
        assert sub._is_stale_result('file.py', 'func', 'abc123', checksums) is True

    def test_stale_when_function_removed(self, temp_dir):
        sub = CodeAnalysysResultsLocalFSSubscriber(temp_dir)
        checksums = {('other_file.py', 'other_func'): 'xyz'}
        assert sub._is_stale_result('file.py', 'func', 'abc123', checksums) is True


class TestLoadExistingResultsWithPurge:
    """Tests for load_existing_results with current_checksums purging."""

    def test_no_purge_without_checksums(self, temp_dir):
        sub = CodeAnalysysResultsLocalFSSubscriber(temp_dir)
        sub.initialize_repo('repo')
        analysis_dir = sub.get_analysis_dir('repo')
        path = _write_result(analysis_dir, 'myFunc', 'src/file.swift', 'aabbccdd11223344')

        pub = _mock_publisher()
        count = sub.load_existing_results('repo', pub)

        assert count == 1
        assert os.path.exists(path)
        pub.index_existing_result.assert_called_once()

    def test_purges_stale_result(self, temp_dir):
        sub = CodeAnalysysResultsLocalFSSubscriber(temp_dir)
        sub.initialize_repo('repo')
        analysis_dir = sub.get_analysis_dir('repo')
        path = _write_result(analysis_dir, 'myFunc', 'src/file.swift', 'oldchecksum1234')

        pub = _mock_publisher()
        checksums = {('src/file.swift', 'myFunc'): 'newchecksum5678'}
        count = sub.load_existing_results('repo', pub, current_checksums=checksums)

        assert count == 0
        assert not os.path.exists(path)
        pub.index_existing_result.assert_not_called()

    def test_keeps_current_result(self, temp_dir):
        sub = CodeAnalysysResultsLocalFSSubscriber(temp_dir)
        sub.initialize_repo('repo')
        analysis_dir = sub.get_analysis_dir('repo')
        path = _write_result(analysis_dir, 'myFunc', 'src/file.swift', 'currentchecksum')

        pub = _mock_publisher()
        checksums = {('src/file.swift', 'myFunc'): 'currentchecksum'}
        count = sub.load_existing_results('repo', pub, current_checksums=checksums)

        assert count == 1
        assert os.path.exists(path)
        pub.index_existing_result.assert_called_once()

    def test_purges_removed_function(self, temp_dir):
        sub = CodeAnalysysResultsLocalFSSubscriber(temp_dir)
        sub.initialize_repo('repo')
        analysis_dir = sub.get_analysis_dir('repo')
        path = _write_result(analysis_dir, 'removedFunc', 'src/old.swift', 'somechecksum123')

        pub = _mock_publisher()
        checksums = {('src/other.swift', 'otherFunc'): 'xyz'}
        count = sub.load_existing_results('repo', pub, current_checksums=checksums)

        assert count == 0
        assert not os.path.exists(path)

    def test_mixed_stale_and_current(self, temp_dir):
        sub = CodeAnalysysResultsLocalFSSubscriber(temp_dir)
        sub.initialize_repo('repo')
        analysis_dir = sub.get_analysis_dir('repo')
        stale_path = _write_result(analysis_dir, 'staleFunc', 'src/a.swift', 'old_checksum_1')
        current_path = _write_result(analysis_dir, 'goodFunc', 'src/b.swift', 'good_checksum_1')

        pub = _mock_publisher()
        checksums = {
            ('src/a.swift', 'staleFunc'): 'new_checksum_1',
            ('src/b.swift', 'goodFunc'): 'good_checksum_1',
        }
        count = sub.load_existing_results('repo', pub, current_checksums=checksums)

        assert count == 1
        assert not os.path.exists(stale_path)
        assert os.path.exists(current_path)


class TestLoadExistingResultsForReportWithPurge:
    """Tests for load_existing_results_for_report with current_checksums purging."""

    def test_no_purge_without_checksums(self, temp_dir):
        sub = CodeAnalysysResultsLocalFSSubscriber(temp_dir)
        sub.initialize_repo('repo')
        analysis_dir = sub.get_analysis_dir('repo')
        path = _write_result(analysis_dir, 'myFunc', 'src/file.swift', 'aabbccdd11223344')

        pub = _mock_publisher()
        count = sub.load_existing_results_for_report('repo', pub)

        assert count == 1
        assert os.path.exists(path)
        pub.load_existing_result_for_report.assert_called_once()

    def test_purges_stale_result(self, temp_dir):
        sub = CodeAnalysysResultsLocalFSSubscriber(temp_dir)
        sub.initialize_repo('repo')
        analysis_dir = sub.get_analysis_dir('repo')
        path = _write_result(analysis_dir, 'myFunc', 'src/file.swift', 'oldchecksum1234')

        pub = _mock_publisher()
        checksums = {('src/file.swift', 'myFunc'): 'newchecksum5678'}
        count = sub.load_existing_results_for_report('repo', pub, current_checksums=checksums)

        assert count == 0
        assert not os.path.exists(path)
        pub.load_existing_result_for_report.assert_not_called()

    def test_keeps_current_result(self, temp_dir):
        sub = CodeAnalysysResultsLocalFSSubscriber(temp_dir)
        sub.initialize_repo('repo')
        analysis_dir = sub.get_analysis_dir('repo')
        path = _write_result(analysis_dir, 'myFunc', 'src/file.swift', 'currentchecksum')

        pub = _mock_publisher()
        checksums = {('src/file.swift', 'myFunc'): 'currentchecksum'}
        count = sub.load_existing_results_for_report('repo', pub, current_checksums=checksums)

        assert count == 1
        assert os.path.exists(path)
        pub.load_existing_result_for_report.assert_called_once()

    def test_mixed_stale_and_current(self, temp_dir):
        sub = CodeAnalysysResultsLocalFSSubscriber(temp_dir)
        sub.initialize_repo('repo')
        analysis_dir = sub.get_analysis_dir('repo')
        stale_path = _write_result(analysis_dir, 'staleFunc', 'src/a.swift', 'old_checksum_1')
        current_path = _write_result(analysis_dir, 'goodFunc', 'src/b.swift', 'good_checksum_1')

        pub = _mock_publisher()
        checksums = {
            ('src/a.swift', 'staleFunc'): 'new_checksum_1',
            ('src/b.swift', 'goodFunc'): 'good_checksum_1',
        }
        count = sub.load_existing_results_for_report('repo', pub, current_checksums=checksums)

        assert count == 1
        assert not os.path.exists(stale_path)
        assert os.path.exists(current_path)
