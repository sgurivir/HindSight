#!/usr/bin/env python3
"""
Diff Hotspots

Compares two hotspot JSON files and shows the differences in function costs.
Only compares functions from libraries specified in the --filter argument.

Input format: Same as hotspot_function_aggregator.py (nested callstack JSON)

Output:
- Common functions with costs from both files (A and B)
- Functions only in file A
- Functions only in file B

Usage:
    python3 dev/diff_hotspots.py --a file_a.json --b file_b.json --filter CoreLocation LocationSupport
    python3 dev/diff_hotspots.py --a file_a.json --b file_b.json --filter CoreLocation --sort-by inclusive
"""

import json
import sys
import argparse
from pathlib import Path
from collections import deque
from typing import Dict, Any, List, Set, Optional, Tuple


def extract_filename(source_path: str) -> str:
    """Extract just the filename from a source path."""
    if not source_path:
        return ""
    return Path(source_path).name


def is_valid_function(function_name: str) -> bool:
    """Check if function name is valid (not ??? or empty or <root>)."""
    return bool(function_name and 
                function_name.strip() and 
                function_name.strip() != "???" and 
                function_name.strip() != "<root>" and
                function_name.strip() != "<unnamed>")


def should_include_library(library_name: str, filter_libraries: Set[str]) -> bool:
    """Check if library should be included based on filter."""
    if not filter_libraries:
        return True
    return library_name in filter_libraries


def iter_all_nodes_with_costs(callstack: Dict[str, Any],
                               value_key: str = "value",
                               name_key: str = "frameName",
                               owner_key: str = "ownerName",
                               source_key: str = "sourcePath") -> List[Dict[str, Any]]:
    """
    Yield all nodes from the callstack tree with both self-time and inclusive costs.
    
    Self time = node's value - sum of ALL children's values
    Inclusive cost = node's value (total time including children)
    """
    all_nodes = []
    stack = deque([callstack])
    
    while stack:
        node = stack.pop()
        children = node.get("children", [])
        node_value = int(node.get(value_key) or 0)
        file_name = node.get(source_key, "")
        
        # Calculate self time (always subtract ALL children)
        if not children:
            self_time = node_value
        else:
            children_sum = sum(int(child.get(value_key) or 0) for child in children)
            self_time = node_value - children_sum
            # Add children to stack for processing
            stack.extend(children)
        
        # Include nodes with either positive self_time OR positive inclusive cost
        if self_time > 0 or node_value > 0:
            all_nodes.append({
                "function_name": node.get(name_key, "<unnamed>"),
                "self_cost": self_time,
                "inclusive_cost": node_value,
                "library_name": node.get(owner_key, ""),
                "file_name": file_name
            })
    
    return all_nodes


def aggregate_functions_from_json(data: Dict[str, Any],
                                   filter_libraries: Set[str],
                                   liberal_matching: bool = False) -> Tuple[Dict[str, Dict[str, Any]], int]:
    """
    Aggregate function costs from nested callstack format.
    
    Returns:
        Tuple of (aggregated_dict, total_cost)
        
    aggregated_dict format:
        Key: (function_name, library_name) if liberal_matching else (file_name, function_name, library_name) as string
        Value: {"self_cost": int, "inclusive_cost": int, "normalized_self_cost": float, "normalized_inclusive_cost": float}
    
    Args:
        data: The JSON data containing callstack
        filter_libraries: Set of library names to filter by
        liberal_matching: If True, match by function_name and library_name only (ignoring file_name)
    """
    callstack = data.get("callstack", {})
    
    # Get total cost from root node
    total_cost = int(callstack.get("value") or 0)
    
    # Get all nodes with costs
    all_nodes = iter_all_nodes_with_costs(callstack)
    
    # Accumulate costs
    aggregated: Dict[str, Dict[str, Any]] = {}
    
    for node in all_nodes:
        function_name = node["function_name"]
        library_name = node["library_name"]
        file_name = extract_filename(node["file_name"])
        self_cost = node["self_cost"]
        inclusive_cost = node["inclusive_cost"]
        
        # Skip invalid functions
        if not is_valid_function(function_name):
            continue
        
        # Apply library filter
        if filter_libraries and not should_include_library(library_name, filter_libraries):
            continue
        
        # Create composite key - liberal matching ignores file_name
        if liberal_matching:
            key = f"({function_name}, {library_name})"
        else:
            key = f"({file_name}, {function_name}, {library_name})"
        
        if key in aggregated:
            # Add to existing costs
            aggregated[key]["self_cost"] += self_cost
            aggregated[key]["inclusive_cost"] += inclusive_cost
            # Keep track of all file names seen (for display purposes)
            if file_name and file_name not in aggregated[key].get("file_names", []):
                aggregated[key].setdefault("file_names", []).append(file_name)
        else:
            # Initialize new entry
            aggregated[key] = {
                "file_name": file_name,
                "file_names": [file_name] if file_name else [],
                "function_name": function_name,
                "library_name": library_name,
                "self_cost": self_cost,
                "inclusive_cost": inclusive_cost,
                "normalized_self_cost": 0.0,
                "normalized_inclusive_cost": 0.0
            }
    
    # Calculate normalized costs
    if total_cost > 0:
        for key in aggregated:
            aggregated[key]["normalized_self_cost"] = round(
                (aggregated[key]["self_cost"] / total_cost) * 100.0, 4
            )
            aggregated[key]["normalized_inclusive_cost"] = round(
                (aggregated[key]["inclusive_cost"] / total_cost) * 100.0, 4
            )
    
    # Filter out entries with very low costs
    filtered_aggregated = {
        k: v for k, v in aggregated.items()
        if v["normalized_self_cost"] >= 0.0001 or v["normalized_inclusive_cost"] >= 0.01
    }
    
    return filtered_aggregated, total_cost


