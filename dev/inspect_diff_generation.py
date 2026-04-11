#!/usr/bin/env python3
"""
Simple Diff Inspector

This script generates both original and expanded diffs for inspection,
without requiring the full Hindsight configuration.
"""

import os
import sys
import json
import argparse
import subprocess
import tempfile
import re
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Dict, Any, Tuple

# Add the project root to Python path for imports
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from hindsight.core.lang_util.all_supported_extensions import ALL_SUPPORTED_EXTENSIONS


def filter_diff_by_supported_extensions(diff_content: str) -> str:
    """Filter diff content to only include files with supported extensions."""
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


def generate_original_diff(repo_path: str, c1: str, c2: str) -> str:
    """Generate original git diff between two commits."""
    try:
        # Generate unified diff with context
        result = subprocess.run(
            ['git', 'diff', '--unified=3', c1, c2],
            cwd=repo_path,
            capture_output=True,
            text=True,
            check=True
        )
        
        diff_content = result.stdout
        
        # Filter to only include supported file extensions
        filtered_diff = filter_diff_by_supported_extensions(diff_content)
        
        return filtered_diff
        
    except subprocess.CalledProcessError as e:
        print(f"Error generating diff: {e}", file=sys.stderr)
        return ""


def extract_changed_files_from_diff(diff_content: str) -> List[str]:
    """Extract list of changed files from git diff content."""
    changed_files = []
    lines = diff_content.split('\n')
    
    for line in lines:
        if line.startswith('diff --git'):
            parts = line.split()
            if len(parts) >= 4:
                file_path = parts[3][2:]  # Remove 'b/' prefix
                changed_files.append(file_path)
    
    return changed_files


def expand_diff_with_simple_context(diff_content: str, repo_path: str, context_lines: int = 10) -> Tuple[str, List[Dict]]:
    """
    Expand diff with additional context lines around changes.
    This is a simplified version that doesn't require AST analysis.
    """
    lines = diff_content.split('\n')
    expanded_lines = []
    expansion_details = []
    
    i = 0
    while i < len(lines):
        line = lines[i]
        
        if line.startswith('@@'):
            # Parse hunk header
            match = re.match(r'@@ -(\d+),?(\d+)? \+(\d+),?(\d+)? @@', line)
            if match:
                old_start = int(match.group(1))
                old_count = int(match.group(2)) if match.group(2) else 1
                new_start = int(match.group(3))
                new_count = int(match.group(4)) if match.group(4) else 1
                
                # Find current file
                current_file = None
                for j in range(i-1, -1, -1):
                    if lines[j].startswith('+++'):
                        file_path = lines[j][4:].strip()
                        if file_path.startswith('b/'):
                            file_path = file_path[2:]
                        current_file = file_path
                        break
                
                if current_file:
                    # Try to expand with additional context
                    expanded_hunk, expansion_info = expand_hunk_with_context(
                        lines[i:], current_file, repo_path, context_lines
                    )
                    
                    if expansion_info:
                        expansion_details.append(expansion_info)
                    
                    # Find end of hunk
                    hunk_end = 1
                    for j in range(i+1, len(lines)):
                        if lines[j].startswith('@@') or lines[j].startswith('diff --git'):
                            break
                        hunk_end = j - i + 1
                    
                    expanded_lines.extend(expanded_hunk)
                    i += hunk_end - 1
                else:
                    expanded_lines.append(line)
            else:
                expanded_lines.append(line)
        else:
            expanded_lines.append(line)
        
        i += 1
    
    return '\n'.join(expanded_lines), expansion_details


