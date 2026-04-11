#!/usr/bin/env python3
"""
Diff Enhancement Utility

This module provides functionality to expand git diffs to include complete function contexts
using AST information from merged_functions.json.

Usage:
    python diff_enhancement_util.py --repo /path/to/repo --diff /path/to/diff.txt --merged-functions /path/to/merged_functions.json --output /path/to/expanded_diff.txt
"""

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Tuple, Optional, Any

# Add project root to Python path for imports
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from hindsight.utils.file_content_provider import FileContentProvider
from hindsight.core.lang_util.all_supported_extensions import ALL_SUPPORTED_EXTENSIONS


def filter_diff_by_supported_extensions(diff_content: str) -> str:
    """
    Filter diff content to only include files with supported extensions.
    
    Args:
        diff_content: The raw diff content
        
    Returns:
        Filtered diff content containing only files with supported extensions
    """
    if not diff_content.strip():
        return diff_content
        
    lines = diff_content.split('\n')
    filtered_lines = []
    current_file_block = []
    current_file_path = None
    include_current_block = False
    
    for line in lines:
        if line.startswith('diff --git'):
            # Process previous block if it should be included
            if include_current_block and current_file_block:
                filtered_lines.extend(current_file_block)
            
            # Start new block
            current_file_block = [line]
            # Extract file path from diff --git a/path b/path
            parts = line.split()
            if len(parts) >= 4:
                # Get the file path (remove a/ or b/ prefix)
                file_path = parts[3]  # Use b/path (new file path)
                if file_path.startswith('b/'):
                    file_path = file_path[2:]
                current_file_path = file_path
                
                # Check if this file has a supported extension
                _, ext = os.path.splitext(current_file_path)
                include_current_block = ext.lower() in ALL_SUPPORTED_EXTENSIONS
            else:
                include_current_block = False
                current_file_path = None
        else:
            current_file_block.append(line)
    
    # Process the last block
    if include_current_block and current_file_block:
        filtered_lines.extend(current_file_block)
    
    return '\n'.join(filtered_lines)


