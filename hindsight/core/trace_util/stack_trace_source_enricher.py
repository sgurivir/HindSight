#!/usr/bin/env python3

"""
Processes stack traces to get source code context
and then adds invoking information from call graph data.
"""

import os
import argparse
from typing import Dict, Any, List
from ..lang_util.ast_call_graph_parser import CallGraphUtilities
from ..lang_util.ast_util_symbol_demangler import SymbolDemanglerUtil
from ...utils.file_util import read_json_file, write_json_file, extract_function_context
from ...utils.log_util import get_logger, setup_default_logging

# Initialize logger
logger = get_logger(__name__)


# File I/O Constants
FILE_READ_ENCODING = "utf-8"
FILE_READ_ERRORS = "ignore"

# Processing Constants
ROOT_SYMBOL = '<root>'
MAIN_FUNCTION_NAME = "main"
NS_APPLICATION_MAIN = "NSApplicationMain"
SIMPLE_MAIN_FUNCTION_MAX_LINES = 10  # Maximum lines for a main function to be considered "simple"

# Dictionary Key Constants
CONTEXT_KEY = "context"
MATCHED_FUNCTION_KEY = "matched_function_key"
FUNCTIONS_INVOKED_KEY = "functions_invoked"
INVOKING_KEY = "invoking"
SYMBOL_KEY = "symbol"
PROCESS_KEY = "process"
SOURCE_PATH_KEY = "sourcePath"
FUNCTION_KEY = "function"
FUNCTION_CONTEXT_KEY = "functionContext"
CALL_STACK_KEY = "callStack"
FILE_KEY = "file"
START_LINE_NUMBER_KEY = "start"
END_LINE_NUMBER_KEY = "end"
FILE_NAME_KEY = "file_name"
START_KEY = "start"
END_KEY = "end"
PATH_KEY = "path"