def expand_hunk_with_context(hunk_lines: List[str], file_path: str, repo_path: str, context_lines: int) -> Tuple[List[str], Optional[Dict]]:
    """Expand a hunk with additional context lines."""
    if not hunk_lines or not hunk_lines[0].startswith('@@'):
        return hunk_lines[:1] if hunk_lines else [], None
    
    hunk_header = hunk_lines[0]
    
    # Parse hunk header
    match = re.match(r'@@ -(\d+),?(\d+)? \+(\d+),?(\d+)? @@', hunk_header)
    if not match:
        return hunk_lines[:1], None
    
    old_start = int(match.group(1))
    old_count = int(match.group(2)) if match.group(2) else 1
    new_start = int(match.group(3))
    new_count = int(match.group(4)) if match.group(4) else 1
    
    # Try to read the file content
    try:
        full_path = Path(repo_path) / file_path
        if not full_path.exists():
            return hunk_lines, None
        
        with open(full_path, 'r', encoding='utf-8', errors='ignore') as f:
            file_lines = f.readlines()
    except:
        return hunk_lines, None
    
    # Calculate expanded boundaries
    new_old_start = max(1, old_start - context_lines)
    new_old_end = min(len(file_lines), old_start + old_count - 1 + context_lines)
    new_old_count = new_old_end - new_old_start + 1
    
    # Check if expansion actually occurs
    if new_old_start >= old_start and new_old_end <= old_start + old_count - 1:
        return hunk_lines, None
    
    # Adjust new file line numbers
    start_diff = old_start - new_old_start
    new_new_start = new_start - start_diff
    new_new_count = new_count + (new_old_count - old_count)
    
    # Create expanded hunk header
    expanded_header = f"@@ -{new_old_start},{new_old_count} +{new_new_start},{new_new_count} @@"
    
    # Build expanded hunk content
    expanded_hunk = [expanded_header]
    
    # Add context lines before
    for line_num in range(new_old_start, old_start):
        if line_num <= len(file_lines):
            expanded_hunk.append(f" {file_lines[line_num - 1].rstrip()}")
    
    # Add original hunk content (excluding header)
    hunk_end = 1
    for i in range(1, len(hunk_lines)):
        if hunk_lines[i].startswith('@@') or hunk_lines[i].startswith('diff --git'):
            break
        expanded_hunk.append(hunk_lines[i])
        hunk_end = i + 1
    
    # Add context lines after
    original_end = old_start + old_count - 1
    for line_num in range(original_end + 1, new_old_end + 1):
        if line_num <= len(file_lines):
            expanded_hunk.append(f" {file_lines[line_num - 1].rstrip()}")
    
    # Create expansion info
    expansion_info = {
        "file": file_path,
        "original_start_line": old_start,
        "original_end_line": old_start + old_count - 1,
        "expanded_start_line": new_old_start,
        "expanded_end_line": new_old_end,
        "context_lines_added": context_lines,
        "lines_added_before": max(0, old_start - new_old_start),
        "lines_added_after": max(0, new_old_end - (old_start + old_count - 1))
    }
    
    return expanded_hunk, expansion_info


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Simple diff inspector - generates original and expanded diffs",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --repo /path/to/repo --c1 abc123 --c2 def456
  %(prog)s --repo /path/to/repo --c1 abc123 --c2 def456 --output-dir /tmp/my_inspection/ --context 15
        """
    )

    parser.add_argument(
        "--repo",
        required=True,
        help="Directory where the git repository is checked out"
    )

    parser.add_argument(
        "--c1",
        required=True,
        help="First commit hash (older commit)"
    )

    parser.add_argument(
        "--c2",
        required=True,
        help="Second commit hash (newer commit)"
    )

    parser.add_argument(
        "--output-dir",
        default="/tmp/git_diff",
        help="Directory to save inspection files (default: /tmp/git_diff)"
    )

    parser.add_argument(
        "--context",
        type=int,
        default=10,
        help="Number of additional context lines to add (default: 10)"
    )

    args = parser.parse_args()

    try:
        # Create output directory
        output_dir = Path(args.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        print(f"🔍 Starting simple diff inspection...")
        print(f"📁 Repository: {args.repo}")
        print(f"🔄 Commits: {args.c1} → {args.c2}")
        print(f"💾 Output directory: {args.output_dir}")
        print(f"📏 Context lines: {args.context}")
        print()

        # Generate original diff
        print("📝 Generating original diff...")
        original_diff = generate_original_diff(args.repo, args.c1, args.c2)
        
        if not original_diff.strip():
            print("❌ No diff content generated - no changes between commits")
            return

        # Generate expanded diff
        print("🔧 Expanding diff with additional context...")
        expanded_diff, expansion_details = expand_diff_with_simple_context(
            original_diff, args.repo, args.context
        )

        # Save files
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        commit_pattern = f"{args.c1[:8]}_to_{args.c2[:8]}"
        
        # Save original diff
        original_path = output_dir / f"original_diff_{commit_pattern}_{timestamp}.diff"
        with open(original_path, 'w', encoding='utf-8') as f:
            f.write(original_diff)
        
        # Save expanded diff
        expanded_path = output_dir / f"expanded_diff_{commit_pattern}_{timestamp}.diff"
        with open(expanded_path, 'w', encoding='utf-8') as f:
            f.write(expanded_diff)
        
        # Save expansion details
        expansion_path = output_dir / f"expansion_details_{commit_pattern}_{timestamp}.json"
        expansion_data = {
            "timestamp": timestamp,
            "old_commit": args.c1,
            "new_commit": args.c2,
            "context_lines": args.context,
            "total_expansions": len(expansion_details),
            "expansions": expansion_details
        }
        
        with open(expansion_path, 'w', encoding='utf-8') as f:
            json.dump(expansion_data, f, indent=2)

        # Save summary
        summary_path = output_dir / f"diff_summary_{commit_pattern}_{timestamp}.json"
        changed_files = extract_changed_files_from_diff(original_diff)
        summary_data = {
            "timestamp": timestamp,
            "old_commit": args.c1,
            "new_commit": args.c2,
            "repo_path": args.repo,
            "changed_files": changed_files,
            "original_diff_path": str(original_path),
            "expanded_diff_path": str(expanded_path),
            "expansion_details_path": str(expansion_path),
            "original_diff_size": len(original_diff),
            "expanded_diff_size": len(expanded_diff),
            "expansion_ratio": len(expanded_diff) / len(original_diff) if len(original_diff) > 0 else 0,
            "context_lines": args.context
        }
        
        with open(summary_path, 'w', encoding='utf-8') as f:
            json.dump(summary_data, f, indent=2)

        print()
        print("✅ Diff inspection completed successfully!")
        print(f"📂 Generated files in {args.output_dir}:")
        print(f"   📄 Original diff: {original_path}")
        print(f"   📄 Expanded diff: {expanded_path}")
        print(f"   📄 Expansion details: {expansion_path}")
        print(f"   📄 Summary file: {summary_path}")
        
        print()
        print("🔍 Use these commands to inspect the differences:")
        print(f"   diff {original_path} {expanded_path}")
        print(f"   code {original_path} {expanded_path}")
        print(f"   cat {expansion_path} | jq '.expansions'")

    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()