#!/usr/bin/env python3
"""
Script to compare checksums for functions in two merged_call_graph.json files
and print differences.

Usage:
    python3 compare_checksums.py file1.json file2.json
"""

import json
import sys
import argparse
from pathlib import Path

def load_json_file(file_path):
    """Load and parse a JSON file."""
    try:
        with open(file_path, 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        print(f"Error: File not found: {file_path}")
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"Error: Invalid JSON in {file_path}: {e}")
        sys.exit(1)

def extract_function_checksums(data):
    """Extract function checksums from the call graph data."""
    checksums = {}

    for file_entry in data.get('call_graph', []):
        file_name = file_entry.get('file', '')

        for function_entry in file_entry.get('functions', []):
            function_name = function_entry.get('function', '')
            checksum = function_entry.get('checksum', '')

            # Create a unique key combining file and function name
            key = f"{file_name}::{function_name}"
            checksums[key] = checksum

    return checksums

def compare_checksums(checksums1, checksums2, file1_name, file2_name):
    """Compare two sets of checksums and print differences."""
    all_keys = set(checksums1.keys()) | set(checksums2.keys())

    differences_found = False
    missing_in_file1 = []
    missing_in_file2 = []
    checksum_differences = []

    for key in sorted(all_keys):
        if key not in checksums1:
            missing_in_file1.append(key)
            differences_found = True
        elif key not in checksums2:
            missing_in_file2.append(key)
            differences_found = True
        elif checksums1[key] != checksums2[key]:
            checksum_differences.append({
                'function': key,
                'file1_checksum': checksums1[key],
                'file2_checksum': checksums2[key]
            })
            differences_found = True

    # Print results
    print("=" * 80)
    print("CHECKSUM COMPARISON RESULTS")
    print("=" * 80)
    print(f"File 1: {file1_name}")
    print(f"File 2: {file2_name}")
    print()

    if not differences_found:
        print("✅ NO DIFFERENCES FOUND")
        print("All functions have identical checksums in both files.")
    else:
        print("❌ DIFFERENCES FOUND")
        print()

        if missing_in_file1:
            print(f"Functions present in {file2_name} but missing in {file1_name}:")
            for func in missing_in_file1:
                print(f"  - {func}")
            print()

        if missing_in_file2:
            print(f"Functions present in {file1_name} but missing in {file2_name}:")
            for func in missing_in_file2:
                print(f"  - {func}")
            print()

        if checksum_differences:
            print("Functions with different checksums:")
            for diff in checksum_differences:
                print(f"  Function: {diff['function']}")
                print(f"    {file1_name}: {diff['file1_checksum']}")
                print(f"    {file2_name}: {diff['file2_checksum']}")
                print()

    print("=" * 80)
    print(f"Summary:")
    print(f"  Total functions in {file1_name}: {len(checksums1)}")
    print(f"  Total functions in {file2_name}: {len(checksums2)}")
    print(f"  Functions missing in {file1_name}: {len(missing_in_file1)}")
    print(f"  Functions missing in {file2_name}: {len(missing_in_file2)}")
    print(f"  Functions with different checksums: {len(checksum_differences)}")
    print("=" * 80)

def parse_arguments():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Compare checksums for functions in two merged_call_graph.json files",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 compare_checksums.py file1.json file2.json
  python3 compare_checksums.py /path/to/first.json ~/Desktop/second.json
        """
    )

    parser.add_argument(
        'file1',
        help='Path to the first JSON file'
    )

    parser.add_argument(
        'file2',
        help='Path to the second JSON file'
    )

    parser.add_argument(
        '-v', '--verbose',
        action='store_true',
        help='Enable verbose output'
    )

    return parser.parse_args()

def main():
    """Main function to compare checksums between two JSON files."""
    args = parse_arguments()

    # Expand user paths (e.g., ~/Desktop)
    file1_path = Path(args.file1).expanduser().resolve()
    file2_path = Path(args.file2).expanduser().resolve()

    if args.verbose:
        print(f"Comparing files:")
        print(f"  File 1: {file1_path}")
        print(f"  File 2: {file2_path}")
        print()

    print("Loading JSON files...")

    # Load both files
    data1 = load_json_file(file1_path)
    data2 = load_json_file(file2_path)

    print("Extracting function checksums...")

    # Extract checksums
    checksums1 = extract_function_checksums(data1)
    checksums2 = extract_function_checksums(data2)

    print("Comparing checksums...")
    print()

    # Compare and print results
    compare_checksums(checksums1, checksums2, str(file1_path), str(file2_path))

if __name__ == "__main__":
    main()