class StackTraceSourceEnricher:
    """
    A stack trace enricher that extracts source code context and adds invoking information.

    This class provides functionality to:
    - Process stack trace samples to extract source code context using symbol demangling
    - Add invoking information from call graph data
    - Extract function context from source files with line numbers
    - Match functions using fuzzy matching algorithms
    - Generate comprehensive output with call stacks and context information
    - Optimize processing by eliminating intermediate steps

    Typical use case: Processing random samples from HotSpotUtil to create enriched
    stack traces with source code context for analysis and debugging.
    """

    def __init__(self, flat_call_stack_list_file: str,
                 nested_call_graph_file: str,
                 output_dir: str,
                 repo_path: str,
                 target_processes: List[str],
                 defined_functions_file: str):
        """Initialize the StackTraceSourceEnricher.

        Args:
            flat_call_stack_list_file: Path to the input JSON file containing flattened stack traces (output of HotSpotUtil/RandomSampler).
            nested_call_graph_file: Path to the nested call graph JSON file.
            output_dir: Directory where the individual trace files will be saved.
            repo_path: Path to the repository containing source code.
            target_processes: List of process names to analyze with demangler.
            defined_functions_file: Path to the defined functions JSON file.
        """
        self.flat_call_stack_list_file = flat_call_stack_list_file
        self.nested_call_graph_file = nested_call_graph_file
        self.output_dir = output_dir
        self.repo_path = repo_path
        self.target_processes = target_processes
        self.defined_functions_file = defined_functions_file
        self.call_graph_map = {}
        self.demangler = None

        # Initialize call graph utilities
        self.call_graph_utils = CallGraphUtilities(repo_path)

    def _process_entry_with_demangler(self, entry: Dict) -> Dict:
        """Process a single entry with demangler and add context."""
        # Get the symbol path and process from the entry
        print(entry)
        sym = entry[PATH_KEY]
        process = entry[PROCESS_KEY]
        source_path = entry[SOURCE_PATH_KEY]

        # Create the new output format
        output_entry = {
            SYMBOL_KEY: sym,
            PROCESS_KEY: process,
            SOURCE_PATH_KEY: source_path
        }

        # Only run demangler if the process is in the target list
        if process in self.target_processes:
            # Run demangler on the symbol
            matches, key = self.demangler.best_match_defs(sym)

            # Add context field based on demangler result
            if matches and len(matches) > 0:
                # Use the first (best) match
                best_match = matches[0]
                output_entry[CONTEXT_KEY] = {
                    FILE_KEY: best_match.get(FILE_NAME_KEY, source_path),
                    START_LINE_NUMBER_KEY: best_match.get(START_KEY),
                    END_LINE_NUMBER_KEY: best_match.get(END_KEY)
                }
                # Store the key for later use in context entries
                output_entry[MATCHED_FUNCTION_KEY] = key
            else:
                # No match found, set context to None
                output_entry[CONTEXT_KEY] = None
                output_entry[MATCHED_FUNCTION_KEY] = None
        else:
            # Process not in target list, set context to None
            output_entry[CONTEXT_KEY] = None
            output_entry[MATCHED_FUNCTION_KEY] = None

        return output_entry

    def _should_include_invoking_info(self, function_name: str, context_item: dict) -> bool:
        """Determine if we should include invoking info for this function."""
        # Skip simple main functions that just call NSApplicationMain
        if function_name == MAIN_FUNCTION_NAME:
            symbol_context = context_item.get(CONTEXT_KEY, {}).get(FUNCTION_CONTEXT_KEY, "")
            if NS_APPLICATION_MAIN in symbol_context and len(symbol_context.split('\n')) < SIMPLE_MAIN_FUNCTION_MAX_LINES:
                return False

        return True

    def _get_invoking_info(self, function_name: str) -> List[Dict[str, Any]]:
        """Get invoking information for a function from the call graph with recursive resolution."""
        matching_func = CallGraphUtilities.find_matching_function(function_name, self.call_graph_map)

        if matching_func and matching_func in self.call_graph_map:
            call_graph_entry = self.call_graph_map[matching_func]
            functions_invoked = call_graph_entry.get(FUNCTIONS_INVOKED_KEY, [])

            if functions_invoked:
                # functions_invoked is a list of function names
                resolved_invoking = [{"function": func_name} for func_name in functions_invoked]
            else:
                resolved_invoking = []
                return resolved_invoking

        return []

    def enrich_stack_traces(self) -> List[str]:
        """Main processing function that enriches stack traces with source code context and invoking information.

        Processes the input file through the following steps:
        1. Load nested call graph
        2. Initialize demangler
        3. Load input data (flat call stack)
        4. Process each stack trace with demangler and extract context
        5. Add invoking information
        6. Save each trace to individual files

        Returns:
            List of file paths where individual traces were saved.
        """
        logger.info(f"Processing {self.flat_call_stack_list_file} with enrichment pipeline...")
        logger.info(f"Target processes: {self.target_processes}")

        # Step 1: Load nested call graph data
        self.call_graph_map = CallGraphUtilities.load_call_graph_data(self.nested_call_graph_file)

        # Step 2: Initialize demangler
        logger.info("Initializing demangler...")
        self.demangler = SymbolDemanglerUtil(self.defined_functions_file)

        # Step 3: Load input data (flat call stack)
        logger.info("Loading flat call stack list data...")
        call_stack_lists = read_json_file(self.flat_call_stack_list_file)
        if call_stack_lists is None:
            logger.error(f"Failed to load flat call stack list data from {self.flat_call_stack_list_file}")
            return []

        logger.info(f"Processing {len(call_stack_lists)} stack traces...")

        # Create output directory if it doesn't exist
        os.makedirs(self.output_dir, exist_ok=True)

        total_functions = 0
        functions_with_invoking = 0
        saved_files = []

        # Step 4: Process each stack trace
        for trace_idx, stack_trace in enumerate(call_stack_lists):
            processed_stack_trace = []

            # Process each entry with demangler and extract context
            for entry in stack_trace:
                print(entry)
                continue
                processed_entry = self._process_entry_with_demangler(entry)

                # Add function context directly
                function_context = extract_function_context(processed_entry, self.repo_path)
                processed_entry[FUNCTION_CONTEXT_KEY] = function_context

                processed_stack_trace.append(processed_entry)

            # Reformat to final output structure
            call_stack = []
            context_entries = []

            # Reverse the stack trace to show from root to current function
            for entry in reversed(processed_stack_trace):
                symbol = entry.get(SYMBOL_KEY, '')
                process = entry.get(PROCESS_KEY, '')

                # Format the call stack entry
                if symbol == ROOT_SYMBOL:
                    call_stack.append(ROOT_SYMBOL)
                elif process:
                    call_stack.append(f"{symbol} (in {process})")
                else:
                    call_stack.append(symbol)

                # Add to context if there's function context available
                if entry.get(FUNCTION_CONTEXT_KEY) and entry[FUNCTION_CONTEXT_KEY].strip():
                    context_entry = {
                        SYMBOL_KEY: symbol,
                        FUNCTION_KEY: entry.get(MATCHED_FUNCTION_KEY, ''),
                        CONTEXT_KEY: {
                            FILE_KEY: entry.get(CONTEXT_KEY, {}).get(FILE_KEY, '') if entry.get(CONTEXT_KEY) else '',
                            START_LINE_NUMBER_KEY: entry.get(CONTEXT_KEY, {}).get(START_LINE_NUMBER_KEY, 0) if entry.get(CONTEXT_KEY) else 0,
                            END_LINE_NUMBER_KEY: entry.get(CONTEXT_KEY, {}).get(END_LINE_NUMBER_KEY, 0) if entry.get(CONTEXT_KEY) else 0,
                            FUNCTION_CONTEXT_KEY: entry.get(FUNCTION_CONTEXT_KEY, '')
                        }
                    }

                    # Step 5: Add invoking information
                    function_name = context_entry[FUNCTION_KEY]
                    if function_name:
                        total_functions += 1

                        # Check if we should include invoking info for this function
                        if self._should_include_invoking_info(function_name, context_entry):
                            invoking_info = self._get_invoking_info(function_name)
                            context_entry[INVOKING_KEY] = invoking_info
                            if invoking_info:
                                functions_with_invoking += 1
                        else:
                            context_entry[INVOKING_KEY] = []
                    else:
                        context_entry[INVOKING_KEY] = []

                    context_entries.append(context_entry)

            reformatted_trace = {
                CALL_STACK_KEY: call_stack,
                CONTEXT_KEY: context_entries
            }

            # Save individual trace to separate file
            trace_filename = f"trace_{trace_idx:04d}.json"
            trace_filepath = os.path.join(self.output_dir, trace_filename)
            success = write_json_file(trace_filepath, reformatted_trace, indent=2)
            if success:
                saved_files.append(trace_filepath)
            else:
                logger.error(f"Failed to save trace {trace_idx} to {trace_filepath}")

            if (trace_idx + 1) % 10 == 0:
                logger.info(f"Processed {trace_idx + 1}/{len(call_stack_lists)} traces...")

        # Step 6: Results summary
        logger.info("Processing complete!")
        logger.info(f"Successfully processed {len(call_stack_lists)} stack traces")
        logger.info(f"Saved {len(saved_files)} individual trace files to {self.output_dir}")
        logger.info(f"Total functions processed: {total_functions}")
        logger.info(f"Functions with invoking information: {functions_with_invoking}")
        logger.info(f"Available functions in call graph: {len(self.call_graph_map)}")

        return saved_files


