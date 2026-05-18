#!/usr/bin/env python3
"""Tests for path_discovery.py — static source-to-sink path finding."""

import unittest
from hindsight.analyzers.path_discovery import PathDiscovery
from hindsight.core.lang_util.call_graph_util import CallGraph


def _build_linear_graph():
    """A → B → C → D (linear chain)"""
    g = CallGraph()
    g.add_edge("A", "B")
    g.add_edge("B", "C")
    g.add_edge("C", "D")
    return g


def _build_diamond_graph():
    """
    A → B → D
    A → C → D
    """
    g = CallGraph()
    g.add_edge("A", "B")
    g.add_edge("A", "C")
    g.add_edge("B", "D")
    g.add_edge("C", "D")
    return g


def _build_cyclic_graph():
    """A → B → C → A (cycle), C → D (exit)"""
    g = CallGraph()
    g.add_edge("A", "B")
    g.add_edge("B", "C")
    g.add_edge("C", "A")
    g.add_edge("C", "D")
    return g


def _build_wide_graph():
    """Source S calls F1..F10, each calls sink K."""
    g = CallGraph()
    for i in range(10):
        g.add_edge("S", f"F{i}")
        g.add_edge(f"F{i}", "K")
    return g


class TestPathDiscoveryBasic(unittest.TestCase):

    def test_linear_path(self):
        g = _build_linear_graph()
        pd = PathDiscovery(g, max_path_depth=10)
        flows = pd.discover_paths({"A"}, {"D": "file_system_write"})
        self.assertEqual(len(flows), 1)
        self.assertEqual(flows[0]["source"], "A")
        self.assertEqual(flows[0]["sink"], "D")
        self.assertEqual(flows[0]["path"], ["A", "B", "C", "D"])
        self.assertEqual(flows[0]["path_length"], 3)
        self.assertEqual(flows[0]["sink_category"], "file_system_write")

    def test_no_path_when_depth_too_short(self):
        g = _build_linear_graph()
        pd = PathDiscovery(g, max_path_depth=2)
        flows = pd.discover_paths({"A"}, {"D": "file_system_write"})
        self.assertEqual(len(flows), 0)

    def test_diamond_finds_both_paths(self):
        g = _build_diamond_graph()
        pd = PathDiscovery(g, max_path_depth=10, max_paths_per_pair=5)
        flows = pd.discover_paths({"A"}, {"D": "network_output"})
        self.assertEqual(len(flows), 2)
        paths = [tuple(f["path"]) for f in flows]
        self.assertIn(("A", "B", "D"), paths)
        self.assertIn(("A", "C", "D"), paths)

    def test_max_paths_per_pair_limits_results(self):
        g = _build_diamond_graph()
        pd = PathDiscovery(g, max_path_depth=10, max_paths_per_pair=1)
        flows = pd.discover_paths({"A"}, {"D": "network_output"})
        self.assertEqual(len(flows), 1)

    def test_cycle_does_not_cause_infinite_loop(self):
        g = _build_cyclic_graph()
        pd = PathDiscovery(g, max_path_depth=10)
        flows = pd.discover_paths({"A"}, {"D": "process_execution"})
        self.assertEqual(len(flows), 1)
        self.assertEqual(flows[0]["path"], ["A", "B", "C", "D"])

    def test_no_path_when_source_is_sink(self):
        """A source that is also a sink should not produce a zero-length path."""
        g = _build_linear_graph()
        pd = PathDiscovery(g, max_path_depth=10)
        flows = pd.discover_paths({"A"}, {"A": "process_execution"})
        self.assertEqual(len(flows), 0)

    def test_source_not_in_graph(self):
        g = _build_linear_graph()
        pd = PathDiscovery(g, max_path_depth=10)
        flows = pd.discover_paths({"MISSING"}, {"D": "file_system_write"})
        self.assertEqual(len(flows), 0)

    def test_sink_not_in_graph(self):
        g = _build_linear_graph()
        pd = PathDiscovery(g, max_path_depth=10)
        flows = pd.discover_paths({"A"}, {"MISSING": "file_system_write"})
        self.assertEqual(len(flows), 0)

    def test_max_total_paths_cap(self):
        g = _build_wide_graph()
        # S → Fi → K produces 10 paths, cap at 5
        pd = PathDiscovery(g, max_path_depth=10, max_paths_per_pair=10, max_total_paths=5)
        flows = pd.discover_paths({"S"}, {"K": "database_write"})
        self.assertLessEqual(len(flows), 5)

    def test_multiple_sources_multiple_sinks(self):
        g = CallGraph()
        g.add_edge("S1", "M")
        g.add_edge("S2", "M")
        g.add_edge("M", "K1")
        g.add_edge("M", "K2")
        pd = PathDiscovery(g, max_path_depth=10)
        flows = pd.discover_paths(
            {"S1", "S2"},
            {"K1": "query_construction", "K2": "markup_generation"}
        )
        # S1→M→K1, S1→M→K2, S2→M→K1, S2→M→K2 = 4 paths
        self.assertEqual(len(flows), 4)
        sources = set(f["source"] for f in flows)
        sinks = set(f["sink"] for f in flows)
        self.assertEqual(sources, {"S1", "S2"})
        self.assertEqual(sinks, {"K1", "K2"})


