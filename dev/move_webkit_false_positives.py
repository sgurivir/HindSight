#!/usr/bin/env python3
"""
Script to move false positive analysis files from code_analysis to dropped_issues
based on the progress.txt file.

Only moves files for vendored libraries where ALL issues are false positives:
- fast_float.h (vendored fast_float library)
- neon.h (vendored SIMDE library)
- simd128.h (vendored SIMDE library)
"""

import os
import re
import json
import shutil
from pathlib import Path
from typing import List, Dict, Tuple, Set

# Paths
PROGRESS_FILE = "/Users/sgurivireddy/third_party/WebKit/progress.txt"
CODE_ANALYSIS_DIR = "/Users/sgurivireddy/llm_artifacts/WebKit/results/code_analysis"
DROPPED_ISSUES_DIR = "/Users/sgurivireddy/llm_artifacts/WebKit/dropped_issues"

# Vendored libraries where ALL issues are false positives
VENDORED_LIBRARIES = {
    'fast_float.h': 'Source/WTF/wtf/fast_float/fast_float.h',
    'neon.h': 'Source/WTF/wtf/simde/arm/neon.h',
    'simd128.h': 'Source/WTF/wtf/simde/wasm/simd128.h',
}


def parse_progress_file(progress_file: str) -> Dict[str, List[Dict]]:
    """Parse the progress.txt file and extract false positives grouped by filename."""
    false_positives_by_file = {}
    valid_issues_by_file = {}
    
    with open(progress_file, 'r') as f:
        for line in f:
            line = line.strip()
            if line.startswith('[ANALYZED:FP]') or line.startswith('[ANALYZED:VALID]'):
                is_fp = line.startswith('[ANALYZED:FP]')
                # Parse the line format:
                # [ANALYZED:FP] Issue Title | File | Line | Verdict | Worth Fixing
                parts = line.split('|')
                if len(parts) >= 2:
                    # Extract file path from the second part
                    file_path = parts[1].strip()
                    # Extract just the filename
                    filename = os.path.basename(file_path)
                    # Extract line numbers if present
                    line_info = parts[2].strip() if len(parts) > 2 else ""
                    
                    entry = {
                        'full_line': line,
                        'file_path': file_path,
                        'filename': filename,
                        'line_info': line_info
                    }
                    
                    if is_fp:
                        if filename not in false_positives_by_file:
                            false_positives_by_file[filename] = []
                        false_positives_by_file[filename].append(entry)
                    else:
                        if filename not in valid_issues_by_file:
                            valid_issues_by_file[filename] = []
                        valid_issues_by_file[filename].append(entry)
    
    return false_positives_by_file, valid_issues_by_file


def find_matching_analysis_files(filename: str, code_analysis_dir: str) -> List[str]:
    """Find analysis files that match the given source filename."""
    matching_files = []
    
    # Remove extension for matching
    base_name = os.path.splitext(filename)[0]
    
    for analysis_file in os.listdir(code_analysis_dir):
        if analysis_file.endswith('_analysis.json'):
            # Check if the source filename appears in the analysis filename
            # Analysis files are named like: function_name_source_file_checksum_analysis.json
            if filename in analysis_file or base_name in analysis_file:
                matching_files.append(analysis_file)
    
    return matching_files


def verify_file_match(analysis_file_path: str, source_file_path: str) -> bool:
    """Verify that the analysis file corresponds to the source file."""
    try:
        with open(analysis_file_path, 'r') as f:
            data = json.load(f)
            # Check if the file_path in the analysis matches
            if 'file_path' in data:
                return source_file_path in data['file_path'] or data['file_path'].endswith(source_file_path)
    except (json.JSONDecodeError, IOError):
        pass
    return False


