#!/usr/bin/env python3
"""
JSON to Text Hotspots Converter

Converts JSON hotspot files to the text format that the trace analyzer expects.

Supports two input formats:
1. Nested callstack tree format (dict with 'callstack' key containing nested children)
2. Individual entries format (JSON array of callstack groups)

Usage:
    python dev/json_to_text_hotspots_converter.py input.json output.txt
    python dev/json_to_text_hotspots_converter.py input.json output.txt --filter CoreLocation LocationSupport
    
Output format: Text format with percentage, cost, library, function, and optional source file
"""

import json
import sys
import argparse
from pathlib import Path
from collections import deque
from typing import Dict, Any, List, Iterable, Set, Optional


def iter_flat_paths(callstack: Dict[str, Any],
                    value_key: str = "value",
                    name_key: str = "frameName",
                    owner_key: str = "ownerName",
                    source_key: str = "sourcePath") -> Iterable[Dict[str, Any]]:
    """Yield flattened paths with cost, ownerName, and sourcePath for a callstack tree."""
    
    stack = deque([(callstack,
                   [callstack.get(name_key, "<unnamed>")],
                   [callstack.get(value_key)],
                   [callstack.get(owner_key, "")],
                   [callstack.get(source_key, "")])])

    while stack:
        node, frames, vals, owners, sources = stack.pop()
        children = node.get("children")
        
        if not children:  # leaf node
            # Pre-calculate top value to avoid repeated access
            top_val = vals[0]
            top = float(top_val or 0.0)
            
            # Use list comprehensions for better performance
            rev_costs = [int(c or 0) for c in reversed(vals)]
            rev_owners = [o or "" for o in reversed(owners)]
            rev_sources = [s or "" for s in reversed(sources)]
            
            # Calculate percentages more efficiently
            if top:
                perc = [round(float(c) / top * 100.0, 4) for c in rev_costs]
            else:
                perc = [0.0] * len(rev_costs)
            
            yield {
                "path": [_n for _n in reversed(frames)],
                "cost": rev_costs,
                "normalized_cost": perc,
                "ownerName": rev_owners,
                "sourcePath": rev_sources
            }
        else:
            # Process children in reverse order to maintain original order
            new_items = []
            for ch in reversed(children):
                new_items.append((
                    ch,
                    frames + [ch.get(name_key, "<unnamed>")],
                    vals + [ch.get(value_key)],
                    owners + [ch.get(owner_key, "")],
                    sources + [ch.get(source_key, "")]
                ))
            stack.extend(new_items)


def should_include_entry(owners: List[str], filter_processes: Set[str]) -> bool:
    """Check if any ownerName in the path matches the filter processes."""
    if not filter_processes:
        return True
    return any(owner in filter_processes for owner in owners if owner)


def is_valid_path_entry(path: str) -> bool:
    """Check if path entry is valid (not ??? or empty)."""
    return bool(path and path.strip() and path.strip() != "???" and path.strip() != "<root>")


def convert_entry_to_text_line(path: str, cost: int, normalized_cost: float,
                                owner_name: str, source_path: str) -> str:
    """
    Convert a single entry to text format line.
    
    Returns:
        str: Formatted line like "2% (35012) libsystem_kernel.dylib mach_msg2_trap"
    """
    # Format percentage (round to nearest integer for display)
    percentage = f"{int(round(normalized_cost))}%"
    
    # Format cost in parentheses
    cost_str = f"({cost})"
    
    # Build the line: percentage (cost) library function_name
    if owner_name and path:
        line = f"{percentage} {cost_str} {owner_name} {path}"
    elif path:
        line = f"{percentage} {cost_str} {path}"
    elif owner_name:
        line = f"{percentage} {cost_str} {owner_name}"
    else:
        line = f"{percentage} {cost_str} unknown_function"
    
    # Add source file if available
    if source_path:
        # Extract just the filename from the source path
        source_filename = Path(source_path).name
        line += f" ({source_filename})"
    
    return line


