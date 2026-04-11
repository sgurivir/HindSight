#!/usr/bin/env python3

# Created by Sridhar Gurivireddy on 12/04/2025

import argparse
from pathlib import Path
from collections import deque
from typing import Dict, Any, List, Iterable, Set, Optional
from ...utils.file_util import read_json_file
from ...utils.log_util import get_logger, setup_default_logging

# Initialize logger
logger = get_logger(__name__)


class TraceSplitterUtil:
    """
    A utility class for processing and formatting callstack JSON data into human-readable text format.

    This class provides functionality to:
    - Flatten nested callstack trees into individual path entries
    - Filter entries based on process names (ownerName field)
    - Convert data to human-readable text format with percentages
    - Output callstacks delimited by separators for easy reading
    - Apply threshold filtering based on normalized cost percentages

    Output format: Each function shows percentage and process name in parentheses
    """

    @staticmethod
    def _iter_flat_paths(callstack: Dict[str, Any],
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
                # Use extend instead of individual appends for better performance
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

    @staticmethod
    def _get_default_process_list(data: Dict[str, Any]) -> List[str]:
        """Extract default process list from the original callstack data."""
        process_name = data.get("processName", "")
        return [process_name] if process_name else []

    @staticmethod
    def _should_include_entry(owners: List[str], filter_processes: Set[str]) -> bool:
        """Check if any ownerName in the path matches the filter processes."""
        if not filter_processes:
            return True
        # Use any() with generator expression for early termination
        return any(owner in filter_processes for owner in owners if owner)

    @staticmethod
    def _is_valid_path_entry(path: str) -> bool:
        """Check if path entry is valid (not ??? or empty)."""
        return bool(path and path.strip() and path.strip() != "???")

    @staticmethod
    def _format_callstack_to_text(entry: Dict[str, Any]) -> str:
        """
        Format a single callstack entry to human-readable text format.
        
        Format: percentage% (raw_cost) <library_name> <function_name> (<file_name>)
        """
        paths = entry["path"]
        costs = entry["cost"]
        normalized_costs = entry["normalized_cost"]
        owners = entry["ownerName"]
        sources = entry["sourcePath"]
        
        # Pre-calculate lengths to avoid repeated len() calls
        costs_len = len(costs)
        normalized_costs_len = len(normalized_costs)
        owners_len = len(owners)
        sources_len = len(sources)
        
        # Build the callstack text line by line
        callstack_lines = []
        for i, path in enumerate(paths):
            # Skip invalid path entries
            if not TraceSplitterUtil._is_valid_path_entry(path):
                continue
                
            # Get values for this level
            cost = costs[i] if i < costs_len else 0
            normalized_cost = normalized_costs[i] if i < normalized_costs_len else 0.0
            owner = owners[i] if i < owners_len else ""
            source = sources[i] if i < sources_len else ""
            
            # Format the line: percentage% (raw_cost) <library_name> <function_name> (<file_name>)
            percentage = int(round(normalized_cost))  # Round to nearest integer
            raw_cost = f"{normalized_cost:.2f}"  # Show 2 decimal places for raw cost
            
            # Start with percentage and cost
            line = f"{percentage}% ({raw_cost}) "
            
            # Add library name if available
            if owner:
                line += f"{owner} "
            
            # Add function name
            line += path
            
            # Add filename in parentheses if available
            if source:
                # Extract just the filename from the full path
                import os
                filename = os.path.basename(source)
                if filename:
                    line += f" ({filename})"
                
            callstack_lines.append(line)
        
        return "\n".join(callstack_lines)

    @staticmethod
    def process_to_text(_input: Path, _output: Path, filter_processes: Optional[List[str]] = None, threshold: float = 0.001) -> None:
        """
        Main processing method that converts callstack JSON to human-readable text format.

        Args:
            _input (Path): Path to input JSON file containing callstack data
            _output (Path): Path to output text file for human-readable callstack dump
            filter_processes (Optional[List[str]]): List of process names to filter by
            threshold (float): Minimum normalized_cost percentage to include callstack (default: 0.001)
        """
        # Load input JSON using fileUtil
        data = read_json_file(str(_input))
        if data is None:
            logger.error(f"Failed to load JSON data from {_input}")
            return

        callstack = data.get("callstack", {})

        # Process and filter callstacks
        all_entries = list(TraceSplitterUtil._iter_flat_paths(callstack))
        filtered_flattened = []

        for entry in all_entries:
            # Apply process filter if specified
            if filter_processes:
                filter_set = set(filter_processes)
                if not TraceSplitterUtil._should_include_entry(entry["ownerName"], filter_set):
                    continue

            # Apply threshold filter - check if top entry (first element) meets the threshold
            if threshold > 0.0:
                top_percentage = entry["normalized_cost"][0] if entry["normalized_cost"] else 0.0
                if top_percentage < threshold:
                    continue

            filtered_flattened.append(entry)

        # Sort by attributed normalized costs in reverse order (highest first)
        filtered_flattened.sort(key=lambda x: x["normalized_cost"][0] if x["normalized_cost"] else 0.0, reverse=True)

        logger.info(f"Filtered {len(all_entries)} entries to {len(filtered_flattened)} entries")
        if filter_processes:
            logger.info(f"Process filter: {filter_processes}")
        if threshold > 0.0:
            logger.info(f"Threshold filter: {threshold}% minimum")

        # Write callstacks to text file in the specified format
        try:
            with open(_output, 'w', encoding='utf-8') as f:
                for i, entry in enumerate(filtered_flattened):
                    if i > 0:
                        # Add separator between callstacks
                        f.write("\n\n=====\n\n")

                    # Format and write the callstack
                    callstack_text = TraceSplitterUtil._format_callstack_to_text(entry)
                    f.write(callstack_text)

            logger.info(f"Wrote {len(filtered_flattened)} callstacks to {_output}")

        except Exception as e:
            logger.error(f"Failed to write callstacks to {_output}: {e}")


def main():
    parser = argparse.ArgumentParser(
        description="Process callstack JSON into human-readable text format with percentage and process information"
    )
    parser.add_argument("-i", "--input", type=Path, required=True,
                        help="Path to input JSON file (with 'callstack' root)")
    parser.add_argument("-o", "--output", type=Path, required=True,
                        help="Path to output text file for human-readable callstack dump")
    parser.add_argument("-p", "--processes", nargs="*",
                        help="List of process names to optionally filter by")
    parser.add_argument("-t", "--threshold", type=float, default=0.001,
                        help="Minimum normalized_cost percentage to include callstack (default: 0.001)")
    args = parser.parse_args()

    TraceSplitterUtil.process_to_text(
        _input=args.input, 
        _output=args.output, 
        filter_processes=args.processes, 
        threshold=args.threshold
    )


if __name__ == "__main__":
    # Setup logging for standalone execution
    setup_default_logging()

    main()