def move_files(files_to_move: List[str], source_dir: str, dest_dir: str, dry_run: bool = False) -> Tuple[int, int]:
    """Move files from source to destination directory."""
    # Create destination directory if it doesn't exist
    if not dry_run:
        os.makedirs(dest_dir, exist_ok=True)
    
    moved = 0
    failed = 0
    
    for filename in files_to_move:
        source_path = os.path.join(source_dir, filename)
        dest_path = os.path.join(dest_dir, filename)
        
        if os.path.exists(source_path):
            if dry_run:
                print(f"  [DRY RUN] Would move: {filename}")
                moved += 1
            else:
                try:
                    shutil.move(source_path, dest_path)
                    print(f"  [MOVED] {filename}")
                    moved += 1
                except Exception as e:
                    print(f"  [ERROR] Failed to move {filename}: {e}")
                    failed += 1
        else:
            print(f"  [NOT FOUND] {filename}")
            failed += 1
    
    return moved, failed


def main():
    import argparse
    parser = argparse.ArgumentParser(description='Move false positive analysis files for vendored libraries')
    parser.add_argument('--dry-run', action='store_true', help='Show what would be moved without actually moving')
    parser.add_argument('--verbose', '-v', action='store_true', help='Verbose output')
    args = parser.parse_args()
    
    print("=" * 80)
    print("WebKit False Positive Mover (Vendored Libraries Only)")
    print("=" * 80)
    print(f"\nProgress file: {PROGRESS_FILE}")
    print(f"Source directory: {CODE_ANALYSIS_DIR}")
    print(f"Destination directory: {DROPPED_ISSUES_DIR}")
    print(f"Dry run: {args.dry_run}")
    print()
    
    print("Vendored libraries to process:")
    for lib_name, lib_path in VENDORED_LIBRARIES.items():
        print(f"  - {lib_name}: {lib_path}")
    print()
    
    # Parse progress file
    print("Parsing progress file...")
    false_positives_by_file, valid_issues_by_file = parse_progress_file(PROGRESS_FILE)
    
    total_fps = sum(len(fps) for fps in false_positives_by_file.values())
    print(f"Found {total_fps} false positives across {len(false_positives_by_file)} files\n")
    
    # Find matching analysis files for vendored libraries only
    all_files_to_move = set()
    
    for lib_name, lib_path in VENDORED_LIBRARIES.items():
        print(f"\n--- Processing vendored library: {lib_name} ---")
        
        # Check if this library has any valid issues (it shouldn't for vendored libs)
        if lib_name in valid_issues_by_file:
            print(f"  WARNING: {lib_name} has {len(valid_issues_by_file[lib_name])} VALID issues - skipping")
            continue
        
        # Get FP count for this library
        fp_count = len(false_positives_by_file.get(lib_name, []))
        print(f"  False positives in progress.txt: {fp_count}")
        
        if args.verbose and lib_name in false_positives_by_file:
            for fp in false_positives_by_file[lib_name]:
                print(f"    FP: {fp['file_path']} @ {fp['line_info']}")
        
        # Find matching analysis files
        matching_files = find_matching_analysis_files(lib_name, CODE_ANALYSIS_DIR)
        
        if matching_files:
            print(f"  Found {len(matching_files)} matching analysis file(s)")
            verified_count = 0
            for mf in matching_files:
                # Verify the match
                analysis_path = os.path.join(CODE_ANALYSIS_DIR, mf)
                
                if verify_file_match(analysis_path, lib_path):
                    all_files_to_move.add(mf)
                    verified_count += 1
                    if args.verbose:
                        print(f"    ✓ {mf}")
                else:
                    if args.verbose:
                        print(f"    ✗ {mf} (file path mismatch)")
            print(f"  Verified matches: {verified_count}")
        else:
            print(f"  No matching analysis files found")
    
    print("\n" + "=" * 80)
    print(f"Total files to move: {len(all_files_to_move)}")
    print("=" * 80)
    
    if all_files_to_move:
        print("\nMoving files...")
        moved, failed = move_files(sorted(all_files_to_move), CODE_ANALYSIS_DIR, DROPPED_ISSUES_DIR, args.dry_run)
        print(f"\nSummary: {moved} moved, {failed} failed")
    else:
        print("\nNo files to move.")


if __name__ == '__main__':
    main()
