#!/usr/bin/env python3
import argparse
import os
import subprocess
import sys
from typing import List, Tuple


"""
Usage:
python3 ./diff_git_branches.py --repo <local_checkout_dir> --branch1 <remote_branch_name_1> --branch2 <remote_branch_name_2>
"""

# Import ALL_SUPPORTED_EXTENSIONS
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from hindsight.core.lang_util.all_supported_extensions import ALL_SUPPORTED_EXTENSIONS


def run_git(repo_path: str, args: List[str], check: bool = True) -> subprocess.CompletedProcess:
    """Run a git command in repo_path and return the CompletedProcess."""
    result = subprocess.run(
        ["git"] + args,
        cwd=repo_path,
        text=True,
        capture_output=True,
    )
    if check and result.returncode != 0:
        print(f"Error running git command: {' '.join(args)}", file=sys.stderr)
        if result.stderr:
            print(result.stderr, file=sys.stderr)
        sys.exit(1)
    return result


def git_ref_exists(repo_path: str, ref: str) -> bool:
    """Return True if ref is a valid git revision in this repo."""
    result = run_git(repo_path, ["rev-parse", "--verify", ref], check=False)
    return result.returncode == 0


def resolve_ref(repo_path: str, ref: str) -> str:
    """
    Resolve a branch/ref name:
    - If ref exists as given, use it.
    - Else, if origin/ref exists, use origin/ref.
    - Else, fail with a clear message.
    """
    if git_ref_exists(repo_path, ref):
        return ref

    origin_ref = f"origin/{ref}"
    if git_ref_exists(repo_path, origin_ref):
        return origin_ref

    print(f"ERROR: Could not resolve ref '{ref}' or '{origin_ref}' in {repo_path}", file=sys.stderr)
    # Show available matching branches to help debugging
    branches = run_git(repo_path, ["branch", "-a"], check=False).stdout
    print("Available branches (for debugging):", file=sys.stderr)
    print(branches, file=sys.stderr)
    sys.exit(1)


def get_changed_files(repo_path: str, branch1: str, branch2: str) -> List[str]:
    """Return list of files changed between two branches."""
    cp = run_git(
        repo_path,
        ["diff", "--name-only", branch1, branch2],
        check=True,
    )
    return [line.strip() for line in cp.stdout.splitlines() if line.strip()]


def filter_files_by_supported_extensions(files: List[str]) -> List[str]:
    """Filter files to only include those with supported extensions."""
    filtered_files = []
    for file_path in files:
        # Get file extension
        _, ext = os.path.splitext(file_path)
        ext = ext.lower()
        
        if ext in ALL_SUPPORTED_EXTENSIONS:
            filtered_files.append(file_path)
    
    return filtered_files


def diff_size_for_file(repo_path: str, branch1: str, branch2: str, file_path: str) -> int:
    """Return the character size of the diff for a single file."""
    cp = run_git(
        repo_path,
        ["diff", branch1, branch2, "--", file_path],
        check=True,
    )
    return len(cp.stdout)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Diff two git branches and list changed files with supported extensions (sorted by size desc)."
    )
    parser.add_argument(
        "--repo",
        required=True,
        help="Path to the git repository",
    )
    parser.add_argument(
        "--branch1",
        required=True,
        help="First branch/ref (e.g. LuckB or origin/LuckB)",
    )
    parser.add_argument(
        "--branch2",
        required=True,
        help="Second branch/ref (e.g. LuckE or origin/LuckE)",
    )
    parser.add_argument(
        "--min-size",
        type=int,
        default=0,
        help="Minimum diff size in characters to report (default: 0 - show all files)",
    )
    args = parser.parse_args()

    repo_path = args.repo

    # Make sure we have up-to-date remote refs
    run_git(repo_path, ["fetch", "--all"], check=False)

    # Resolve branches to actual refs (local or origin/*)
    branch1 = resolve_ref(repo_path, args.branch1)
    branch2 = resolve_ref(repo_path, args.branch2)

    # Get all changed files
    all_changed_files = get_changed_files(repo_path, branch1, branch2)
    
    # Filter to only include files with supported extensions
    supported_files = filter_files_by_supported_extensions(all_changed_files)
    
    results: List[Tuple[str, int]] = []

    for f in supported_files:
        size = diff_size_for_file(repo_path, branch1, branch2, f)
        results.append((f, size))

    # Sort by diff size desc
    results.sort(key=lambda x: x[1], reverse=True)

    # Print all files with supported extensions (or only those above threshold if specified)
    for f, size in results:
        if size >= args.min_size:
            print(f"{size:>8}  {f}")


if __name__ == "__main__":
    main()