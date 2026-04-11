#!/usr/bin/env python3

# Created by Sridhar Gurivireddy on 08/27/2025

import argparse
from pathlib import Path
from collections import deque
from typing import Dict, Any, List, Iterable, Set, Optional
from ...utils.file_util import read_json_file, write_json_file
from ...utils.log_util import get_logger, setup_default_logging

# Initialize logger
logger = get_logger(__name__)


class HotSpotUtil:
    """
    A utility class for processing and flattening callstack JSON data from aggregated micro stack shots.

    This class provides functionality to:
    - Flatten nested callstack trees into individual path entries
    - Filter entries based on process names (ownerName field)
    - Convert data to standardized individual entries format
    - Extract process information and validate path entries
    - Save processed results with proper logging

    Typical use case: Processing raw callstack data to create flattened representations
    suitable for further analysis by RandomSampler or other tools.
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
    def _convert_to_individual_entries(flattened_data: List[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
        """Convert flattened data to individual entries format."""
        result = []

        for entry in flattened_data:
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

            # Create individual entries for each level in the path
            individual_entries = []
            for i, path in enumerate(paths):
                # Skip invalid path entries
                if not HotSpotUtil._is_valid_path_entry(path):
                    continue

                individual_entry = {
                    "path": path,
                    "cost": costs[i] if i < costs_len else 0,
                    "normalizedCost": normalized_costs[i] if i < normalized_costs_len else 0.0,
                    "ownerName": owners[i] if i < owners_len else "",
                    "sourcePath": sources[i] if i < sources_len else ""
                }
                individual_entries.append(individual_entry)

            # Only add if we have valid entries
            if individual_entries:
                result.append(individual_entries)

        return result

    @staticmethod
    def process(_input: Path, _output: Path, filter_processes: Optional[List[str]] = None, threshold: float = 0.001) -> Dict[str, Any]:
        """
        Main processing method that flattens callstack data and applies optional filtering.

        Args:
            _input (Path): Path to input JSON file containing callstack data
            _output (Path): Path to output JSON file for processed results
            filter_processes (Optional[List[str]]): List of process names to filter by
            threshold (float): Minimum normalized_cost percentage to include callstack (default: 1.0)

        Returns:
            Dict[str, Any]: Original input data for reference
        """
        # Load input JSON using fileUtil
        data = read_json_file(str(_input))
        if data is None:
            logger.error(f"Failed to load JSON data from {_input}")
            return {}

        callstack = data.get("callstack", {})

        # Process and filter in a single pass to reduce memory usage
        all_entries = list(HotSpotUtil._iter_flat_paths(callstack))
        filtered_flattened = []

        for entry in all_entries:
            # Apply process filter if specified
            if filter_processes:
                filter_set = set(filter_processes)
                if not HotSpotUtil._should_include_entry(entry["ownerName"], filter_set):
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

        # Convert to individual entries format
        individual_entries = HotSpotUtil._convert_to_individual_entries(filtered_flattened)

        # Write output JSON using fileUtil
        success = write_json_file(str(_output), individual_entries, indent=2)
        if success:
            logger.info(f"Wrote {len(individual_entries)} entry groups to {_output}")
        else:
            logger.error(f"Failed to write results to {_output}")

        return data

    @staticmethod
    def process_to_text(_input: Path, _output: Path, libraries_of_interest: Optional[List[str]] = None, threshold: float = 1.0) -> None:
        """
        Dump callstacks to a text file with percentages attributed to each function.

        Args:
            _input (Path): Path to input JSON file containing callstack data
            _output (Path): Path to output text file for callstack dump
            libraries_of_interest (Optional[List[str]]): List of process names to filter by
            threshold (float): Minimum normalized_cost percentage to include callstack (default: 1.0)
        """
        # Load input JSON using fileUtil
        data = read_json_file(str(_input))
        if data is None:
            logger.error(f"Failed to load JSON data from {_input}")
            return

        callstack = data.get("callstack", {})

        # Process and filter callstacks
        all_entries = list(HotSpotUtil._iter_flat_paths(callstack))
        filtered_flattened = []

        for entry in all_entries:
            # Apply process filter if specified
            if libraries_of_interest:
                filter_set = set(libraries_of_interest)
                if not HotSpotUtil._should_include_entry(entry["ownerName"], filter_set):
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
        if libraries_of_interest:
            logger.info(f"Process filter: {libraries_of_interest}")
        if threshold > 0.0:
            logger.info(f"Threshold filter: {threshold}% minimum")

        # Write callstacks to text file
        try:
            with open(_output, 'w', encoding='utf-8') as f:
                for i, entry in enumerate(filtered_flattened):
                    if i > 0:
                        # Add separator between callstacks
                        f.write("\n\n========\n\n")

                    # Write callstack header
                    f.write(f"Callstack {i + 1}:\n")
                    f.write("-" * 40 + "\n")

                    # Write each function in the callstack with percentage
                    paths = entry["path"]
                    percentages = entry["normalized_cost"]
                    owners = entry["ownerName"]
                    sources = entry["sourcePath"]

                    for j, (path, percentage, owner, source) in enumerate(zip(paths, percentages, owners, sources)):
                        if HotSpotUtil._is_valid_path_entry(path):
                            f.write(f"  {j + 1}. {path} ({percentage:.4f}%)")
                            if owner:
                                f.write(f" [Owner: {owner}]")
                            if source:
                                f.write(f" [Source: {source}]")
                            f.write("\n")

            logger.info(f"Wrote {len(filtered_flattened)} callstacks to {_output}")

        except Exception as e:
            logger.error(f"Failed to write callstacks to {_output}: {e}")


def main():
    parser = argparse.ArgumentParser(
        description="Flatten callstack JSON tree into individual entries with process and sourcePath filtering"
    )
    parser.add_argument("-i", "--input", type=Path, required=True,
                        help="Path to input JSON file (with 'callstack' root)")
    parser.add_argument("-o", "--output", type=Path, required=True,
                        help="Path to output JSON file (individual entries format)")
    parser.add_argument("-p", "--processes", nargs="*",
                        help="List of process names to optionally filter by")
    parser.add_argument("-d", "--text-dump", type=Path,
                        help="Path to output text file for callstack dump with percentages")
    parser.add_argument("-t", "--threshold", type=float, default=0.001,
                        help="Minimum normalized_cost percentage to include callstack (default: 0.001)")
    args = parser.parse_args()


    HotSpotUtil.process(_input=args.input, _output=args.output, filter_processes=args.processes, threshold=args.threshold)

    if args.text_dump:
        # Dump callstacks to text file
        HotSpotUtil.process_to_text(_input=args.input, _output=args.text_dump, threshold=args.threshold)

if __name__ == "__main__":
    # Setup logging for standalone execution
    setup_default_logging()

    main()