def load_and_aggregate(file_path: str, filter_libraries: Set[str], liberal_matching: bool = False) -> Tuple[Dict[str, Dict[str, Any]], int, str]:
    """
    Load a hotspot JSON file and aggregate function costs.
    
    Returns:
        Tuple of (aggregated_dict, total_cost, process_name)
    """
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception as e:
        print(f"Error loading {file_path}: {e}")
        sys.exit(1)
    
    if not isinstance(data, dict) or 'callstack' not in data:
        print(f"Error: {file_path} is not in nested callstack format")
        sys.exit(1)
    
    process_name = data.get("processName", "unknown")
    aggregated, total_cost = aggregate_functions_from_json(data, filter_libraries, liberal_matching)
    
    return aggregated, total_cost, process_name


def diff_hotspots(
    file_a: str,
    file_b: str,
    filter_libraries: List[str],
    sort_by: str = "inclusive",
    liberal_matching: bool = False
) -> None:
    """
    Compare two hotspot files and print the differences.
    
    Args:
        file_a: Path to first hotspot JSON file
        file_b: Path to second hotspot JSON file
        filter_libraries: List of library names to filter by
        sort_by: Sort order for common functions ("inclusive", "self", "diff")
        liberal_matching: If True, match by function_name and library_name only (ignoring file_name)
    """
    filter_set = set(filter_libraries) if filter_libraries else set()
    
    # Get file names for display
    name_a = Path(file_a).name
    name_b = Path(file_b).name
    
    print(f"\n{'=' * 80}")
    print(f"HOTSPOT DIFF")
    print(f"{'=' * 80}")
    print(f"File A: {name_a}")
    print(f"File B: {name_b}")
    if filter_libraries:
        print(f"Filter libraries: {', '.join(filter_libraries)}")
    if liberal_matching:
        print(f"Matching mode: LIBERAL (ignoring file names)")
    else:
        print(f"Matching mode: STRICT (file + function + library)")
    print(f"{'=' * 80}\n")
    
    # Load and aggregate both files
    print(f"Loading {name_a}...")
    agg_a, total_a, process_a = load_and_aggregate(file_a, filter_set, liberal_matching)
    print(f"  Process: {process_a}")
    print(f"  Total cost: {total_a}")
    print(f"  Functions: {len(agg_a)}")
    
    print(f"\nLoading {name_b}...")
    agg_b, total_b, process_b = load_and_aggregate(file_b, filter_set, liberal_matching)
    print(f"  Process: {process_b}")
    print(f"  Total cost: {total_b}")
    print(f"  Functions: {len(agg_b)}")
    
    # Find common, only-A, and only-B functions
    keys_a = set(agg_a.keys())
    keys_b = set(agg_b.keys())
    
    common_keys = keys_a & keys_b
    only_a_keys = keys_a - keys_b
    only_b_keys = keys_b - keys_a
    
    print(f"\n{'=' * 80}")
    print(f"SUMMARY")
    print(f"{'=' * 80}")
    print(f"Common functions: {len(common_keys)}")
    print(f"Only in {name_a}: {len(only_a_keys)}")
    print(f"Only in {name_b}: {len(only_b_keys)}")
    
    # Print common functions with diff
    if common_keys:
        print(f"\n{'=' * 80}")
        print(f"COMMON FUNCTIONS (sorted by {sort_by})")
        print(f"{'=' * 80}")
        print(f"{'Function':<60} | {name_a:>12} | {name_b:>12} | {'Diff':>12}")
        print(f"{'-' * 60}-+-{'-' * 12}-+-{'-' * 12}-+-{'-' * 12}")
        
        # Build list with diff values for sorting
        common_list = []
        for key in common_keys:
            val_a = agg_a[key]
            val_b = agg_b[key]
            
            if sort_by == "self":
                cost_a = val_a["self_cost"]
                cost_b = val_b["self_cost"]
            else:  # inclusive or diff
                cost_a = val_a["inclusive_cost"]
                cost_b = val_b["inclusive_cost"]
            
            diff = cost_b - cost_a
            diff_pct = ((cost_b - cost_a) / cost_a * 100) if cost_a > 0 else (100 if cost_b > 0 else 0)
            
            common_list.append({
                "key": key,
                "cost_a": cost_a,
                "cost_b": cost_b,
                "diff": diff,
                "diff_pct": diff_pct,
                "norm_a": val_a["normalized_inclusive_cost"] if sort_by != "self" else val_a["normalized_self_cost"],
                "norm_b": val_b["normalized_inclusive_cost"] if sort_by != "self" else val_b["normalized_self_cost"]
            })
        
        # Sort based on sort_by parameter
        if sort_by == "diff":
            common_list.sort(key=lambda x: abs(x["diff"]), reverse=True)
        else:
            common_list.sort(key=lambda x: max(x["cost_a"], x["cost_b"]), reverse=True)
        
        for item in common_list:
            # Truncate function key for display
            display_key = item["key"][:57] + "..." if len(item["key"]) > 60 else item["key"]
            
            diff_str = f"{item['diff']:+d}"
            if item["diff_pct"] != 0:
                diff_str += f" ({item['diff_pct']:+.1f}%)"
            
            print(f"{display_key:<60} | {item['cost_a']:>12,} | {item['cost_b']:>12,} | {diff_str:>12}")
    
    # Print functions only in A
    if only_a_keys:
        print(f"\n{'=' * 80}")
        print(f"ONLY IN {name_a}")
        print(f"{'=' * 80}")
        
        only_a_list = [(key, agg_a[key]) for key in only_a_keys]
        only_a_list.sort(key=lambda x: x[1]["inclusive_cost"], reverse=True)
        
        print(f"{'Function':<70} | {'Self Cost':>12} | {'Incl Cost':>12} | {'Norm %':>8}")
        print(f"{'-' * 70}-+-{'-' * 12}-+-{'-' * 12}-+-{'-' * 8}")
        
        for key, val in only_a_list:
            display_key = key[:67] + "..." if len(key) > 70 else key
            print(f"{display_key:<70} | {val['self_cost']:>12,} | {val['inclusive_cost']:>12,} | {val['normalized_inclusive_cost']:>7.4f}%")
    
    # Print functions only in B
    if only_b_keys:
        print(f"\n{'=' * 80}")
        print(f"ONLY IN {name_b}")
        print(f"{'=' * 80}")
        
        only_b_list = [(key, agg_b[key]) for key in only_b_keys]
        only_b_list.sort(key=lambda x: x[1]["inclusive_cost"], reverse=True)
        
        print(f"{'Function':<70} | {'Self Cost':>12} | {'Incl Cost':>12} | {'Norm %':>8}")
        print(f"{'-' * 70}-+-{'-' * 12}-+-{'-' * 12}-+-{'-' * 8}")
        
        for key, val in only_b_list:
            display_key = key[:67] + "..." if len(key) > 70 else key
            print(f"{display_key:<70} | {val['self_cost']:>12,} | {val['inclusive_cost']:>12,} | {val['normalized_inclusive_cost']:>7.4f}%")
    
    print(f"\n{'=' * 80}")
    print("DIFF COMPLETE")
    print(f"{'=' * 80}\n")


