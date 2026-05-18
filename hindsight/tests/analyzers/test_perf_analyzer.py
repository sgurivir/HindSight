#!/usr/bin/env python3
"""Tests for the performance analyzer components."""

import json
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

# Mock fastmcp before importing analyzers package (pre-existing env issue)
sys.modules.setdefault("fastmcp", MagicMock())

from hindsight.analyzers.edge_coloring_tracker import EdgeColoringTracker
from hindsight.analyzers.call_path_enumerator import CallPathEnumerator
from hindsight.core.lang_util.call_graph_util import CallGraph
from hindsight.core.llm.perf_context_cache import PerfContextCache
from hindsight.core.llm.iterative.perf_context_analyzer import PerfContextAnalyzer
from hindsight.core.llm.iterative.perf_analysis_analyzer import PerfAnalysisAnalyzer


class TestEdgeColoringTracker(unittest.TestCase):
    """Tests for EdgeColoringTracker."""

    def setUp(self):
        self.tracker = EdgeColoringTracker()

    def test_empty_tracker_all_novel(self):
        assert self.tracker.has_novel_edges(["A", "B", "C"]) is True

    def test_mark_analyzed_colors_edges(self):
        self.tracker.mark_analyzed(["A", "B", "C"])
        assert self.tracker.has_novel_edges(["A", "B"]) is False
        assert self.tracker.has_novel_edges(["B", "C"]) is False

    def test_partial_overlap_is_novel(self):
        self.tracker.mark_analyzed(["A", "B", "C"])
        # C->D is novel even though A->B and B->C are colored
        assert self.tracker.has_novel_edges(["B", "C", "D"]) is True

    def test_get_novel_edges(self):
        self.tracker.mark_analyzed(["A", "B", "C"])
        novel = self.tracker.get_novel_edges(["A", "B", "C", "D"])
        assert novel == [("C", "D")]

    def test_get_novel_edge_count(self):
        self.tracker.mark_analyzed(["A", "B", "C"])
        assert self.tracker.get_novel_edge_count(["A", "B", "C"]) == 0
        assert self.tracker.get_novel_edge_count(["A", "B", "D"]) == 1
        assert self.tracker.get_novel_edge_count(["X", "Y", "Z"]) == 2

    def test_coverage_stats(self):
        self.tracker.mark_analyzed(["A", "B", "C"])
        stats = self.tracker.get_coverage_stats()
        assert stats["analyzed_edges"] == 2

    def test_single_node_path_no_edges(self):
        assert self.tracker.has_novel_edges(["A"]) is False


class TestCallPathEnumerator(unittest.TestCase):
    """Tests for CallPathEnumerator."""

    def _build_linear_graph(self):
        """A -> B -> C -> D -> E"""
        g = CallGraph()
        g.add_edge("A", "B")
        g.add_edge("B", "C")
        g.add_edge("C", "D")
        g.add_edge("D", "E")
        return g

    def _build_branching_graph(self):
        """A -> B -> C, A -> B -> D -> E, A -> F -> G"""
        g = CallGraph()
        g.add_edge("A", "B")
        g.add_edge("B", "C")
        g.add_edge("B", "D")
        g.add_edge("D", "E")
        g.add_edge("A", "F")
        g.add_edge("F", "G")
        return g

    def test_min_depth_filter(self):
        g = self._build_linear_graph()
        enum = CallPathEnumerator(g, min_path_depth=3, max_path_depth=10)
        paths = enum.enumerate_paths()
        # Only A->B->C->D->E qualifies (depth 4), shorter sub-paths don't
        assert all(len(p) - 1 >= 3 for p in paths)

    def test_max_depth_cap(self):
        g = self._build_linear_graph()
        enum = CallPathEnumerator(g, min_path_depth=1, max_path_depth=2)
        paths = enum.enumerate_paths()
        assert all(len(p) - 1 <= 2 for p in paths)

    def test_branching_produces_multiple_paths(self):
        g = self._build_branching_graph()
        enum = CallPathEnumerator(g, min_path_depth=2, max_path_depth=5)
        paths = enum.enumerate_paths()
        assert len(paths) >= 2

    def test_cycle_handling(self):
        """Graph with a cycle should not infinite loop."""
        g = CallGraph()
        g.add_edge("A", "B")
        g.add_edge("B", "C")
        g.add_edge("C", "A")  # Cycle
        g.add_edge("C", "D")
        enum = CallPathEnumerator(g, min_path_depth=2, max_path_depth=5, max_paths=50)
        paths = enum.enumerate_paths()
        # Should terminate and produce paths without revisiting nodes
        for p in paths:
            assert len(p) == len(set(p)), f"Path has duplicates: {p}"

    def test_max_paths_cap(self):
        g = self._build_linear_graph()
        enum = CallPathEnumerator(g, min_path_depth=1, max_path_depth=10, max_paths=2)
        paths = enum.enumerate_paths()
        assert len(paths) <= 2

    def test_entry_points(self):
        g = self._build_branching_graph()
        # Start only from F
        enum = CallPathEnumerator(g, min_path_depth=1, max_path_depth=5, entry_points={"F"})
        paths = enum.enumerate_paths()
        assert all(p[0] == "F" for p in paths)

    def test_deduplication_removes_subsets(self):
        g = CallGraph()
        g.add_edge("A", "B")
        g.add_edge("B", "C")
        g.add_edge("C", "D")
        # With min_depth=1, we'd get A->B->C->D and A->B->C as sub-path
        # Dedup should remove the subset
        enum = CallPathEnumerator(g, min_path_depth=1, max_path_depth=5)
        paths = enum.enumerate_paths()
        edges_list = [frozenset((p[i], p[i+1]) for i in range(len(p)-1)) for p in paths]
        for i, edges_i in enumerate(edges_list):
            for j, edges_j in enumerate(edges_list):
                if i != j:
                    assert not (edges_i < edges_j), f"Path {i} is strict subset of path {j}"

    def test_priority_scoring(self):
        g = CallGraph()
        g.add_edge("fetchData", "parseJSON")
        g.add_edge("parseJSON", "buildModel")
        g.add_edge("helper", "util")
        g.add_edge("util", "log")
        enum = CallPathEnumerator(g, min_path_depth=1, max_path_depth=5)
        paths = enum.enumerate_paths()
        # Path with I/O keyword should be ranked higher
        if len(paths) >= 2:
            first_path_has_io = any("fetch" in f.lower() for f in paths[0])
            assert first_path_has_io


