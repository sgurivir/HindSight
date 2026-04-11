#!/usr/bin/env python3
"""
context_generation_profiler.py - Profile context generation by computing cumulative line counts.

This script analyzes call trees to compute cumulative line count statistics at each level.
It helps understand how much code context would be needed when traversing the call tree
from leaves upward.

Given a merged call graph JSON file, the script will:
1. Generate a call tree (reusing existing functionality)
2. Compute multiple sets of line count statistics at each level:
   - Full Subtree: Self + all descendants (children, grandchildren, etc.)
   - Depth-Limited (2 levels): Self + children only
   - Depth-Limited (3 levels): Self + children + grandchildren
   - Depth-Limited (4 levels): Self + up to 3 levels of descendants
   - Depth-Limited (5 levels): Self + up to 4 levels of descendants

Usage:
    python context_generation_profiler.py -f <path_to_merged_call_graph.json>
    python context_generation_profiler.py -f <path> -o output.txt
    python context_generation_profiler.py -f <path> --json -o output.json
    
Example:
    python context_generation_profiler.py -f ~/hindsight_artifacts/corelocation/code_insights/merged_call_graph.json
"""

import argparse
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

# Add the project root to the path to import from hindsight
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from hindsight.core.lang_util.call_graph_util import CallGraph
from hindsight.core.lang_util.call_tree_util import create_dag

from call_graph_helper import CallGraphHelper


# Depth limits to compute statistics for
DEPTH_LIMITS: List[Optional[int]] = [
    None,  # Full subtree (unlimited)
    2,     # Self + children
    3,     # Self + children + grandchildren
    4,     # Self + up to 3 levels of descendants
    5,     # Self + up to 4 levels of descendants
]


def compute_all_depth_limited_counts(
    dag_edges: Dict[str, Set[str]],
    all_nodes: Set[str],
    implementations: Dict[str, List[Dict[str, Any]]],
    depth_limits: List[Optional[int]] = DEPTH_LIMITS
) -> Dict[str, Dict[str, int]]:
    """
    Compute line counts for all nodes at all depth limits efficiently.
    
    Uses memoization to avoid redundant computation.
    
    Args:
        dag_edges: The DAG edges (node -> set of children)
        all_nodes: Set of all nodes in the graph
        implementations: Function implementation locations
        depth_limits: List of depth limits to compute (None = unlimited)
        
    Returns:
        Dictionary mapping depth key to node counts:
        {
            "full_subtree": {"func1": 100, "func2": 200, ...},
            "depth_2": {"func1": 50, "func2": 80, ...},
            ...
        }
    """
    # Pre-compute self line counts (used by all depth limits)
    self_counts: Dict[str, int] = {
        node: CallGraphHelper.compute_function_line_count(implementations, node)
        for node in all_nodes
    }
    
    results: Dict[str, Dict[str, int]] = {}
    
    for depth_limit in depth_limits:
        depth_key = "full_subtree" if depth_limit is None else f"depth_{depth_limit}"
        
        # Use memoization for this depth limit
        memo: Dict[Tuple[str, int], int] = {}
        
        def compute_with_memo(func: str, remaining_depth: int, visited: frozenset) -> int:
            """Compute with memoization on (func, remaining_depth)."""
            # Avoid cycles
            if func in visited:
                return 0
            
            if remaining_depth == 0:
                return self_counts.get(func, 0)
            
            key = (func, remaining_depth)
            if key in memo:
                return memo[key]
            
            total = self_counts.get(func, 0)
            new_visited = visited | {func}
            
            for child in dag_edges.get(func, set()):
                total += compute_with_memo(child, remaining_depth - 1, new_visited)
            
            memo[key] = total
            return total
        
        # Compute for all nodes
        node_counts: Dict[str, int] = {}
        for node in all_nodes:
            if depth_limit is None:
                # Full subtree - use large number as "unlimited"
                node_counts[node] = compute_with_memo(node, 1000, frozenset())
            else:
                node_counts[node] = compute_with_memo(node, depth_limit - 1, frozenset())
        
        results[depth_key] = node_counts
    
    return results


def compute_level_statistics(
    counts: Dict[str, int],
    levels: Dict[int, Set[str]]
) -> Dict[int, Dict[str, float]]:
    """
    Compute statistics (mean, min, max, median, count) for each level.
    
    Args:
        counts: Dictionary mapping function name to line count
        levels: Dictionary mapping level number to set of nodes at that level
        
    Returns:
        Dictionary mapping level to statistics dict
    """
    level_stats: Dict[int, Dict[str, float]] = {}
    
    for level, nodes in sorted(levels.items()):
        level_counts = [counts.get(node, 0) for node in nodes]
        
        if level_counts:
            level_stats[level] = {
                "mean": statistics.mean(level_counts),
                "min": min(level_counts),
                "max": max(level_counts),
                "median": statistics.median(level_counts),
                "count": len(level_counts)
            }
    
    return level_stats