def main():
    parser = argparse.ArgumentParser(
        description="Compare two hotspot JSON files and show differences in function costs",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Compare two hotspot files with library filter
  python dev/diff_hotspots.py --a hotspot_a.json --b hotspot_b.json --filter CoreLocation LocationSupport
  
  # Sort by inclusive cost instead of diff
  python dev/diff_hotspots.py --a hotspot_a.json --b hotspot_b.json --filter CoreLocation --sort-by inclusive
  
  # Sort by self cost
  python dev/diff_hotspots.py --a hotspot_a.json --b hotspot_b.json --filter CoreLocation --sort-by self
        """
    )
    
    parser.add_argument(
        '--a', '-a',
        required=True,
        dest='file_a',
        help='Path to first hotspot JSON file'
    )
    
    parser.add_argument(
        '--b', '-b',
        required=True,
        dest='file_b',
        help='Path to second hotspot JSON file'
    )
    
    parser.add_argument(
        '--filter',
        nargs='+',
        required=True,
        dest='filter_libraries',
        help='List of library names to filter by (required)'
    )
    
    parser.add_argument(
        '--sort-by',
        choices=['inclusive', 'self', 'diff'],
        default='diff',
        dest='sort_by',
        help='Sort common functions by: absolute diff (default), inclusive cost, or self cost'
    )
    
    parser.add_argument(
        '--liberal',
        action='store_true',
        dest='liberal_matching',
        help='Use liberal matching: match by function_name and library_name only, ignoring file_name differences'
    )
    
    args = parser.parse_args()
    
    # Validate input files exist
    if not Path(args.file_a).exists():
        print(f"Error: File does not exist: {args.file_a}")
        return 1
    
    if not Path(args.file_b).exists():
        print(f"Error: File does not exist: {args.file_b}")
        return 1
    
    # Handle comma-separated filter libraries
    expanded_libs = []
    for lib in args.filter_libraries:
        expanded_libs.extend([l.strip() for l in lib.split(',') if l.strip()])
    
    diff_hotspots(
        args.file_a,
        args.file_b,
        expanded_libs,
        args.sort_by,
        args.liberal_matching
    )
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
