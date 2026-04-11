#!/usr/bin/env python3
import os
import sys
import argparse

def count_chars_and_lines_in_file(filepath):
    """Count characters and lines in a file safely."""
    try:
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
            char_count = len(content)
            line_count = content.count('\n') + (1 if content and not content.endswith('\n') else 0)
            return char_count, line_count
    except Exception as e:
        print(f"⚠️ Skipping {filepath}: {e}")
        return 0, 0

def scan_directory(directory, extensions, ignore_dirs):
    """Recursively count characters and lines in files with given extensions, skipping ignored directories."""
    results = []
    for root, dirs, files in os.walk(directory):
        # Remove ignored directories from traversal
        # Check both absolute paths and directory names for flexibility
        dirs[:] = [d for d in dirs if not any(
            os.path.join(root, d) == ignore_dir or  # Exact absolute path match
            d == ignore_dir or                      # Directory name match
            os.path.join(root, d) == os.path.abspath(ignore_dir) or  # Absolute path of relative ignore_dir
            d == os.path.basename(ignore_dir)       # Base name of absolute ignore_dir
            for ignore_dir in ignore_dirs
        )]

        for filename in files:
            if any(filename.lower().endswith(ext.lower()) for ext in extensions):
                filepath = os.path.join(root, filename)
                char_count, line_count = count_chars_and_lines_in_file(filepath)
                # Store filepath, char_count, line_count, and estimated_tokens
                estimated_tokens = char_count // 3  # Using integer division for cleaner output
                results.append((filepath, char_count, line_count, estimated_tokens))
    return sorted(results, key=lambda x: x[1], reverse=True)  # Sort by character count

def main():
    script_name = os.path.basename(sys.argv[0])

    parser = argparse.ArgumentParser(
 description=(
        f"Analyze files recursively: count characters, lines, and estimate LLM tokens.\n"
        f"Token estimation uses: characters ÷ 3 = estimated tokens\n"
        f"Example:\n"
        f"  python3 {script_name} -d ~/projects/myrepo -e .py .cpp .h -x tests old_code"
    ),
    formatter_class=argparse.RawTextHelpFormatter
 )
    parser.add_argument(
        "-d", "--directory",
        required=True,
        help="Root directory to scan"
    )
    parser.add_argument(
        "-e", "--extensions",
        required=True,
        nargs="+",
        help="File extensions to include (e.g. .py .cpp .h)"
    )
    parser.add_argument(
        "-x", "--ignore",
        nargs="*",
        default=[],
        help="List of directories to ignore (absolute or relative paths)"
    )
    args = parser.parse_args()

    # Keep ignore directories as provided (don't force absolute paths)
    # This allows both absolute paths and directory names to work
    ignore_dirs = args.ignore
    results = scan_directory(os.path.abspath(args.directory), args.extensions, ignore_dirs)

    print(f"\nFile analysis for {args.directory} (filtered by {args.extensions}):")
    print(f"{'Characters':>10} {'Lines':>6} {'Est.Tokens':>10}  File Path")
    print(f"{'-'*10} {'-'*6} {'-'*10}  {'-'*50}")

    for filepath, char_count, line_count, estimated_tokens in results:
        print(f"{char_count:10d} {line_count:6d} {estimated_tokens:10d}  {filepath}")

    # Print summary statistics
    if results:
        total_chars = sum(item[1] for item in results)
        total_lines = sum(item[2] for item in results)
        total_tokens = sum(item[3] for item in results)
        print(f"\n{'='*80}")
        print(f"Summary for {len(results)} files:")
        print(f"  Total characters: {total_chars:,}")
        print(f"  Total lines: {total_lines:,}")
        print(f"  Estimated tokens: {total_tokens:,}")

if __name__ == "__main__":
    main()