def compute_multi_depth_level_statistics(
    graph: CallGraph,
    dag_edges: Dict[str, Set[str]],
    implementations: Dict[str, List[Dict[str, Any]]],
    depth_limits: List[Optional[int]] = DEPTH_LIMITS,
    max_graph_depth: int = 20
) -> Dict[str, Dict[int, Dict[str, float]]]:
    """
    Compute line count statistics for each level at multiple depth limits.
    
    Args:
        graph: The CallGraph instance
        dag_edges: The DAG edges after cycle breaking
        implementations: Function implementation locations
        depth_limits: List of depth limits to compute
        max_graph_depth: Maximum depth for level computation
        
    Returns:
        Dictionary mapping depth key to level statistics:
        {
            "full_subtree": {
                0: {"mean": X, "min": Y, "max": Z, "median": W, "count": N},
                1: {...},
                ...
            },
            "depth_2": {...},
            ...
        }
    """
    # Step 1: Compute levels from bottom
    levels = graph.compute_levels_from_bottom(max_graph_depth)
    
    # Step 2: Compute line counts for all nodes at all depth limits
    all_counts = compute_all_depth_limited_counts(
        dag_edges, graph.nodes, implementations, depth_limits
    )
    
    # Step 3: Compute statistics per level for each depth limit
    all_stats: Dict[str, Dict[int, Dict[str, float]]] = {}
    
    for depth_key, counts in all_counts.items():
        all_stats[depth_key] = compute_level_statistics(counts, levels)
    
    return all_stats


def format_text_output(
    stats: Dict[str, Dict[int, Dict[str, float]]],
    metadata: Dict[str, Any]
) -> str:
    """
    Format statistics as text output.
    
    Args:
        stats: Multi-depth level statistics
        metadata: Metadata about the analysis
        
    Returns:
        Formatted text string
    """
    lines = []
    
    # Header
    lines.append("=" * 80)
    lines.append("CONTEXT GENERATION PROFILE")
    lines.append("=" * 80)
    lines.append(f"Input: {metadata.get('input_file', 'N/A')}")
    lines.append(f"Total Functions: {metadata.get('total_functions', 0)}")
    lines.append(f"Total Levels: {metadata.get('total_levels', 0)}")
    lines.append("=" * 80)
    lines.append("")
    
    # Define depth limit labels
    depth_labels = {
        "full_subtree": "FULL SUBTREE STATISTICS (Self + All Descendants)",
        "depth_2": "DEPTH-LIMITED STATISTICS: 2 Levels (Self + Children)",
        "depth_3": "DEPTH-LIMITED STATISTICS: 3 Levels (Self + Children + Grandchildren)",
        "depth_4": "DEPTH-LIMITED STATISTICS: 4 Levels",
        "depth_5": "DEPTH-LIMITED STATISTICS: 5 Levels",
    }
    
    # Output each depth limit's statistics
    for depth_key in ["full_subtree", "depth_2", "depth_3", "depth_4", "depth_5"]:
        if depth_key not in stats:
            continue
        
        level_stats = stats[depth_key]
        label = depth_labels.get(depth_key, depth_key)
        
        lines.append("=" * 80)
        lines.append(label)
        lines.append("=" * 80)
        lines.append(f"{'Level':>5} | {'Count':>6} | {'Mean':>10} | {'Min':>8} | {'Max':>10} | {'Median':>10}")
        lines.append("-" * 80)
        
        for level in sorted(level_stats.keys()):
            s = level_stats[level]
            lines.append(
                f"{level:>5} | {int(s['count']):>6} | {s['mean']:>10.1f} | "
                f"{int(s['min']):>8} | {int(s['max']):>10} | {s['median']:>10.1f}"
            )
        
        lines.append("-" * 80)
        lines.append("")
    
    # Get all levels across all depth limits
    all_levels = set()
    for level_stats in stats.values():
        all_levels.update(level_stats.keys())
    
    # Comparison summary - Mean
    lines.append("=" * 80)
    lines.append("COMPARISON SUMMARY (Mean Line Counts by Level)")
    lines.append("=" * 80)
    
    # Header row
    header = f"{'Level':>5} | {'Full Tree':>12}"
    for depth in [2, 3, 4, 5]:
        header += f" | {'Depth ' + str(depth):>10}"
    lines.append(header)
    lines.append("-" * 80)
    
    # Data rows - Mean
    for level in sorted(all_levels):
        row = f"{level:>5}"
        
        # Full subtree
        if "full_subtree" in stats and level in stats["full_subtree"]:
            row += f" | {stats['full_subtree'][level]['mean']:>12.1f}"
        else:
            row += f" | {'N/A':>12}"
        
        # Depth-limited
        for depth in [2, 3, 4, 5]:
            depth_key = f"depth_{depth}"
            if depth_key in stats and level in stats[depth_key]:
                row += f" | {stats[depth_key][level]['mean']:>10.1f}"
            else:
                row += f" | {'N/A':>10}"
        
        lines.append(row)
    
    lines.append("-" * 80)
    lines.append("")
    
    # Comparison summary - Median
    lines.append("=" * 80)
    lines.append("COMPARISON SUMMARY (Median Line Counts by Level)")
    lines.append("=" * 80)
    
    # Header row
    header = f"{'Level':>5} | {'Full Tree':>12}"
    for depth in [2, 3, 4, 5]:
        header += f" | {'Depth ' + str(depth):>10}"
    lines.append(header)
    lines.append("-" * 80)
    
    # Data rows - Median
    for level in sorted(all_levels):
        row = f"{level:>5}"
        
        # Full subtree
        if "full_subtree" in stats and level in stats["full_subtree"]:
            row += f" | {stats['full_subtree'][level]['median']:>12.1f}"
        else:
            row += f" | {'N/A':>12}"
        
        # Depth-limited
        for depth in [2, 3, 4, 5]:
            depth_key = f"depth_{depth}"
            if depth_key in stats and level in stats[depth_key]:
                row += f" | {stats[depth_key][level]['median']:>10.1f}"
            else:
                row += f" | {'N/A':>10}"
        
        lines.append(row)
    
    lines.append("-" * 80)
    lines.append("=" * 80)
    
    return "\n".join(lines)


