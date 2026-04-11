#!/usr/bin/env python3
"""
File counting script that:
1. Prints counts of files by their extension
2. Prints counts of files per directory in a tree format
3. Only shows directories with 5 or more files
"""

import os
import argparse
import sys
from collections import defaultdict, Counter
from pathlib import Path

# Add the parent directory to the path to import from hindsight
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from core.lang_util.all_supported_extensions import ALL_SUPPORTED_EXTENSIONS

# Convert list to set for faster lookup
SUPPORTED_EXTENSIONS = set(ALL_SUPPORTED_EXTENSIONS)


def count_files_by_extension(root_path, ignore_dirs=None):
    """Count files by their extension (only supported extensions)."""
    extension_counts = Counter()
    ignore_dirs = ignore_dirs or []

    for root, dirs, files in os.walk(root_path):
        # Remove ignored directories from dirs list to prevent os.walk from entering them
        dirs[:] = [d for d in dirs if d not in ignore_dirs]

        for file in files:
            # Skip hidden files and system files
            if file.startswith('.'):
                continue

            # Get file extension (including the dot)
            ext = Path(file).suffix.lower()

            # Only count files with supported extensions
            if ext in SUPPORTED_EXTENSIONS:
                extension_counts[ext] += 1

    return extension_counts


def count_files_per_directory(root_path, ignore_dirs=None):
    """Count files per directory and build a tree structure."""
    dir_file_counts = defaultdict(int)
    dir_structure = defaultdict(set)
    ignore_dirs = ignore_dirs or []

    # First pass: count direct files and build structure
    for root, dirs, files in os.walk(root_path):
        # Remove ignored directories from dirs list to prevent os.walk from entering them
        dirs[:] = [d for d in dirs if d not in ignore_dirs]

        # Count non-hidden files with supported extensions in this directory only
        file_count = 0
        for f in files:
            if not f.startswith('.'):
                ext = Path(f).suffix.lower()
                if ext in SUPPORTED_EXTENSIONS:
                    file_count += 1
        dir_file_counts[root] = file_count

        # Build parent-child relationships
        parent = os.path.dirname(root)
        if parent != root:  # Not the root directory
            dir_structure[parent].add(root)

    # Second pass: calculate recursive file counts
    def get_recursive_file_count(directory):
        """Get total file count including all subdirectories."""
        total = dir_file_counts[directory]  # Files in this directory

        # Add files from all subdirectories
        if directory in dir_structure:
            for subdir in dir_structure[directory]:
                total += get_recursive_file_count(subdir)

        return total

    # Calculate recursive counts for all directories
    recursive_counts = {}
    for directory in dir_file_counts:
        recursive_counts[directory] = get_recursive_file_count(directory)

    return recursive_counts, dir_structure


def print_directory_tree(root_path, dir_file_counts, dir_structure, current_dir, indent_level=0):
    """Print directory tree with file counts, filtering directories with < 5 files."""
    file_count = dir_file_counts[current_dir]

    # Only print directories with 5 or more files
    if file_count >= 5:
        # Create indentation
        indent = "  " * indent_level
        tree_char = "├── " if indent_level > 0 else ""

        # Get relative path for display
        rel_path = os.path.relpath(current_dir, root_path)
        if rel_path == '.':
            rel_path = os.path.basename(root_path)

        print(f"{indent}{tree_char}{rel_path} ({file_count} files)")

    # Recursively print subdirectories
    if current_dir in dir_structure:
        subdirs = sorted(dir_structure[current_dir])
        for subdir in subdirs:
            print_directory_tree(root_path, dir_file_counts, dir_structure, subdir, indent_level + 1)


def main():
    # Parse command line arguments
    parser = argparse.ArgumentParser(description='Count files by extension and display directory tree')
    parser.add_argument('-r', '--root',
                       required=True,
                       help='Root directory path to analyze (required)')
    parser.add_argument('-i', '--exclude-dirs',
                       nargs='*',
                       default=[],
                       help='List of directory names to exclude during analysis')

    args = parser.parse_args()
    root_path = os.path.abspath(args.root)

    # Validate that the path exists
    if not os.path.exists(root_path):
        print(f"Error: Directory '{root_path}' does not exist.")
        return 1

    if not os.path.isdir(root_path):
        print(f"Error: '{root_path}' is not a directory.")
        return 1

    print("=" * 60)
    print("FILE COUNT ANALYSIS")
    print("=" * 60)
    print(f"Root directory: {root_path}")
    print()

    # Count files by extension
    print("FILES BY EXTENSION:")
    print("-" * 30)
    extension_counts = count_files_by_extension(root_path, args.exclude_dirs)

    if extension_counts:
        # Sort by count (descending) then by extension name
        sorted_extensions = sorted(extension_counts.items(),
                                 key=lambda x: (-x[1], x[0]))

        for ext, count in sorted_extensions:
            print(f"{ext:20} : {count:4d} files")
    else:
        print("No files found.")

    print()

    # Count files per directory and show tree
    print("DIRECTORY TREE (directories with 5+ files only):")
    print("-" * 50)

    dir_file_counts, dir_structure = count_files_per_directory(root_path, args.exclude_dirs)

    # Filter and show only directories with 5+ files
    has_qualifying_dirs = any(count >= 5 for count in dir_file_counts.values())

    if has_qualifying_dirs:
        print_directory_tree(root_path, dir_file_counts, dir_structure, root_path)
    else:
        print("No directories with 5 or more files found.")

    print()
    print("=" * 60)
    return 0


if __name__ == "__main__":
    exit(main())