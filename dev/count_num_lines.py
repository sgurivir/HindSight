#!/usr/bin/env python3
"""
Count number of lines for each file in a directory (recursively)
and print them in reverse order of line count.

Usage:
    python count_lines.py [directory] [--ext py,txt,json]
"""

import os
import argparse

def count_lines_in_file(file_path):
    try:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            return sum(1 for _ in f)
    except Exception as e:
        print(f"Error reading {file_path}: {e}")
        return 0

def count_lines(root_dir, extensions):
    results = []
    for dirpath, _, filenames in os.walk(root_dir):
        for filename in filenames:
            if any(filename.endswith(f".{ext}") for ext in extensions):
                path = os.path.join(dirpath, filename)
                line_count = count_lines_in_file(path)
                results.append((path, line_count))
    return results

def main():
    parser = argparse.ArgumentParser(description="Count lines per file recursively.")
    parser.add_argument("directory", nargs="?", default=".", help="Root directory to scan")
    parser.add_argument("--ext", default="py", help="Comma-separated list of extensions (e.g. py,txt,md)")
    args = parser.parse_args()

    extensions = [e.strip().lstrip(".") for e in args.ext.split(",") if e.strip()]
    results = count_lines(args.directory, extensions)
    results.sort(key=lambda x: x[1], reverse=True)

    for path, count in results:
        print(f"{count:6d}  {path}")

if __name__ == "__main__":
    main()