class TestPathDiscoveryStatistics(unittest.TestCase):

    def test_statistics_empty(self):
        g = _build_linear_graph()
        pd = PathDiscovery(g, max_path_depth=1)
        flows = pd.discover_paths({"A"}, {"D": "x"})
        stats = pd.get_statistics(flows)
        self.assertEqual(stats["total_candidate_flows"], 0)

    def test_statistics_populated(self):
        g = _build_diamond_graph()
        pd = PathDiscovery(g, max_path_depth=10, max_paths_per_pair=5)
        flows = pd.discover_paths({"A"}, {"D": "network_output"})
        stats = pd.get_statistics(flows)
        self.assertEqual(stats["total_candidate_flows"], 2)
        self.assertEqual(stats["unique_sources"], 1)
        self.assertEqual(stats["unique_sinks"], 1)
        self.assertEqual(stats["unique_pairs"], 1)
        self.assertEqual(stats["path_length_distribution"], {2: 2})
        self.assertEqual(stats["flows_by_sink_category"], {"network_output": 2})


class TestPathDiscoveryAuthPassthrough(unittest.TestCase):

    def test_auth_sink_not_recorded_as_flow(self):
        """Auth sinks are security gates, not targets — no standalone flows."""
        g = CallGraph()
        g.add_edge("handler", "isEntitled")
        pd = PathDiscovery(g, max_path_depth=10)
        flows = pd.discover_paths(
            {"handler"},
            {"isEntitled": "authentication_authorization"},
        )
        self.assertEqual(len(flows), 0)

    def test_bfs_continues_past_auth_sink_to_real_sink(self):
        """BFS should traverse through auth sinks to discover real sinks beyond."""
        g = CallGraph()
        g.add_edge("handler", "authCheck")
        g.add_edge("authCheck", "writeDB")
        pd = PathDiscovery(g, max_path_depth=10)
        flows = pd.discover_paths(
            {"handler"},
            {
                "authCheck": "authentication_authorization",
                "writeDB": "database_write",
            },
        )
        self.assertEqual(len(flows), 1)
        self.assertEqual(flows[0]["sink"], "writeDB")
        self.assertEqual(flows[0]["sink_category"], "database_write")
        self.assertEqual(flows[0]["path"], ["handler", "authCheck", "writeDB"])

    def test_auth_sibling_real_sink_recorded(self):
        """Source calls both auth check and real sink — only real sink recorded."""
        g = CallGraph()
        g.add_edge("handler", "isEntitled")
        g.add_edge("handler", "writeFile")
        pd = PathDiscovery(g, max_path_depth=10)
        flows = pd.discover_paths(
            {"handler"},
            {
                "isEntitled": "authentication_authorization",
                "writeFile": "file_system_write",
            },
        )
        self.assertEqual(len(flows), 1)
        self.assertEqual(flows[0]["sink"], "writeFile")
        self.assertEqual(flows[0]["sink_category"], "file_system_write")

    def test_mixed_sinks_only_real_sinks_in_output(self):
        """Graph with auth and real sinks — only real sinks appear in flows."""
        g = CallGraph()
        g.add_edge("src", "checkPermission")
        g.add_edge("src", "doWork")
        g.add_edge("doWork", "validateToken")
        g.add_edge("doWork", "sqlExec")
        pd = PathDiscovery(g, max_path_depth=10)
        flows = pd.discover_paths(
            {"src"},
            {
                "checkPermission": "authentication_authorization",
                "validateToken": "authentication_authorization",
                "sqlExec": "database_write",
            },
        )
        self.assertEqual(len(flows), 1)
        self.assertEqual(flows[0]["sink"], "sqlExec")
        self.assertEqual(flows[0]["path"], ["src", "doWork", "sqlExec"])


if __name__ == "__main__":
    unittest.main()