class DiffContextExpander:
    """
    Utility class for expanding git diffs to include complete function contexts
    using AST information from merged_functions.json.
    """
    
    def __init__(self):
        self.file_functions_cache: Dict[str, List[Dict[str, Any]]] = {}
    
    @staticmethod
    def generate_diff(repo_path: str, c1: str, c2: str, output_file: str) -> bool:
        """
        Generate a git diff between two commits and save to file, filtered by supported extensions.
        
        Args:
            repo_path: Path to the repository
            c1: First commit (older)
            c2: Second commit (newer)
            output_file: Path to save the diff
            
        Returns:
            True if successful, False otherwise
        """
        try:
            # Change to repo directory and run git diff
            cmd = ['git', 'diff', c1, c2]
            result = subprocess.run(
                cmd,
                cwd=repo_path,
                capture_output=True,
                text=True,
                check=True
            )
            
            # Filter diff content to only include files with supported extensions
            raw_diff = result.stdout
            filtered_diff = filter_diff_by_supported_extensions(raw_diff)
            
            # Write filtered diff to file
            Path(output_file).parent.mkdir(parents=True, exist_ok=True)
            with open(output_file, 'w', encoding='utf-8') as f:
                f.write(filtered_diff)
            
            print(f"Generated filtered diff from {c1} to {c2}: {output_file}")
            print(f"Filtered to include only files with supported extensions: {ALL_SUPPORTED_EXTENSIONS}")
            return True
            
        except subprocess.CalledProcessError as e:
            print(f"Error generating diff: {e}", file=sys.stderr)
            print(f"Git error output: {e.stderr}", file=sys.stderr)
            return False
        except Exception as e:
            print(f"Error generating diff: {e}", file=sys.stderr)
            return False
    
    def build_file_functions_dictionary(self, merged_functions_path: str, files_to_process: List[str]) -> Dict[str, List[Dict[str, Any]]]:
        """
        Build a dictionary mapping file names to their functions with line numbers.
        
        Args:
            merged_functions_path: Path to merged_functions.json file
            files_to_process: List of file paths to process (limits processing to these files)
            
        Returns:
            Dictionary mapping file_name -> list of function info dicts
            Each function info dict contains: {name, start_line, end_line}
        """
        try:
            with open(merged_functions_path, 'r', encoding='utf-8') as f:
                merged_data = json.load(f)
            
            # Handle the new schema format
            if 'function_to_location' in merged_data:
                function_locations = merged_data['function_to_location']
            else:
                # Fallback to old format
                function_locations = merged_data
            
            file_functions_dict = {}
            
            # Convert files_to_process to a set for faster lookup
            files_set = set(files_to_process) if files_to_process else None
            
            for func_name, locations in function_locations.items():
                
                # Ensure locations is a list
                if isinstance(locations, dict):
                    locations = [locations]
                elif not isinstance(locations, list):
                    continue
                
                for location in locations:
                    if not isinstance(location, dict):
                        continue
                    
                    # Handle the new schema where file info is in 'code' array
                    code_locations = location.get('code', [location])  # Fallback to location itself for old schema
                    
                    for code_location in code_locations:
                        if not isinstance(code_location, dict):
                            continue
                            
                        file_name = code_location.get('file_name')
                        start_line = code_location.get('start')
                        end_line = code_location.get('end')
                        
                        if not all([file_name, start_line is not None, end_line is not None]):
                            continue
                        
                        # Filter by files_to_process if provided
                        if files_set and file_name not in files_set:
                            continue
                        
                        if file_name not in file_functions_dict:
                            file_functions_dict[file_name] = []
                        
                        file_functions_dict[file_name].append({
                            'name': func_name,
                            'start_line': start_line,
                            'end_line': end_line
                        })
            
            # Sort functions by start line for each file
            for file_name in file_functions_dict:
                file_functions_dict[file_name].sort(key=lambda x: x['start_line'])
            
            self.file_functions_cache = file_functions_dict
            return file_functions_dict
            
        except Exception as e:
            print(f"Error building file functions dictionary: {e}", file=sys.stderr)
            return {}
    
    @staticmethod
    def expand_diff_with_function_context(
        repo_path: str,
        file_content_provider: Optional[Any],
        diff_file_path: str,
        merged_functions_path: str,
        output_file_path: str
    ) -> bool:
        """
        Static method to expand git diff with function context.
        
        Args:
            repo_path: Path to the repository
            file_content_provider: FileContentProvider instance (can be None)
            diff_file_path: Path to the diff file generated by generate_diff()
            merged_functions_path: Path to merged_functions.json from ast_util
            output_file_path: Path to output file for expanded diff
            
        Returns:
            True if successful, False otherwise
        """
        try:
            expander = DiffContextExpander()
            
            # Read the original diff
            with open(diff_file_path, 'r', encoding='utf-8') as f:
                diff_content = f.read()
            
            # Parse diff to extract changed files
            changed_files = expander._extract_changed_files_from_diff(diff_content)
            
            # Build file functions dictionary for changed files only
            file_functions_dict = expander.build_file_functions_dictionary(
                merged_functions_path, changed_files
            )
            
            # Expand the diff
            expanded_diff = expander._expand_diff_content(
                diff_content, file_functions_dict, repo_path, file_content_provider
            )
            
            # Write expanded diff to output file
            Path(output_file_path).parent.mkdir(parents=True, exist_ok=True)
            with open(output_file_path, 'w', encoding='utf-8') as f:
                f.write(expanded_diff)
            
            print(f"Expanded diff written to: {output_file_path}")
            return True
            
        except Exception as e:
            print(f"Error expanding diff: {e}", file=sys.stderr)
            return False
    
    def _extract_changed_files_from_diff(self, diff_content: str) -> List[str]:
        """
        Extract list of changed files from git diff content.
        
        Args:
            diff_content: Git diff content
            
        Returns:
            List of file paths that were changed
        """
        changed_files = []
        lines = diff_content.split('\n')
        
        for line in lines:
            if line.startswith('diff --git'):
                # Extract file path from diff header
                # Format: diff --git a/path/to/file b/path/to/file
                parts = line.split()
                if len(parts) >= 4:
                    file_path = parts[3][2:]  # Remove 'b/' prefix
                    changed_files.append(file_path)
            elif line.startswith('+++'):
                # Alternative way to get file path
                file_path = line[4:].strip()  # Remove '+++ ' prefix
                if file_path.startswith('b/'):
                    file_path = file_path[2:]  # Remove 'b/' prefix
                if file_path != '/dev/null' and file_path not in changed_files:
                    changed_files.append(file_path)
        
        return changed_files
    
    def _expand_diff_content(
        self, 
        diff_content: str, 
        file_functions_dict: Dict[str, List[Dict[str, Any]]], 
        repo_path: str,
        file_content_provider: Optional[Any]
    ) -> str:
        """
        Expand diff content to include complete function contexts.
        
        Args:
            diff_content: Original diff content
            file_functions_dict: Dictionary mapping file names to function info
            repo_path: Path to repository
            file_content_provider: FileContentProvider instance (optional)
            
        Returns:
            Expanded diff content
        """
        lines = diff_content.split('\n')
        expanded_lines = []
        current_file = None
        
        i = 0
        while i < len(lines):
            line = lines[i]
            
            # Track current file being processed
            if line.startswith('diff --git'):
                parts = line.split()
                if len(parts) >= 4:
                    current_file = parts[3][2:]  # Remove 'b/' prefix
                expanded_lines.append(line)
            elif line.startswith('+++'):
                file_path = line[4:].strip()  # Remove '+++ ' prefix
                if file_path.startswith('b/'):
                    file_path = file_path[2:]  # Remove 'b/' prefix
                current_file = file_path
                expanded_lines.append(line)
            elif line.startswith('@@') and current_file:
                # This is a hunk header - try to expand it
                expanded_hunk, lines_consumed = self._expand_hunk_with_function_context(
                    lines[i:], current_file, file_functions_dict, repo_path, file_content_provider
                )
                expanded_lines.extend(expanded_hunk)
                i += lines_consumed - 1  # -1 because we'll increment i at the end of the loop
            else:
                expanded_lines.append(line)
            
            i += 1
        
        return '\n'.join(expanded_lines)
    
    def _expand_hunk_with_function_context(
        self, 
        hunk_lines: List[str], 
        file_path: str, 
        file_functions_dict: Dict[str, List[Dict[str, Any]]],
        repo_path: str,
        file_content_provider: Optional[Any]
    ) -> Tuple[List[str], int]:
        """
        Expand a hunk to include complete function context.
        
        Args:
            hunk_lines: Lines starting with the hunk header
            file_path: Path to the file being modified
            file_functions_dict: Dictionary of file functions
            repo_path: Repository path
            file_content_provider: FileContentProvider instance (optional)
            
        Returns:
            Tuple of (expanded_hunk_lines, number_of_lines_consumed)
        """
        if not hunk_lines or not hunk_lines[0].startswith('@@'):
            return hunk_lines[:1] if hunk_lines else [], 1
        
        hunk_header = hunk_lines[0]
        
        # Parse hunk header to get line numbers
        match = re.match(r'@@ -(\d+),?(\d+)? \+(\d+),?(\d+)? @@', hunk_header)
        if not match:
            return hunk_lines[:1], 1
        
        old_start = int(match.group(1))
        old_count = int(match.group(2)) if match.group(2) else 1
        new_start = int(match.group(3))
        new_count = int(match.group(4)) if match.group(4) else 1
        
        # Find the end of this hunk
        hunk_end = 1
        for i in range(1, len(hunk_lines)):
            if hunk_lines[i].startswith('@@') or hunk_lines[i].startswith('diff --git'):
                break
            hunk_end = i + 1
        
        # Get functions for this file
        functions = file_functions_dict.get(file_path, [])
        if not functions:
            # No function info available, return original hunk
            return hunk_lines[:hunk_end], hunk_end
        
        # Find functions that overlap with the changed lines
        changed_start = old_start
        changed_end = old_start + old_count - 1
        
        overlapping_functions = []
        for func in functions:
            func_start = func['start_line']
            func_end = func['end_line']
            
            # Check if function overlaps with changed lines
            if (func_start <= changed_end and func_end >= changed_start):
                overlapping_functions.append(func)
        
        if not overlapping_functions:
            # No overlapping functions, return original hunk
            return hunk_lines[:hunk_end], hunk_end
        
        # Find the earliest start and latest end of overlapping functions
        earliest_start = min(func['start_line'] for func in overlapping_functions)
        latest_end = max(func['end_line'] for func in overlapping_functions)
        
        # If the function boundaries don't expand the context, return original
        # Only skip expansion if function boundaries don't extend beyond existing hunk boundaries
        old_end = old_start + old_count - 1
        if earliest_start >= old_start and latest_end <= old_end:
            return hunk_lines[:hunk_end], hunk_end
        
        # Try to get the file content to expand the context
        file_content = self._get_file_content(file_path, repo_path, file_content_provider)
        if not file_content:
            return hunk_lines[:hunk_end], hunk_end
        
        file_lines = file_content.split('\n')
        
        # Calculate new hunk boundaries
        new_old_start = min(earliest_start, old_start)
        new_old_end = max(latest_end, old_start + old_count - 1)
        new_old_count = new_old_end - new_old_start + 1
        
        # Adjust new file line numbers accordingly
        start_diff = old_start - new_old_start
        new_new_start = new_start - start_diff
        new_new_count = new_count + (new_old_count - old_count)
        
        # Extract function name from original header if present
        original_header = hunk_lines[0]
        function_name = ""
        if "@@" in original_header:
            parts = original_header.split("@@")
            if len(parts) >= 3 and parts[2].strip():
                function_name = " @@" + parts[2]  # Keep the function name part with proper spacing
        
        # Create expanded hunk header with preserved function name
        expanded_header = f"@@ -{new_old_start},{new_old_count} +{new_new_start},{new_new_count}{function_name}"
        
        # Build expanded hunk content
        expanded_hunk = [expanded_header]
        
        # Add context lines before the original change
        for line_num in range(new_old_start, old_start):
            if line_num <= len(file_lines):
                expanded_hunk.append(f" {file_lines[line_num - 1]}")
        
        # Add the original hunk content (excluding the header)
        for line in hunk_lines[1:hunk_end]:
            if not line.startswith('@@'):
                expanded_hunk.append(line)
        
        # Add context lines after the original change
        original_end = old_start + old_count - 1
        for line_num in range(original_end + 1, new_old_end + 1):
            if line_num <= len(file_lines):
                expanded_hunk.append(f" {file_lines[line_num - 1]}")
        
        return expanded_hunk, hunk_end
    
    def _get_file_content(self, file_path: str, repo_path: str, file_content_provider: Optional[Any]) -> Optional[str]:
        """
        Get file content using FileContentProvider or direct file reading.
        
        Args:
            file_path: Relative path to the file
            repo_path: Repository root path
            file_content_provider: FileContentProvider instance (optional)
            
        Returns:
            File content as string, or None if not found
        """
        try:
            # Try using FileContentProvider if available
            if file_content_provider and hasattr(file_content_provider, 'read_text'):
                content = file_content_provider.read_text(file_path)
                if content:
                    return content
            
            # Fallback to direct file reading
            full_path = Path(repo_path) / file_path
            if full_path.exists():
                return full_path.read_text(encoding='utf-8', errors='ignore')
            
            return None
            
        except Exception as e:
            print(f"Error reading file {file_path}: {e}", file=sys.stderr)
            return None


