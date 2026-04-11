# Created by Sridhar Gurivireddy on 09/27/2025

"""
Example
python3 -m hindsight.core.trace_util.RandomSampler --max 10 ~/bugs/hotspots/locationd_callstacks.json -p locationd -n 10
"""

import json
import random
import argparse
from typing import List, Dict, Any
from ...utils.file_util import read_json_file, write_json_file
from ...utils.log_util import get_logger

# Initialize logger
logger = get_logger(__name__)


class RandomSampler:
    """
    A utility class for filtering and randomly sampling JSON sublists from aggregated micro stack shot data.

    This class provides functionality to:
    - Load JSON data containing nested sublists of process information
    - Filter sublists based on target process names (ownerName field)
    - Apply optional length constraints (min/max subtree length)
    - Randomly sample a specified number of matching sublists
    - Transform output format to include only essential fields (path, process, sourcePath)
    - Save results to JSON files with proper logging

    Typical use case: Processing HotSpotUtil output to extract random samples of
    stack traces containing specific processes for further analysis.
    """

    @staticmethod
    def _has_matching_process(sublist: List[Dict[str, Any]], target_processes: List[str]) -> bool:
        """Check if any entry in the sublist contains at least one of the target processes."""
        for entry in sublist:
            if entry.get('ownerName', '') in target_processes:
                return True
        return False

    @staticmethod
    def _has_matching_path_substring(sublist: List[Dict[str, Any]], path_substring_filters: List[str]) -> bool:
        """Check if any entry in the sublist contains a path with any of the specified substrings."""
        if not path_substring_filters:
            return True  # No filters means all sublists match

        for entry in sublist:
            path = entry.get('path', '')
            for substring in path_substring_filters:
                if substring in path:
                    return True
        return False

    @staticmethod
    def filter_and_sample_sublists(data: List[List[Dict[str, Any]]],
                                  target_processes: List[str],
                                  n_sublists: int = None,
                                  max_subtree_length: int = None,
                                  min_subtree_length: int = None,
                                  top_n_percent: float = None,
                                  bottom_n_percent: float = None,
                                  path_substring_filters: List[str] = None) -> List[List[Dict[str, Any]]]:
        """
        Filter and randomly sample sublists based on process matching and length constraints.

        This method performs a multi-stage filtering process:
        1. Filter sublists containing at least one target process
        2. Apply minimum length filter (if specified)
        3. Apply maximum length filter (if specified)
        4. Apply top/bottom percentage exclusion filter (if specified)
        5. Randomly sample the requested number of sublists

        Args:
            data (List[List[Dict[str, Any]]]): Input data containing nested sublists of process entries
            target_processes (List[str]): List of process names to filter by (matches 'ownerName' field)
            n_sublists (int): Number of sublists to return in the final sample
            max_subtree_length (int, optional): Maximum allowed sublist length. None means no upper limit
            min_subtree_length (int, optional): Minimum required sublist length. None means no lower limit
            top_n_percent (float, optional): Percentage of data to exclude from the end of the list (e.g., 10.0 for 10%)
            bottom_n_percent (float, optional): Percentage of data to exclude from the beginning of the list (e.g., 5.0 for 5%)
            path_substring_filters (List[str], optional): List of substrings to filter by. Only sublists containing at least one entry with a path matching any of these substrings will be included

        Returns:
            List[List[Dict[str, Any]]]: Filtered and sampled sublists. May contain fewer than n_sublists
                                       if insufficient matching data is available
        """
        # Filter sublists that contain at least one target process
        filtered_sublists = [
            sublist for sublist in data
            if RandomSampler._has_matching_process(sublist, target_processes)
        ]

        # Apply path substring filter if specified
        if path_substring_filters:
            filtered_sublists = [
                sublist for sublist in filtered_sublists
                if RandomSampler._has_matching_path_substring(sublist, path_substring_filters)
            ]
            logger.info(f"Applied path substring filter for: {path_substring_filters}")

        # Apply min subtree length filter if specified
        if min_subtree_length is not None:
            filtered_sublists = [
                sublist for sublist in filtered_sublists
                if len(sublist) >= min_subtree_length
            ]
            logger.info(f"Applied min subtree length filter: {min_subtree_length}")

        # Apply max subtree length filter if specified
        if max_subtree_length is not None:
            filtered_sublists = [
                sublist for sublist in filtered_sublists
                if len(sublist) <= max_subtree_length
            ]
            logger.info(f"Applied max subtree length filter: {max_subtree_length}")

        # Apply top/bottom percentage exclusion filter if specified
        if top_n_percent is not None or bottom_n_percent is not None:
            if top_n_percent is not None and (top_n_percent < 0 or top_n_percent > 100):
                logger.error(f"Invalid top_n_percent: {top_n_percent}. Must be between 0 and 100.")
                return []
            if bottom_n_percent is not None and (bottom_n_percent < 0 or bottom_n_percent > 100):
                logger.error(f"Invalid bottom_n_percent: {bottom_n_percent}. Must be between 0 and 100.")
                return []
            if top_n_percent is not None and bottom_n_percent is not None and (top_n_percent + bottom_n_percent >= 100):
                logger.error(f"Combined top_n_percent ({top_n_percent}) and bottom_n_percent ({bottom_n_percent}) cannot be >= 100%")
                return []

            total_count = len(filtered_sublists)
            if total_count > 0:
                # Calculate indices to exclude from top and bottom of the list
                top_exclude_count = int((top_n_percent or 0) / 100.0 * total_count)
                bottom_exclude_count = int((bottom_n_percent or 0) / 100.0 * total_count)

                # Exclude from bottom (beginning of list) and top (end of list)
                start_idx = bottom_exclude_count
                end_idx = total_count - top_exclude_count

                if start_idx < end_idx:
                    filtered_sublists = filtered_sublists[start_idx:end_idx]
                    logger.info(f"Applied percentage filters - excluded {bottom_exclude_count} entries from beginning and {top_exclude_count} entries from end")
                    logger.info(f"Remaining sublists after percentage filtering: {len(filtered_sublists)}")
                else:
                    logger.warning(f"Percentage filters too restrictive - no sublists remaining")
                    filtered_sublists = []

        if not filtered_sublists:
            logger.warning(f"No sublists found containing processes: {target_processes}")
            return []

        # If n_sublists is None, return all filtered sublists (no sampling limit)
        if n_sublists is None:
            logger.info(f"No sampling limit specified - returning all {len(filtered_sublists)} matching sublists")
            return filtered_sublists

        # If we have fewer filtered sublists than requested, return all
        if len(filtered_sublists) <= n_sublists:
            logger.info(f"Found {len(filtered_sublists)} matching sublists (requested {n_sublists})")
            return filtered_sublists

        # Randomly sample N sublists
        sampled_sublists = random.sample(filtered_sublists, n_sublists)
        return sampled_sublists

    @staticmethod
    def _transform_output_format(sublists: List[List[Dict[str, Any]]]) -> List[List[Dict[str, str]]]:
        """Transform sublists to standardized output format with only path, process, and sourcePath fields."""
        result = []
        for sublist in sublists:
            transformed_sublist = []
            for entry in sublist:
                transformed_entry = {
                    "path": entry.get('path', ''),
                    "process": entry.get('ownerName', ''),
                    "sourcePath": entry.get('sourcePath', '')
                }
                transformed_sublist.append(transformed_entry)
            result.append(transformed_sublist)
        return result

    @staticmethod
    def _print_summary(result: List[List[Dict[str, str]]], target_processes: List[str]) -> None:
        """Generate and log a summary of the sampling results."""
        logger.info(f"Summary:")
        logger.info(f"- Returned {len(result)} sublists")
        for i, sublist in enumerate(result):
            processes_in_sublist = set(entry['process'] for entry in sublist if entry['process'])
            matching_processes = processes_in_sublist.intersection(set(target_processes))
            logger.info(f"- Sublist {i+1}: {len(sublist)} entries, matching processes: {list(matching_processes)}")

    @staticmethod
    def process_data(json_file: str,
                    target_processes: List[str],
                    num_sublists: int = None,
                    output_path: str = None,
                    seed: int = None,
                    print_output: bool = True,
                    max_subtree_length: int = None,
                    min_subtree_length: int = None,
                    top_n_percent: float = None,
                    bottom_n_percent: float = None,
                    path_substring_filters: List[str] = None) -> List[List[Dict[str, str]]]:
        """
        Main processing method that orchestrates the complete workflow for filtering and sampling stack trace data.

        This method performs the complete end-to-end processing pipeline:
        1. Load JSON data from input file
        2. Apply process-based filtering
        3. Apply optional length constraints
        4. Apply optional top/bottom percentage exclusion
        5. Randomly sample the requested number of sublists
        6. Transform to standardized output format
        7. Save results to output file
        8. Generate summary and optional console output

        Args:
            json_file (str): Path to the input JSON file (typically HotSpotUtil output containing nested sublists)
            target_processes (List[str]): List of process names to filter by (matches 'ownerName' field)
            num_sublists (int): Number of sublists to return in the final sample
            output_path (str): Path where the processed JSON results will be saved
            seed (int, optional): Random seed for reproducible sampling results. None means non-deterministic
            print_output (bool): Whether to log the full JSON output to console (default: True)
            max_subtree_length (int, optional): Maximum allowed sublist length. None means no upper limit
            min_subtree_length (int, optional): Minimum required sublist length. None means no lower limit
            top_n_percent (float, optional): Percentage of data to exclude from the end of the list (e.g., 10.0 for 10%)
            bottom_n_percent (float, optional): Percentage of data to exclude from the beginning of the list (e.g., 5.0 for 5%)
            path_substring_filters (List[str], optional): List of substrings to filter by. Only sublists containing at least one entry with a path matching any of these substrings will be included

        Returns:
            List[List[Dict[str, str]]]: Processed and sampled sublists in standardized format.
                                       Each entry contains 'path', 'process', and 'sourcePath' fields.
                                       Returns empty list if no matching data found or processing fails.
        """
        # Set random seed if provided
        if seed:
            random.seed(seed)

        # Load data
        data = read_json_file(json_file)
        if data is None:
            logger.error(f"Failed to load JSON data from {json_file}")
            return []

        if not isinstance(data, list):
            logger.error(f"Expected list data structure in {json_file}, got {type(data)}")
            return []

        logger.info(f"Loaded {len(data)} sublists from {json_file}")
        logger.info(f"Looking for processes: {target_processes}")
        if num_sublists is not None:
            logger.info(f"Requesting {num_sublists} sublists")
        else:
            logger.info("Requesting all matching sublists (no sampling limit)")
        if min_subtree_length is not None:
            logger.info(f"Min subtree length filter: {min_subtree_length}")
        else:
            logger.info("Min subtree length filter: none (all lengths allowed)")
        if max_subtree_length is not None:
            logger.info(f"Max subtree length filter: {max_subtree_length}")
        else:
            logger.info("Max subtree length filter: none (all lengths allowed)")
        if top_n_percent is not None:
            logger.info(f"Top percentage exclusion filter: {top_n_percent}%")
        else:
            logger.info("Top percentage exclusion filter: none")
        if bottom_n_percent is not None:
            logger.info(f"Bottom percentage exclusion filter: {bottom_n_percent}%")
        else:
            logger.info("Bottom percentage exclusion filter: none")
        if path_substring_filters:
            logger.info(f"Path substring filters: {path_substring_filters}")
        else:
            logger.info("Path substring filters: none")

        # Filter and sample sublists
        filtered_sublists = RandomSampler.filter_and_sample_sublists(data, target_processes, num_sublists, max_subtree_length, min_subtree_length, top_n_percent, bottom_n_percent, path_substring_filters)

        if not filtered_sublists:
            return []

        # Transform to required output format
        result = RandomSampler._transform_output_format(filtered_sublists)

        # Save to file if output path is provided
        if output_path:
            success = write_json_file(output_path, result, indent=2)
            if success:
                logger.info(f"Results saved to: {output_path}")
            else:
                logger.error(f"Failed to save results to: {output_path}")
        else:
            logger.info("No output path specified - results not saved to file")

        # Output result as JSON to console if requested
        if print_output:
            logger.info("Result:")
            logger.info(json.dumps(result, indent=2))

        # Print summary
        RandomSampler._print_summary(result, target_processes)

        return result