def convert_nested_json_to_text(input_file: str, output_file: str,
                                 filter_processes: Optional[List[str]] = None,
                                 threshold: float = 1.0,
                                 max_traces: Optional[int] = None) -> bool:
    """
    Convert nested callstack JSON to text format.
    
    Args:
        input_file: Path to input JSON file
        output_file: Path to output text file
        filter_processes: Optional list of process/library names to filter by
        threshold: Minimum percentage threshold to include callstack
        max_traces: Optional limit on number of traces to convert
    """
    print(f"Converting {input_file} to {output_file}")
    
    # Load JSON data
    try:
        with open(input_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception as e:
        print(f"Error loading JSON file: {e}")
        return False
    
    # Handle nested callstack format (dict with 'callstack' key)
    if isinstance(data, dict) and 'callstack' in data:
        process_name = data.get('processName', 'unknown')
        print(f"Detected nested callstack format for process: {process_name}")
        
        callstack = data.get("callstack", {})
        filter_set = set(filter_processes) if filter_processes else None
        
        # Process and filter callstacks
        all_entries = list(iter_flat_paths(callstack))
        filtered_entries = []
        
        for entry in all_entries:
            # Apply process filter if specified
            if filter_set and not should_include_entry(entry["ownerName"], filter_set):
                continue
            
            # Apply threshold filter
            if threshold > 0.0:
                max_percentage = max(entry["normalized_cost"]) if entry["normalized_cost"] else 0.0
                if max_percentage < threshold:
                    continue
            
            filtered_entries.append(entry)
        
        print(f"Filtered {len(all_entries)} entries to {len(filtered_entries)} entries")
        if filter_processes:
            print(f"Process filter: {filter_processes}")
        if threshold > 0.0:
            print(f"Threshold filter: {threshold}% minimum")
        
        # Apply max_traces limit
        if max_traces and max_traces > 0:
            filtered_entries = filtered_entries[:max_traces]
            print(f"Limited to first {len(filtered_entries)} callstack groups")
        
        # Write to text format
        try:
            with open(output_file, 'w', encoding='utf-8') as f:
                for i, entry in enumerate(filtered_entries):
                    paths = entry["path"]
                    costs = entry["cost"]
                    percentages = entry["normalized_cost"]
                    owners = entry["ownerName"]
                    sources = entry["sourcePath"]
                    
                    # Write each function in the callstack
                    for j, (path, cost, percentage, owner, source) in enumerate(
                            zip(paths, costs, percentages, owners, sources)):
                        if is_valid_path_entry(path):
                            line = convert_entry_to_text_line(path, cost, percentage, owner, source)
                            f.write(line + '\n')
                    
                    # Add separator between callstacks (except for the last one)
                    if i < len(filtered_entries) - 1:
                        f.write('\n=====\n\n')
            
            print(f"Successfully converted {len(filtered_entries)} callstack groups to {output_file}")
            return True
            
        except Exception as e:
            print(f"Error writing output file: {e}")
            return False
    
    # Handle individual entries format (JSON array)
    elif isinstance(data, list):
        return convert_array_json_to_text(data, output_file, max_traces)
    
    else:
        print(f"Error: Unsupported JSON format. Expected dict with 'callstack' key or array.")
        return False


def convert_array_json_to_text(data: list, output_file: str, max_traces: Optional[int] = None) -> bool:
    """
    Convert JSON array format (individual entries) to text format.
    """
    print(f"Detected individual entries format with {len(data)} callstack groups")
    
    # Apply limit if specified
    if max_traces and max_traces > 0:
        data = data[:max_traces]
        print(f"Limited to first {len(data)} callstack groups")
    
    # Convert to text format
    try:
        with open(output_file, 'w', encoding='utf-8') as f:
            for i, callstack_group in enumerate(data):
                if not isinstance(callstack_group, list):
                    print(f"Warning: Skipping invalid callstack group {i} (not a list)")
                    continue
                
                # Convert each entry in the callstack group
                for entry in callstack_group:
                    if not isinstance(entry, dict):
                        print(f"Warning: Skipping invalid entry in group {i} (not a dict)")
                        continue
                    
                    path = entry.get('path', '')
                    cost = entry.get('cost', 0)
                    normalized_cost = entry.get('normalizedCost', 0.0)
                    owner_name = entry.get('ownerName', '')
                    source_path = entry.get('sourcePath', '')
                    
                    if is_valid_path_entry(path):
                        line = convert_entry_to_text_line(path, cost, normalized_cost, owner_name, source_path)
                        f.write(line + '\n')
                
                # Add separator between callstack groups (except for the last one)
                if i < len(data) - 1:
                    f.write('\n=====\n\n')
        
        print(f"Successfully converted {len(data)} callstack groups to {output_file}")
        return True
        
    except Exception as e:
        print(f"Error writing output file: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(
        description="Convert JSON hotspot files to text format for trace analyzer",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Convert entire file
  python dev/json_to_text_hotspots_converter.py input.json output.txt
  
  # Convert with process/library filter
  python dev/json_to_text_hotspots_converter.py input.json output.txt --filter CoreLocation LocationSupport
  
  # Convert only first 100 traces with 2% threshold
  python dev/json_to_text_hotspots_converter.py input.json output.txt --max-traces 100 --threshold 2.0
  
  # Convert with verbose output
  python dev/json_to_text_hotspots_converter.py input.json output.txt --verbose
        """
    )
    
    parser.add_argument(
        'input_file',
        help='Path to input JSON file (nested callstack or individual_entries format)'
    )
    
    parser.add_argument(
        'output_file',
        help='Path to output text file'
    )
    
    parser.add_argument(
        '--filter', '-f',
        nargs='*',
        dest='filter_processes',
        help='List of process/library names to filter by (e.g., CoreLocation LocationSupport)'
    )
    
    parser.add_argument(
        '--threshold', '-t',
        type=float,
        default=0.01,
        help='Minimum percentage threshold to include callstack (default: 0.01)'
    )
    
    parser.add_argument(
        '--max-traces', '-n',
        type=int,
        help='Maximum number of traces to convert (default: all)'
    )
    
    parser.add_argument(
        '--verbose', '-v',
        action='store_true',
        help='Enable verbose output'
    )
    
    args = parser.parse_args()
    
    # Validate input file exists
    if not Path(args.input_file).exists():
        print(f"Error: Input file does not exist: {args.input_file}")
        return 1
    
    # Create output directory if needed
    output_path = Path(args.output_file)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    # Perform conversion
    success = convert_nested_json_to_text(
        args.input_file,
        args.output_file,
        filter_processes=args.filter_processes,
        threshold=args.threshold,
        max_traces=args.max_traces
    )
    
    if success:
        print("Conversion completed successfully!")
        
        # Show file sizes
        input_size = Path(args.input_file).stat().st_size
        output_size = Path(args.output_file).stat().st_size
        print(f"Input file size: {input_size / 1024 / 1024:.2f} MB")
        print(f"Output file size: {output_size / 1024 / 1024:.2f} MB")
        
        return 0
    else:
        print("Conversion failed!")
        return 1


if __name__ == "__main__":
    sys.exit(main())