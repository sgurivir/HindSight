#!/usr/bin/env python3
# Created by Sridhar Gurivireddy
"""
Path Discovery - Static graph traversal to find source-to-sink paths.

Given a call graph, a set of source functions (external input), and a set of
sink functions (security-relevant operations), this module finds all call paths
from sources to sinks within a configurable depth limit.

This is Phase 1 of the security data flow analysis pipeline. The candidate
paths produced here are verified by an LLM in Phase 2.
"""

import logging
from collections import deque
from typing import Any, Dict, List, Optional, Set, Tuple

from ..core.lang_util.call_graph_util import CallGraph

logger = logging.getLogger(__name__)


class PathDiscovery:
    """Finds call paths from source functions to sink functions in a call graph."""

    def __init__(
        self,
        call_graph: CallGraph,
        max_path_depth: int = 10,
        max_paths_per_pair: int = 3,
        max_total_paths: int = 5000,
    ):
        """
        Args:
            call_graph: The CallGraph instance (edges = caller → callees)
            max_path_depth: Maximum number of hops from source to sink
            max_paths_per_pair: Maximum paths to keep per (source, sink) pair
            max_total_paths: Hard cap on total candidate paths
        """
        self.graph = call_graph
        self.max_path_depth = max_path_depth
        self.max_paths_per_pair = max_paths_per_pair
        self.max_total_paths = max_total_paths

    def discover_paths(
        self,
        source_functions: Set[str],
        sink_functions: Dict[str, str],
    ) -> List[Dict[str, Any]]:
        """
        Find all call paths from sources to sinks within depth limit.

        Uses BFS from each source, following outgoing call edges (caller→callee).
        Shortest paths are found first due to BFS ordering.

        Args:
            source_functions: Set of function names that accept external input
            sink_functions: Dict mapping sink function name → sink category

        Returns:
            List of candidate flow dicts, each containing:
              - source: source function name
              - sink: sink function name
              - sink_category: security category of the sink
              - path: list of function names from source to sink (inclusive)
              - path_length: number of hops (len(path) - 1)
        """
        sink_set = set(sink_functions.keys())
        candidate_flows: List[Dict[str, Any]] = []
        pair_counts: Dict[Tuple[str, str], int] = {}

        sources_in_graph = source_functions & self.graph.nodes
        sinks_in_graph = sink_set & self.graph.nodes

        logger.info(
            f"Path discovery: {len(sources_in_graph)} sources, "
            f"{len(sinks_in_graph)} sinks in graph "
            f"(max_depth={self.max_path_depth}, max_paths_per_pair={self.max_paths_per_pair})"
        )

        if not sources_in_graph or not sinks_in_graph:
            logger.warning("No sources or sinks found in call graph — no paths to discover")
            return []

        # Precompute: which sinks are reachable from anywhere?
        # Use reverse BFS from all sinks to find the set of nodes that CAN reach a sink.
        reachable_to_sink = self._compute_reverse_reachability(sinks_in_graph)

        for source in sorted(sources_in_graph):
            if len(candidate_flows) >= self.max_total_paths:
                logger.info(f"Reached max_total_paths ({self.max_total_paths}), stopping discovery")
                break

            # Skip sources that can't reach any sink
            if source not in reachable_to_sink:
                continue

            remaining_budget = self.max_total_paths - len(candidate_flows)
            paths_from_source = self._bfs_paths_from_source(
                source, sink_set, sink_functions, pair_counts, remaining_budget
            )
            candidate_flows.extend(paths_from_source)

        logger.info(
            f"Path discovery complete: {len(candidate_flows)} candidate flows found "
            f"across {len(pair_counts)} unique (source, sink) pairs"
        )

        return candidate_flows

    def _compute_reverse_reachability(self, sink_nodes: Set[str]) -> Set[str]:
        """
        BFS backward from all sinks to find all nodes that can reach at least one sink.

        Uses reverse_edges (callee→callers) to walk UP the call chain.
        Limited to max_path_depth hops.
        """
        reachable: Set[str] = set(sink_nodes)
        queue: deque = deque()

        for sink in sink_nodes:
            queue.append((sink, 0))

        while queue:
            node, depth = queue.popleft()
            if depth >= self.max_path_depth:
                continue

            for caller in self.graph.get_incoming_edges(node):
                if caller not in reachable:
                    reachable.add(caller)
                    queue.append((caller, depth + 1))

        logger.info(
            f"Reverse reachability: {len(reachable)} nodes can reach a sink "
            f"within {self.max_path_depth} hops"
        )
        return reachable

    def _bfs_paths_from_source(
        self,
        source: str,
        sink_set: Set[str],
        sink_functions: Dict[str, str],
        pair_counts: Dict[Tuple[str, str], int],
        budget: int,
    ) -> List[Dict[str, Any]]:
        """
        BFS from a single source, collecting all paths that reach a sink.

        Each queue entry tracks the full path to avoid revisiting nodes within
        the same path (preventing cycles), while still allowing different paths
        to share intermediate nodes.
        """
        results: List[Dict[str, Any]] = []

        # BFS queue: (current_node, path_so_far)
        queue: deque = deque()
        queue.append((source, [source]))

        while queue:
            if len(results) >= budget:
                break

            current, path = queue.popleft()

            if len(path) - 1 > self.max_path_depth:
                continue

            # Check if current node is a sink (and not the source itself at depth 0)
            if current in sink_set and current != source:
                category = sink_functions.get(current, "unknown")

                if category == "authentication_authorization":
                    # Auth/entitlement checks are security gates, not targets.
                    # Don't record them as standalone flows — they produce
                    # trivially-not-vulnerable results.  Continue BFS past
                    # them so we discover the real sinks they protect.
                    pass  # fall through to callee expansion below
                else:
                    pair_key = (source, current)
                    count = pair_counts.get(pair_key, 0)
                    if count < self.max_paths_per_pair:
                        pair_counts[pair_key] = count + 1
                        results.append({
                            "source": source,
                            "sink": current,
                            "sink_category": category,
                            "path": list(path),
                            "path_length": len(path) - 1,
                        })
                    # Taint terminates at real sinks
                    continue

            # Expand to callees
            for callee in self.graph.get_outgoing_edges(current):
                if callee not in path:  # cycle avoidance within this path
                    queue.append((callee, path + [callee]))

        return results

    def get_statistics(self, candidate_flows: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Compute summary statistics for discovered paths."""
        if not candidate_flows:
            return {
                "total_candidate_flows": 0,
                "unique_sources": 0,
                "unique_sinks": 0,
                "unique_pairs": 0,
                "path_length_distribution": {},
                "flows_by_sink_category": {},
            }

        sources = set(f["source"] for f in candidate_flows)
        sinks = set(f["sink"] for f in candidate_flows)
        pairs = set((f["source"], f["sink"]) for f in candidate_flows)

        length_dist: Dict[int, int] = {}
        category_dist: Dict[str, int] = {}
        for flow in candidate_flows:
            pl = flow["path_length"]
            length_dist[pl] = length_dist.get(pl, 0) + 1
            cat = flow["sink_category"]
            category_dist[cat] = category_dist.get(cat, 0) + 1

        return {
            "total_candidate_flows": len(candidate_flows),
            "unique_sources": len(sources),
            "unique_sinks": len(sinks),
            "unique_pairs": len(pairs),
            "path_length_distribution": dict(sorted(length_dist.items())),
            "flows_by_sink_category": dict(sorted(category_dist.items(), key=lambda x: -x[1])),
        }