def main():
    """Main function with argument parsing."""
    parser = argparse.ArgumentParser(
        description="Process stack traces to get source code context and add invoking information from call graph data."
    )

    # Required arguments
    parser.add_argument(
        "--traces-json",
        type=str,
        required=True,
        help="Path to the input JSON file containing flattened stack traces (output of HotSpotUtil/RandomSampler)"
    )
    parser.add_argument(
        "--repo",
        type=str,
        required=True,
        help="Path to the repository containing source code"
    )

    # Optional arguments with defaults
    parser.add_argument(
        "-o", "--output",
        help="Output directory for processed traces (default: /tmp/<repo_name>/processed_traces)",
        default=None
    )
    parser.add_argument(
        "--nested-call-graph",
        default="merged_call_graph.json",
        help="Path to the nested call graph JSON file (default: merged_call_graph.json)"
    )
    parser.add_argument(
        "--defined-functions",
        default="merged_functions.json",
        help="Path to the defined functions JSON file (default: merged_functions.json)"
    )
    parser.add_argument(
        "--target-processes",
        nargs="+",
        default=['locationd', 'LocationSupport'],
        help="List of process names to analyze with demangler (default: locationd LocationSupport)"
    )

    args = parser.parse_args()

    # Setup logging for standalone execution
    setup_default_logging()

    # Determine output directory
    if args.output is None:
        # Extract repo name from repo_path
        repo_name = os.path.basename(os.path.abspath(args.repo))
        args.output = f"/tmp/{repo_name}/processed_traces"

    # Create output directory if it doesn't exist
    os.makedirs(args.output, exist_ok=True)

    logger.info(f"Input file: {args.traces_json}")
    logger.info(f"Repository path: {args.repo}")
    logger.info(f"Output directory: {args.output}")
    logger.info(f"Nested call graph file: {args.nested_call_graph}")
    logger.info(f"Defined functions file: {args.defined_functions}")
    logger.info(f"Target processes: {args.target_processes}")

    enricher = StackTraceSourceEnricher(
        flat_call_stack_list_file=args.traces_json,
        nested_call_graph_file=args.nested_call_graph,
        output_dir=args.output,
        repo_path=args.repo,
        target_processes=args.target_processes,
        defined_functions_file=args.defined_functions
    )
    enricher.enrich_stack_traces()


if __name__ == "__main__":
    main()
