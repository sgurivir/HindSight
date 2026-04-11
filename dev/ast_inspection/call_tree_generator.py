#!/usr/bin/env python3
"""
call_tree_generator.py - Script to generate a call tree in JSON format from a call graph.

This script takes a nested call graph JSON file, breaks cycles to create a DAG,
and outputs a call tree in JSON format with implementation location metadata.

NOTE: The core implementation has been moved to hindsight/core/lang_util/call_tree_util.py
This script now uses that module for the actual call tree generation.

Usage:
    python call_tree_generator.py -f <path_to_merged_call_graph.json>
    python call_tree_generator.py -f <path_to_merged_call_graph.json> -o output.json
    
Example:
    python call_tree_generator.py -f ~/llm_artifacts/xnu/code_insights/merged_call_graph.json
"""

import argparse
import json
import sys
import os
from pathlib import Path

# Add the project root to the path to import from hindsight
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

# Import from the new location
from hindsight.core.lang_util.call_graph_util import CallGraph, load_call_graph_from_json
from hindsight.core.lang_util.call_tree_util import (
    CallTreeGenerator,
    extract_implementations,
    create_dag,
    get_dag_root_nodes,
    build_call_tree_node,
    generate_call_tree,
    format_location,
    write_tree_text_format
)


def main():
    """Main entry point for the script."""
    parser = argparse.ArgumentParser(
        description="Generate a call tree in JSON format from a call graph."
    )
    parser.add_argument(
        "-f",
        metavar="<path>",
        required=True,
        help="Path to merged call graph JSON file"
    )
    parser.add_argument(
        "-o",
        "--output",
        metavar="<path>",
        default="/tmp/calltree.json",
        required=False,
        help="Output file path (default: /tmp/calltree.json)"
    )
    parser.add_argument(
        "--max-depth",
        type=int,
        default=20,
        help="Maximum depth for cycle breaking (default: 20)"
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty print the JSON output"
    )
    parser.add_argument(
        "-t",
        "--text-output",
        metavar="<path>",
        default="/tmp/callstacks.txt",
        required=False,
        help="Text output file path (default: /tmp/callstacks.txt)"
    )
    parser.add_argument(
        "-l",
        "--show-location",
        action="store_true",
        help="Show implementation location details in text output (file:start-end)"
    )
    args = parser.parse_args()
    
    json_path = args.f
    
    # Check if file exists
    if not os.path.exists(json_path):
        print(f"Error: File not found: {json_path}", file=sys.stderr)
        sys.exit(1)
    
    # Use the CallTreeGenerator class from the new location
    generator = CallTreeGenerator(max_depth=args.max_depth)
    
    try:
        # Load and generate
        generator.load_from_json(json_path)
        call_tree = generator.generate_call_tree()
        
        # Write JSON output
        generator.write_json(args.output, pretty=args.pretty)
        print(f"Call tree JSON written to: {args.output}", file=sys.stderr)
        
        # Write text format output
        generator.write_text(args.text_output, show_location=args.show_location)
        print(f"Call tree text written to: {args.text_output}", file=sys.stderr)
        
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