def main():
    """Main entry point for the diff enhancement utility."""
    parser = argparse.ArgumentParser(
        description="Expand git diffs to include complete function contexts using AST information",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --repo /path/to/repo --diff diff.txt --merged-functions merged_functions.json --output expanded_diff.txt
  %(prog)s --repo /path/to/repo --c1 abc123 --c2 def456 --merged-functions merged_functions.json --output expanded_diff.txt
  %(prog)s --repo /path/to/repo --diff diff.txt --merged-functions merged_functions.json --output expanded_diff.txt --files file1.py file2.py
        """
    )
    
    parser.add_argument(
        "--repo",
        required=True,
        help="Path to the repository root directory"
    )
    
    # Make diff optional when using c1/c2
    parser.add_argument(
        "--diff",
        help="Path to the git diff file to expand (optional if using --c1 and --c2)"
    )
    
    parser.add_argument(
        "--c1",
        help="First commit hash/reference (older commit)"
    )
    
    parser.add_argument(
        "--c2",
        help="Second commit hash/reference (newer commit)"
    )
    
    parser.add_argument(
        "--merged-functions",
        required=True,
        help="Path to merged_functions.json file generated by ast_util"
    )
    
    parser.add_argument(
        "--output",
        required=True,
        help="Path to output file for the expanded diff"
    )
    
    parser.add_argument(
        "--files",
        nargs="*",
        help="Optional list of specific files to process (limits processing to these files)"
    )
    
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose output"
    )
    
    args = parser.parse_args()
    
    try:
        # Validate that either diff file or c1/c2 are provided
        if not args.diff and not (args.c1 and args.c2):
            print("Error: Either --diff file or both --c1 and --c2 must be provided", file=sys.stderr)
            sys.exit(1)
        
        if args.diff and (args.c1 or args.c2):
            print("Error: Cannot use both --diff and --c1/--c2 options together", file=sys.stderr)
            sys.exit(1)
        
        # Generate diff if c1/c2 provided
        diff_file_path = args.diff
        if args.c1 and args.c2:
            # Generate diff file in /tmp/diff.txt
            diff_file_path = "/tmp/diff.txt"
            if not DiffContextExpander.generate_diff(args.repo, args.c1, args.c2, diff_file_path):
                sys.exit(1)
        
        # Validate diff file exists
        if not Path(diff_file_path).exists():
            print(f"Error: Diff file not found: {diff_file_path}", file=sys.stderr)
            sys.exit(1)
        
        if not Path(args.merged_functions).exists():
            print(f"Error: Merged functions file not found: {args.merged_functions}", file=sys.stderr)
            sys.exit(1)
        
        if not Path(args.repo).exists():
            print(f"Error: Repository path not found: {args.repo}", file=sys.stderr)
            sys.exit(1)
        
        if args.verbose:
            print(f"Repository: {args.repo}")
            if args.c1 and args.c2:
                print(f"Commits: {args.c1} -> {args.c2}")
            print(f"Diff file: {diff_file_path}")
            print(f"Merged functions: {args.merged_functions}")
            print(f"Output file: {args.output}")
            if args.files:
                print(f"Processing files: {args.files}")
        
        # Initialize FileContentProvider
        file_content_provider = None
        try:
            file_content_provider = FileContentProvider.from_repo(args.repo)
            if args.verbose:
                print("FileContentProvider initialized successfully")
        except Exception as e:
            if args.verbose:
                print(f"Warning: Could not initialize FileContentProvider: {e}")
        
        # Expand the diff
        success = DiffContextExpander.expand_diff_with_function_context(
            repo_path=args.repo,
            file_content_provider=file_content_provider,
            diff_file_path=diff_file_path,
            merged_functions_path=args.merged_functions,
            output_file_path=args.output
        )
        
        # Keep the original diff file in /tmp/diff.txt for reference
        if args.c1 and args.c2 and args.verbose:
            print(f"Original diff saved to: {diff_file_path}")
        
        if success:
            print("✅ Diff expansion completed successfully!")
            if args.verbose:
                print(f"📊 Expanded diff saved to: {args.output}")
        else:
            print("❌ Diff expansion failed!")
            sys.exit(1)
    
    except KeyboardInterrupt:
        print("\n❌ Operation cancelled by user")
        sys.exit(1)
    except Exception as e:
        print(f"❌ Error: {e}", file=sys.stderr)
        if args.verbose:
            import traceback
            traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()