def format_json_output(
    stats: Dict[str, Dict[int, Dict[str, float]]],
    metadata: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Format statistics as JSON-serializable dictionary.
    
    Args:
        stats: Multi-depth level statistics
        metadata: Metadata about the analysis
        
    Returns:
        JSON-serializable dictionary
    """
    # Convert integer keys to strings for JSON compatibility
    json_stats = {}
    for depth_key, level_stats in stats.items():
        json_stats[depth_key] = {
            str(level): level_data
            for level, level_data in level_stats.items()
        }
    
    return {
        "metadata": metadata,
        "statistics": json_stats
    }


def main():
    """Main entry point for the script."""
    parser = argparse.ArgumentParser(
        description="Profile context generation by computing cumulative line counts per call tree level."
    )
    parser.add_argument(
        "-f",
        metavar="<path>",
        required=True,
        help="Path to merged call graph JSON file"
    )
    parser.add_argument(
        "-o", "--output",
        metavar="<path>",
        default="/tmp/context_profile.txt",
        help="Output file path (default: /tmp/context_profile.txt)"
    )
    parser.add_argument(
        "--max-depth",
        type=int,
        default=20,
        help="Maximum depth for cycle breaking (default: 20)"
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output in JSON format"
    )
    args = parser.parse_args()
    
    json_path = args.f
    
    # Load call graph
    print(f"Loading call graph from: {json_path}", file=sys.stderr)
    
    try:
        graph, implementations, raw_data = CallGraphHelper.load_call_graph(json_path)
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"Error: Invalid JSON file: {e}", file=sys.stderr)
        sys.exit(1)
    
    print(f"Loaded {graph.get_num_nodes()} nodes and {graph.get_num_edges()} edges", file=sys.stderr)
    
    # Create DAG by breaking cycles
    print("Creating DAG by breaking cycles...", file=sys.stderr)
    dag_edges = create_dag(graph, args.max_depth)
    
    # Compute levels
    levels = graph.compute_levels_from_bottom(args.max_depth)
    total_levels = max(levels.keys()) + 1 if levels else 0
    
    # Compute multi-depth statistics
    print("Computing multi-depth line count statistics...", file=sys.stderr)
    stats = compute_multi_depth_level_statistics(
        graph, dag_edges, implementations,
        DEPTH_LIMITS, args.max_depth
    )
    
    # Prepare metadata
    metadata = {
        "input_file": json_path,
        "total_functions": graph.get_num_nodes(),
        "total_levels": total_levels,
        "max_depth_setting": args.max_depth,
        "depth_limits_computed": ["full_subtree", "depth_2", "depth_3", "depth_4", "depth_5"]
    }
    
    # Output results
    if args.json:
        output_data = format_json_output(stats, metadata)
        CallGraphHelper.write_json_output(output_data, args.output, pretty=True)
        print(f"JSON output written to: {args.output}", file=sys.stderr)
    else:
        output_text = format_text_output(stats, metadata)
        CallGraphHelper.write_text_output(output_text, args.output)
        print(f"Text output written to: {args.output}", file=sys.stderr)
        
        # Also print to stdout
        print()
        print(output_text)


if __name__ == "__main__":
    main()