def main():
    parser = argparse.ArgumentParser(description='Filter and sample JSON sublists based on processes')
    parser.add_argument('json_file', help='Path to the JSON file')
    parser.add_argument('-n', '--num_sublists', type=int, default=2,
                       help='Number of sublists to return (default: 2)')
    parser.add_argument('-p', '--processes', nargs='+', required=True,
                       help='List of processes to filter by')
    parser.add_argument('-s', '--seed', type=int,
                       help='Random seed for reproducible results')
    parser.add_argument('-o', '--output', type=str, default='randomSample.json',
                       help='Output file path (default: randomSample.json)')
    parser.add_argument('--no-print', action='store_true',
                       help='Do not print results to console')
    parser.add_argument('-max', '--max-subtree-length', type=int, default=None,
                       help='Maximum length of sublists to include (default: no limit)')
    parser.add_argument('-min', '--min-subtree-length', type=int, default=None,
                       help='Minimum length of sublists to include (default: no limit)')
    parser.add_argument('--top-percent', type=float, default=None,
                       help='Percentage of data to exclude from the top (e.g., 10.0 for 10%%)')
    parser.add_argument('--bottom-percent', type=float, default=None,
                       help='Percentage of data to exclude from the bottom (e.g., 5.0 for 5%%)')
    parser.add_argument('--path-filters', nargs='+', default=None,
                       help='List of substrings to filter paths by (e.g., --path-filters _conformsToProtocol CLNotifierClient)')

    args = parser.parse_args()

    # Process data using the RandomSampler class
    RandomSampler.process_data(
        json_file=args.json_file,
        target_processes=args.processes,
        num_sublists=args.num_sublists,
        output_path=args.output,
        seed=args.seed,
        print_output=not args.no_print,
        max_subtree_length=args.max_subtree_length,
        min_subtree_length=args.min_subtree_length,
        top_n_percent=args.top_percent,
        bottom_n_percent=args.bottom_percent,
        path_substring_filters=args.path_filters
    )


