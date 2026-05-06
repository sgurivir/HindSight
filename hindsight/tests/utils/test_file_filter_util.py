#!/usr/bin/env python3
"""
Tests for hindsight/utils/file_filter_util.py

Tests the centralized path matching utilities:
- matches_path_components: partial path matching for file paths
- matches_directory_components: partial path matching for directory paths
- _consecutive_component_match: sliding window component matcher
"""

import pytest

from hindsight.utils.file_filter_util import (
    _consecutive_component_match,
    matches_path_components,
    matches_directory_components,
)


class TestConsecutiveComponentMatch:

    def test_exact_match(self):
        assert _consecutive_component_match(["A", "B", "C"], ["A", "B", "C"]) == 0

    def test_prefix_match(self):
        assert _consecutive_component_match(["A", "B", "C", "D"], ["A", "B"]) == 0

    def test_suffix_match(self):
        assert _consecutive_component_match(["A", "B", "C"], ["B", "C"]) == 1

    def test_middle_match(self):
        assert _consecutive_component_match(["A", "B", "C", "D"], ["B", "C"]) == 1

    def test_single_component(self):
        assert _consecutive_component_match(["A", "B", "C"], ["B"]) == 1

    def test_no_match(self):
        assert _consecutive_component_match(["A", "B", "C"], ["X", "Y"]) == -1

    def test_partial_component_no_match(self):
        """Ensure 'BC' does not match component 'B' or 'C'."""
        assert _consecutive_component_match(["A", "BC", "D"], ["B", "C"]) == -1

    def test_empty_needle(self):
        assert _consecutive_component_match(["A", "B"], []) == 0

    def test_needle_longer_than_haystack(self):
        assert _consecutive_component_match(["A"], ["A", "B"]) == -1


class TestMatchesPathComponents:

    def test_single_component_prefix(self):
        assert matches_path_components("src/file.txt", "src") is True

    def test_single_component_nested(self):
        assert matches_path_components("apps/Orange/file.txt", "Orange") is True

    def test_multi_component_prefix(self):
        assert matches_path_components("B/C/file.txt", "B/C") is True

    def test_multi_component_partial(self):
        assert matches_path_components("A/B/C/file.txt", "B/C") is True

    def test_multi_component_deep(self):
        assert matches_path_components("X/Y/B/C/Z/file.txt", "B/C") is True

    def test_no_match(self):
        assert matches_path_components("A/B/D/file.txt", "B/C") is False

    def test_filename_not_matched(self):
        """Pattern should not match the filename itself."""
        assert matches_path_components("A/B/file.txt", "file.txt") is False

    def test_no_directory(self):
        """File with no directory components."""
        assert matches_path_components("file.txt", "src") is False

    def test_empty_pattern(self):
        assert matches_path_components("A/B/file.txt", "") is False

    def test_leading_dot_slash(self):
        assert matches_path_components("./A/B/file.txt", "A/B") is True

    def test_backslash_normalization(self):
        assert matches_path_components("A\\B\\C\\file.txt", "B/C") is True


class TestMatchesDirectoryComponents:

    def test_exact_match(self):
        assert matches_directory_components("B/C", "B/C") is True

    def test_partial_match(self):
        assert matches_directory_components("A/B/C", "B/C") is True

    def test_parent_match(self):
        assert matches_directory_components("A/B/C/D", "B/C") is True

    def test_single_component(self):
        assert matches_directory_components("apps/Orange", "Orange") is True

    def test_no_match(self):
        assert matches_directory_components("A/B/D", "B/C") is False

    def test_no_partial_component_match(self):
        """'srcutil' should not match pattern 'src'."""
        assert matches_directory_components("srcutil", "src") is False

    def test_empty_pattern(self):
        assert matches_directory_components("A/B", "") is False