class TestPerfContextCache(unittest.TestCase):
    """Tests for PerfContextCache."""

    def test_cache_miss_then_hit(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = PerfContextCache(cache_dir=tmpdir)
            assert cache.get("func", "abc") is None
            cache.put("func", "abc", {"body": "hello"})
            assert cache.get("func", "abc") == {"body": "hello"}

    def test_wrong_checksum_returns_none(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = PerfContextCache(cache_dir=tmpdir)
            cache.put("func", "abc", {"body": "hello"})
            assert cache.get("func", "xyz") is None

    def test_invalidate(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = PerfContextCache(cache_dir=tmpdir)
            cache.put("func", "abc", {"body": "hello"})
            cache.invalidate("abc")
            assert cache.get("func", "abc") is None

    def test_stats(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = PerfContextCache(cache_dir=tmpdir)
            cache.get("f1", "a")  # miss
            cache.put("f1", "a", {"x": 1})
            cache.get("f1", "a")  # hit
            stats = cache.get_stats()
            assert stats["hits"] == 1
            assert stats["misses"] == 1
            assert stats["total_cached"] == 1


class TestPerfContextAnalyzer(unittest.TestCase):
    """Tests for PerfContextAnalyzer."""

    def setUp(self):
        self.analyzer = PerfContextAnalyzer(MagicMock())

    def test_extract_valid_bundle(self):
        bundle = json.dumps({"call_path": ["A", "B"], "functions": {"A": {}}})
        content = f"Result:\n```json\n{bundle}\n```"
        result = self.analyzer.extract_json(content)
        assert result is not None
        parsed = json.loads(result)
        assert "call_path" in parsed

    def test_validate_with_functions_key(self):
        assert self.analyzer.validate_json({"functions": {}}) is True

    def test_validate_with_call_path_key(self):
        assert self.analyzer.validate_json({"call_path": []}) is True

    def test_validate_rejects_list(self):
        assert self.analyzer.validate_json([]) is False

    def test_validate_rejects_dict_without_keys(self):
        assert self.analyzer.validate_json({"other": "data"}) is False


class TestPerfAnalysisAnalyzer(unittest.TestCase):
    """Tests for PerfAnalysisAnalyzer."""

    def setUp(self):
        self.analyzer = PerfAnalysisAnalyzer(MagicMock())

    def test_extract_issues_array(self):
        issues = json.dumps([{"file_path": "a.swift", "issue": "test"}])
        content = f"Found:\n{issues}"
        result = self.analyzer.extract_json(content)
        assert result is not None
        parsed = json.loads(result)
        assert len(parsed) == 1

    def test_extract_empty_array(self):
        content = "No issues found:\n[]"
        result = self.analyzer.extract_json(content)
        assert result is not None
        assert json.loads(result) == []

    def test_validate_empty_array(self):
        assert self.analyzer.validate_json([]) is True

    def test_validate_array_of_dicts(self):
        assert self.analyzer.validate_json([{"x": 1}, {"y": 2}]) is True

    def test_validate_rejects_non_list(self):
        assert self.analyzer.validate_json({"not": "a list"}) is False

    def test_extract_from_wrapper_dict(self):
        content = json.dumps({"issues": [{"file": "a.swift"}]})
        result = self.analyzer.extract_json(content)
        assert result is not None
        parsed = json.loads(result)
        assert len(parsed) == 1


if __name__ == "__main__":
    unittest.main()
