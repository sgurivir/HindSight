#!/usr/bin/env python3
"""
Call Path Enumerator

Enumerates analysis-worthy call paths from the repo call graph.
Handles path enumeration, filtering, deduplication, and prioritization.
"""

import hashlib
from collections import deque
from typing import Any, Dict, List, Optional, Set

from ..core.lang_util.call_graph_util import CallGraph
from ..utils.log_util import get_logger

logger = get_logger(__name__)

# Heuristic keywords for priority scoring
IO_KEYWORDS = {"fetch", "load", "read", "write", "download", "upload", "send", "receive", "request", "save", "persist"}
ALLOC_KEYWORDS = {"create", "build", "copy", "serialize", "deserialize", "encode", "decode", "clone", "allocate", "init"}
LOOP_KEYWORDS = {"for", "while", "repeat", "forEach", "map", "filter", "reduce", "flatMap", "compactMap"}


class CallPathEnumerator:
    """Enumerates analysis-worthy call paths from the repo call graph."""

    def __init__(
        self,
        call_graph: CallGraph,
        merged_functions: Optional[Dict[str, Any]] = None,
        min_path_depth: int = 3,
        max_path_depth: int = 8,
        max_paths: int = 500,
        entry_points: Optional[Set[str]] = None,
        min_function_lines: int = 5,
        hot_modules: Optional[List[str]] = None,
    ):
        self.call_graph = call_graph
        self.merged_functions = merged_functions or {}
        self.min_path_depth = min_path_depth
        self.max_path_depth = max_path_depth
        self.max_paths = max_paths
        self.entry_points = entry_points
        self.min_function_lines = min_function_lines
        self.hot_modules = set(hot_modules or [])

    def enumerate_paths(self) -> List[List[str]]:
        """
        Returns deduplicated, prioritized list of call paths.
        Each path is a list of function names [root, ..., leaf].
        """
        roots = self._get_roots()
        if not roots:
            logger.warning("No root nodes found in call graph")
            return []

        logger.info(f"Enumerating paths from {len(roots)} root nodes "
                    f"(min_depth={self.min_path_depth}, max_depth={self.max_path_depth})")

        raw_paths = self._dfs_enumerate(roots)
        logger.info(f"DFS enumeration produced {len(raw_paths)} raw paths")

        filtered = self._filter_paths(raw_paths)
        logger.info(f"After filtering: {len(filtered)} paths")

        deduped = self._deduplicate(filtered)
        logger.info(f"After deduplication: {len(deduped)} paths")

        prioritized = self._prioritize(deduped)

        result = prioritized[:self.max_paths]
        logger.info(f"Final path count: {len(result)} (capped at {self.max_paths})")
        return result

    def _get_roots(self) -> Set[str]:
        """Get starting nodes for path enumeration."""
        if self.entry_points:
            valid = self.entry_points & self.call_graph.nodes
            if valid:
                return valid
            logger.warning("No entry_points found in graph, falling back to root nodes")

        return self.call_graph.get_root_nodes()

    def _dfs_enumerate(self, roots: Set[str]) -> List[List[str]]:
        """DFS from each root, collecting all paths up to max_path_depth."""
        all_paths: List[List[str]] = []

        for root in sorted(roots):
            stack: List[List[str]] = [[root]]

            while stack:
                path = stack.pop()
                current = path[-1]
                depth = len(path) - 1

                if depth >= self.max_path_depth:
                    all_paths.append(path)
                    continue

                callees = self.call_graph.get_outgoing_edges(current)
                # Filter out self-recursion and nodes already in path (cycle prevention)
                valid_callees = [c for c in callees if c not in path]

                if not valid_callees:
                    # Leaf node — record path
                    all_paths.append(path)
                else:
                    for callee in valid_callees:
                        stack.append(path + [callee])

                # Cap total raw paths to prevent memory blowup
                if len(all_paths) > self.max_paths * 20:
                    logger.warning(f"Raw path cap reached ({len(all_paths)}), stopping enumeration")
                    break

            if len(all_paths) > self.max_paths * 20:
                break

        return all_paths

    def _filter_paths(self, paths: List[List[str]]) -> List[List[str]]:
        """Filter paths by minimum depth and function size."""
        result = []
        for path in paths:
            # Minimum depth check (path length = depth + 1)
            if len(path) - 1 < self.min_path_depth:
                continue

            # Skip paths where all functions are trivial
            if self.merged_functions and self._all_trivial(path):
                continue

            result.append(path)
        return result

    def _all_trivial(self, path: List[str]) -> bool:
        """Check if all functions in the path are trivially short."""
        for func_name in path:
            func_data = self.merged_functions.get(func_name)
            if func_data and isinstance(func_data, dict):
                line_count = func_data.get("line_count", 0)
                if not line_count:
                    start = func_data.get("start_line", 0)
                    end = func_data.get("end_line", 0)
                    line_count = max(0, end - start) if start and end else 0
                if line_count >= self.min_function_lines:
                    return False
        return True

    def _deduplicate(self, paths: List[List[str]]) -> List[List[str]]:
        """Remove exact duplicate paths and strict subsets of longer paths."""
        # Sort by length descending so longer paths are processed first
        sorted_paths = sorted(paths, key=len, reverse=True)
        seen_sets: List[frozenset] = []
        result: List[List[str]] = []
        seen_hashes: Set[str] = set()

        for path in sorted_paths:
            # Exact duplicate check (order-sensitive)
            path_hash = self._get_path_hash(path)
            if path_hash in seen_hashes:
                continue
            seen_hashes.add(path_hash)

            # Subset check — skip if this path's edge set is a subset of a longer path
            path_edges = frozenset((path[i], path[i + 1]) for i in range(len(path) - 1))
            is_subset = any(path_edges <= existing for existing in seen_sets)
            if is_subset:
                continue

            seen_sets.append(path_edges)
            result.append(path)

        return result

    def _prioritize(self, paths: List[List[str]]) -> List[List[str]]:
        """Sort paths by estimated optimization potential (highest first)."""
        scored = [(self._compute_priority(p), p) for p in paths]
        scored.sort(key=lambda x: x[0], reverse=True)
        return [p for _, p in scored]

    def _compute_priority(self, path: List[str]) -> float:
        """Score a path by estimated optimization potential."""
        score = 0.0

        # Deeper paths have more optimization surface
        score += len(path) * 0.5

        for func_name in path:
            lower_name = func_name.lower()

            # I/O indicators
            if any(kw in lower_name for kw in IO_KEYWORDS):
                score += 3.0

            # Allocation-heavy indicators
            if any(kw in lower_name for kw in ALLOC_KEYWORDS):
                score += 2.0

            # Function size bonus
            func_data = self.merged_functions.get(func_name)
            if func_data and isinstance(func_data, dict):
                line_count = func_data.get("line_count", 0)
                if not line_count:
                    start = func_data.get("start_line", 0)
                    end = func_data.get("end_line", 0)
                    line_count = max(0, end - start) if start and end else 0
                if line_count > 30:
                    score += 1.0

                # Hot module bonus
                file_path = func_data.get("file", "") or func_data.get("file_path", "")
                if self.hot_modules and any(mod in file_path for mod in self.hot_modules):
                    score += 2.0

        # Boundary crossing bonus: functions from different directories
        dirs = set()
        for func_name in path:
            func_data = self.merged_functions.get(func_name)
            if func_data and isinstance(func_data, dict):
                file_path = func_data.get("file", "") or func_data.get("file_path", "")
                if "/" in file_path:
                    dirs.add(file_path.rsplit("/", 1)[0])
        if len(dirs) > 1:
            score += 2.0

        return score

    def _get_path_hash(self, path: List[str]) -> str:
        """Deterministic hash of a path (order-sensitive)."""
        key = "|".join(path)
        return hashlib.sha256(key.encode()).hexdigest()[:16]

    def get_path_checksum(self, path: List[str]) -> str:
        """
        Compute a checksum for a path based on its constituent function checksums.
        Used for cross-run caching.
        """
        parts = []
        for func_name in path:
            func_data = self.merged_functions.get(func_name)
            if func_data and isinstance(func_data, dict):
                # Use file + start_line + end_line as a proxy for content checksum
                file_path = func_data.get("file", "") or func_data.get("file_path", "")
                start = func_data.get("start_line", 0)
                end = func_data.get("end_line", 0)
                parts.append(f"{file_path}:{start}-{end}")
            else:
                parts.append(func_name)
        key = "|".join(parts)
        return hashlib.sha256(key.encode()).hexdigest()[:16]
