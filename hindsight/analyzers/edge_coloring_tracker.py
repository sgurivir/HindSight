#!/usr/bin/env python3
"""
Edge Coloring Tracker

Tracks which caller→callee edges have been analyzed to prevent
redundant analysis of overlapping call paths.
"""

from typing import Dict, List, Set, Tuple

from ..utils.log_util import get_logger

logger = get_logger(__name__)


class EdgeColoringTracker:
    """Tracks analyzed edges to prevent redundant path analysis."""

    def __init__(self):
        self.analyzed_edges: Set[Tuple[str, str]] = set()

    def has_novel_edges(self, path: List[str]) -> bool:
        """Returns True if the path contains at least one unanalyzed edge."""
        for i in range(len(path) - 1):
            edge = (path[i], path[i + 1])
            if edge not in self.analyzed_edges:
                return True
        return False

    def get_novel_edges(self, path: List[str]) -> List[Tuple[str, str]]:
        """Returns the unanalyzed edges in this path."""
        return [
            (path[i], path[i + 1])
            for i in range(len(path) - 1)
            if (path[i], path[i + 1]) not in self.analyzed_edges
        ]

    def mark_analyzed(self, path: List[str]) -> None:
        """Color all edges in the path after successful analysis."""
        for i in range(len(path) - 1):
            self.analyzed_edges.add((path[i], path[i + 1]))

    def get_coverage_stats(self) -> Dict[str, int]:
        """Report how many edges have been analyzed."""
        return {
            "analyzed_edges": len(self.analyzed_edges),
        }

    def get_novel_edge_count(self, path: List[str]) -> int:
        """Count how many novel edges a path has (useful for prioritization)."""
        count = 0
        for i in range(len(path) - 1):
            if (path[i], path[i + 1]) not in self.analyzed_edges:
                count += 1
        return count
