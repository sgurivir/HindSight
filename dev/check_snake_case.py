#!/usr/bin/env python3
"""
Find all .py files in a repository that don't follow snake_case naming.
Example valid: my_file.py, data_loader_v2.py
"""

import os
import re
import sys

SNAKE_CASE_PATTERN = re.compile(r"^[a-z0-9_]+\.py$")

def find_non_snake_py_files(root_dir: str):
    bad_files = []
    for dirpath, _, filenames in os.walk(root_dir):
        for f in filenames:
            if f.endswith(".py") and not SNAKE_CASE_PATTERN.match(f):
                bad_files.append(os.path.join(dirpath, f))
    return bad_files

def main():
    root = sys.argv[1] if len(sys.argv) > 1 else "."
    bad_files = find_non_snake_py_files(root)
    if bad_files:
        print("⚠️ Non-snake_case .py files found:")
        for f in bad_files:
            print("  -", f)
        print(f"\nTotal: {len(bad_files)} file(s)")
    else:
        print("✅ All .py files follow snake_case naming convention.")

if __name__ == "__main__":
    main()
