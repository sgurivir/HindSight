#!/usr/bin/env python3
"""
Script to detect all preprocessor macros used in a repository.

Usage:
    python dev/detect_macros.py --repo <repo_path> --out_path <output_file>

This script scans source files in the repository and detects all preprocessor
macros used in #if, #ifdef, #ifndef directives.
"""

import argparse
import sys
from pathlib import Path

# Add project root to Python path for imports
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from hindsight.core.lang_util.cast_util import detect_preprocessor_macros, SUPPORTED_EXTENSIONS
from hindsight.utils.file_filter_util import find_files_with_extensions


def main():
    parser = argparse.ArgumentParser(
        description="Detect all preprocessor macros used in a repository"
    )
    parser.add_argument(
        "--repo",
        type=str,
        required=True,
        help="Path to the repository root"
    )
    parser.add_argument(
        "--out_path",
        type=str,
        required=True,
        help="Path to the output file where macros will be written (one per line)"
    )
    parser.add_argument(
        "--exclude",
        nargs="*",
        default=[],
        help="Directories to exclude from scanning"
    )
    
    args = parser.parse_args()
    
    repo_root = Path(args.repo).resolve()
    
    if not repo_root.exists():
        print(f"Error: Repository path does not exist: {repo_root}", file=sys.stderr)
        sys.exit(1)
    
    if not repo_root.is_dir():
        print(f"Error: Repository path is not a directory: {repo_root}", file=sys.stderr)
        sys.exit(1)
    
    # Find all source files in the repository
    exclude_set = set(args.exclude)
    source_files = find_files_with_extensions(repo_root, exclude_set, set(SUPPORTED_EXTENSIONS))
    
    if not source_files:
        print(f"Warning: No source files found in {repo_root}", file=sys.stderr)
        # Write empty file
        with open(args.out_path, 'w', encoding='utf-8') as f:
            pass
        sys.exit(0)
    
    # Detect preprocessor macros using the existing function
    macros = detect_preprocessor_macros(source_files)
    
    # Write macros to output file, one per line
    out_path = Path(args.out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    
    with open(out_path, 'w', encoding='utf-8') as f:
        for macro in sorted(macros):
            f.write(f"{macro}\n")
    
    print(f"Detected {len(macros)} macros, written to {args.out_path}")


if __name__ == "__main__":
    main()
