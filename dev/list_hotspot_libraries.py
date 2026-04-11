#!/usr/bin/env python3
"""
List Libraries from Hotspot File

Extracts and prints a de-duplicated list of all libraries (ownerName) 
from a hotspot JSON file.

Usage:
    python3 dev/list_hotspot_libraries.py <hotspot_file.json>
    python3 dev/list_hotspot_libraries.py ~/bugs/hotspots/routined/20260224_110606_routined_hotspot_data.json
"""

import json
import sys
import argparse
from pathlib import Path
from collections import deque
from typing import Set, Dict, Any


def extract_libraries_from_nested(data: Dict[str, Any]) -> Set[str]:
    """
    Extract all unique library names (ownerName) from nested callstack format.
    
    Args:
        data: The loaded hotspot JSON data with 'callstack' key
        
    Returns:
        Set of unique library names
    """
    libraries: Set[str] = set()
    
    callstack = data.get("callstack", {})
    if not callstack:
        return libraries
    
    # BFS/DFS traversal of the callstack tree
    stack = deque([callstack])
    
    while stack:
        node = stack.pop()
        
        # Extract ownerName (library name)
        owner_name = node.get("ownerName", "")
        if owner_name:
            libraries.add(owner_name)
        
        # Add children to stack
        children = node.get("children", [])
        stack.extend(children)
    
    return libraries


def extract_libraries_from_array(data: list) -> Set[str]:
    """
    Extract all unique library names (ownerName) from array format.
    
    Args:
        data: The loaded hotspot JSON data as array
        
    Returns:
        Set of unique library names
    """
    libraries: Set[str] = set()
    
    for callstack_group in data:
        if not isinstance(callstack_group, list):
            continue
        
        for entry in callstack_group:
            if not isinstance(entry, dict):
                continue
            
            owner_name = entry.get("ownerName", "")
            if owner_name:
                libraries.add(owner_name)
    
    return libraries


def list_libraries(input_file: str) -> Set[str]:
    """
    Extract and return all unique libraries from a hotspot JSON file.
    
    Args:
        input_file: Path to the hotspot JSON file
        
    Returns:
        Set of unique library names
    """
    # Load JSON data
    try:
        with open(input_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception as e:
        print(f"Error loading JSON file: {e}", file=sys.stderr)
        return set()
    
    # Detect format and extract libraries
    if isinstance(data, dict) and 'callstack' in data:
        process_name = data.get('processName', 'unknown')
        print(f"Process: {process_name}", file=sys.stderr)
        print(f"Format: nested callstack", file=sys.stderr)
        libraries = extract_libraries_from_nested(data)
    elif isinstance(data, list):
        print(f"Format: array with {len(data)} callstack groups", file=sys.stderr)
        libraries = extract_libraries_from_array(data)
    else:
        print(f"Error: Unsupported JSON format", file=sys.stderr)
        return set()
    
    return libraries


def main():
    parser = argparse.ArgumentParser(
        description="List all unique libraries from a hotspot JSON file",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    python3 dev/list_hotspot_libraries.py hotspot_data.json
    python3 dev/list_hotspot_libraries.py ~/bugs/hotspots/routined/20260224_110606_routined_hotspot_data.json
        """
    )
    
    parser.add_argument(
        'input_file',
        help='Path to hotspot JSON file'
    )
    
    parser.add_argument(
        '--count', '-c',
        action='store_true',
        help='Show count of libraries'
    )
    
    parser.add_argument(
        '--sort', '-s',
        action='store_true',
        default=True,
        help='Sort libraries alphabetically (default: True)'
    )
    
    args = parser.parse_args()
    
    # Validate input file exists
    if not Path(args.input_file).exists():
        print(f"Error: Input file does not exist: {args.input_file}", file=sys.stderr)
        return 1
    
    # Extract libraries
    libraries = list_libraries(args.input_file)
    
    if not libraries:
        print("No libraries found.", file=sys.stderr)
        return 1
    
    # Sort if requested
    if args.sort:
        sorted_libs = sorted(libraries)
    else:
        sorted_libs = list(libraries)
    
    # Print libraries
    print(f"\n{'=' * 60}", file=sys.stderr)
    print(f"LIBRARIES ({len(libraries)} unique)", file=sys.stderr)
    print(f"{'=' * 60}", file=sys.stderr)
    
    for lib in sorted_libs:
        print(lib)
    
    if args.count:
        print(f"\nTotal: {len(libraries)} libraries", file=sys.stderr)
    
    return 0


if __name__ == "__main__":
    sys.exit(main())