if __name__ == "__main__":
    # Setup logging for standalone execution
    from ...utils.log_util import setup_default_logging
    setup_default_logging()

    # Example usage when run directly without arguments
    if len(__import__('sys').argv) == 1:
        logger.info("Example usage:")
        logger.info("python randomSampler.py out.json -n 2 -p LocationSupport locationd --no-print")
        logger.info("python randomSampler.py out.json -n 1 -p Foundation CoreFoundation -s 42 -o custom_output.json")
        logger.info("python randomSampler.py out.json -n 2 -p LocationSupport --no-print")
        logger.info("python randomSampler.py out.json -n 2 -p LocationSupport locationd -m 10 --no-print")
        logger.info("python randomSampler.py out.json -n 2 -p LocationSupport --min-subtree-length 5 --no-print")
        logger.info("python randomSampler.py out.json -n 2 -p LocationSupport -m 20 --min-subtree-length 5 --no-print")
        logger.info("python randomSampler.py out.json -n 2 -p LocationSupport --top-percent 10.0 --bottom-percent 5.0 --no-print")
        logger.info("python randomSampler.py out.json -n 2 -p LocationSupport --path-filters _conformsToProtocol CLNotifierClient --no-print")
        logger.info("For help: python randomSampler.py -h")
    else:
        main()