#!/usr/bin/env python3
"""
Script to count files using FilteredFileFinder from hindsight.utils.filtered_file_finder.

This script extends the functionality with additional features like file extension statistics.
"""

import argparse
import os
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Dict, List, Optional

# Add the parent directory to the Python path so we can import hindsight
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Import DiffStrategy enum and git utilities
from hindsight.analysys_strategy.diff_strategy import DiffStrategy
from hindsight.core.constants import DEFAULT_DIFF_DAYS
from hindsight.utils.filtered_file_finder import FilteredFileFinder

def get_default_branch(repo_dir: str) -> str:
    """
    Get the default branch of a git repository.

    Args:
        repo_dir: Repository directory path

    Returns:
        str: Default branch name (e.g., 'main', 'master')
    """
    try:
        # Try to get the default branch from remote
        result = subprocess.run(
            ['git', 'symbolic-ref', 'refs/remotes/origin/HEAD'],
            cwd=repo_dir,
            capture_output=True,
            text=True,
            check=True
        )
        # Extract branch name from refs/remotes/origin/HEAD -> refs/remotes/origin/main
        default_branch = result.stdout.strip().split('/')[-1]
        return default_branch
    except subprocess.CalledProcessError:
        # Fallback: try to get current branch
        try:
            result = subprocess.run(
                ['git', 'rev-parse', '--abbrev-ref', 'HEAD'],
                cwd=repo_dir,
                capture_output=True,
                text=True,
                check=True
            )
            return result.stdout.strip()
        except subprocess.CalledProcessError:
            # Final fallback
            return 'main'


def get_recently_modified_files(repo_dir: str, days: int = DEFAULT_DIFF_DAYS) -> List[str]:
    """
    Get files modified in the last N days.

    Args:
        repo_dir: Repository directory path
        days: Number of days to look back (default: DEFAULT_DIFF_DAYS for 3 weeks)

    Returns:
        List[str]: List of relative file paths
    """
    try:
        since_date = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
        result = subprocess.run(
            ['git', 'log', '--name-only', '--pretty=format:', f'--since={since_date}'],
            cwd=repo_dir,
            capture_output=True,
            text=True,
            check=True
        )

        if not result.stdout.strip():
            return []

        # Filter out empty lines and get unique files
        files = [line.strip() for line in result.stdout.split('\n') if line.strip()]
        return list(set(files))  # Remove duplicates
    except subprocess.CalledProcessError as e:
        print(f"Error getting recently modified files: {e}")
        return []


def get_branch_diff_files(repo_dir: str, base_branch: str, current_branch: str = None) -> List[str]:
    """
    Get files that differ between current branch and base branch.

    Args:
        repo_dir: Repository directory path
        base_branch: Base branch to compare against
        current_branch: Current branch (if None, uses HEAD)

    Returns:
        List[str]: List of relative file paths
    """
    try:
        if current_branch is None:
            # Compare current HEAD against base branch
            diff_spec = f'{base_branch}..HEAD'
        else:
            diff_spec = f'{base_branch}..{current_branch}'

        result = subprocess.run(
            ['git', 'diff', '--name-only', diff_spec],
            cwd=repo_dir,
            capture_output=True,
            text=True,
            check=True
        )

        if not result.stdout.strip():
            return []

        files = [line.strip() for line in result.stdout.split('\n') if line.strip()]
        return files
    except subprocess.CalledProcessError as e:
        print(f"Error getting branch diff files: {e}")
        return []


def filter_files_by_strategy(repo_dir: str, strategy: DiffStrategy, base_branch: str = None,
                           include_dirs: List[str] = None, exclude_dirs: List[str] = None,
                           exclude_files: List[str] = None, extensions: List[str] = None) -> List[str]:
    """
    Filter files based on the specified strategy.

    Args:
        repo_dir: Repository directory path
        strategy: DiffStrategy enum value
        base_branch: Base branch for branch-based strategy
        include_dirs: Directories to include
        exclude_dirs: Directories to exclude
        exclude_files: Files to exclude
        extensions: File extensions to include

    Returns:
        List[str]: List of filtered file paths
    """
    if strategy == DiffStrategy.RECENTLY_MODIFIED_FILES:
        files = get_recently_modified_files(repo_dir, days=DEFAULT_DIFF_DAYS)  # 3 weeks
    elif strategy == DiffStrategy.BRANCH_BASED:
        if not base_branch:
            base_branch = get_default_branch(repo_dir)
        files = get_branch_diff_files(repo_dir, base_branch)
    else:  # ENTIRE_REPO
        return None  # Let the normal FilteredFileFinder handle this

    if not files:
        return []

    # Apply the same filtering logic as FilteredFileFinder
    try:
        # Create a temporary finder to use its filtering methods
        temp_finder = FilteredFileFinder(
            repo_dir=repo_dir,
            include_directories=include_dirs or [],
            exclude_directories=exclude_dirs or [],
            exclude_files=exclude_files or [],
            extensions=extensions or []
        )

        # Filter the git-provided files using the same logic
        filtered_files = []
        for file_path in files:
            full_path = os.path.join(repo_dir, file_path)
            if os.path.exists(full_path) and temp_finder._should_include_file(full_path):
                filtered_files.append(file_path)

        return filtered_files
    except ImportError:
        # Fallback: basic filtering
        filtered_files = []
        for file_path in files:
            full_path = os.path.join(repo_dir, file_path)
            if os.path.exists(full_path):
                # Basic extension filtering
                if extensions:
                    _, ext = os.path.splitext(file_path)
                    if ext.lower() not in [e.lower() for e in extensions]:
                        continue

                # Basic directory filtering
                if exclude_dirs:
                    excluded = False
                    for exclude_dir in exclude_dirs:
                        if file_path.startswith(exclude_dir + '/') or file_path.startswith(exclude_dir + os.sep):
                            excluded = True
                            break
                    if excluded:
                        continue

                filtered_files.append(file_path)

        return filtered_files


def create_tree_from_files(repo_dir: str, files: List[str]) -> str:
    """
    Create a tree structure from a list of files.

    Args:
        repo_dir: Repository directory path
        files: List of relative file paths

    Returns:
        str: Tree-style string representation
    """
    if not files:
        return "No files found matching the criteria.\n"

    # Build directory structure
    tree_dict = {}
    for file_path in files:
        parts = file_path.split('/')
        current = tree_dict

        # Build nested dictionary structure
        for i, part in enumerate(parts):
            if i == len(parts) - 1:  # It's a file
                if '_files' not in current:
                    current['_files'] = []
                current['_files'].append(part)
            else:  # It's a directory
                if part not in current:
                    current[part] = {}
                current = current[part]

    # Function to count total files in a directory (including subdirectories)
    def count_files_recursive(node: Dict) -> int:
        count = 0
        # Count files in current directory
        if '_files' in node:
            count += len(node['_files'])
        # Count files in subdirectories
        for key, value in node.items():
            if key != '_files' and isinstance(value, dict):
                count += count_files_recursive(value)
        return count

    # Generate tree string with file counts
    def build_tree_string(node: Dict, prefix: str = "", is_last: bool = True) -> str:
        result = ""
        items = []

        # Add directories
        dirs = [k for k in node.keys() if k != '_files']
        dirs.sort()

        # Add files
        files = node.get('_files', [])
        files.sort()

        # Combine directories and files
        all_items = [(d, True) for d in dirs] + [(f, False) for f in files]

        for i, (item, is_dir) in enumerate(all_items):
            is_item_last = (i == len(all_items) - 1)

            if is_dir:
                # Count files in this directory (including subdirectories)
                file_count = count_files_recursive(node[item])
                item_with_count = f"{item} ({file_count})"
            else:
                item_with_count = item

            if is_item_last:
                result += f"{prefix}└── {item_with_count}\n"
                new_prefix = prefix + "    "
            else:
                result += f"{prefix}├── {item_with_count}\n"
                new_prefix = prefix + "│   "

            if is_dir and item in node:
                result += build_tree_string(node[item], new_prefix, is_item_last)

        return result

    # Start with repo directory name and total file count
    repo_name = os.path.basename(repo_dir)
    total_files = count_files_recursive(tree_dict)
    tree_output = f"{repo_name}/ ({total_files})\n"
    tree_output += build_tree_string(tree_dict)

    return tree_output


def print_extension_stats(repo_dir: str, all_supported_extensions: list) -> None:
    """
    Print statistics of file counts by extension for the entire repository.
    Only counts files with extensions in ALL_SUPPORTED_EXTENSIONS.

    Args:
        repo_dir: Repository directory path
        all_supported_extensions: List of supported file extensions
    """
    try:
        pass  # FilteredFileFinder already imported at top
    except ImportError:
        # Use fallback implementation if import fails
        print("Warning: Could not import FilteredFileFinder, using fallback implementation")
        return

    # Create a finder for the entire repository with only supported extensions
    stats_finder = FilteredFileFinder(
        repo_dir=repo_dir,
        include_directories=[],  # Include all directories
        exclude_directories=[],  # No exclusions for stats
        exclude_files=[],        # No file exclusions for stats
        extensions=all_supported_extensions.append(".py")
    )

    # Get all files with supported extensions
    all_files = stats_finder.get()

    # Count files by extension
    extension_counts = defaultdict(int)
    total_files = 0

    for file_path in all_files:
        _, ext = os.path.splitext(file_path)
        if ext:
            extension_counts[ext.lower()] += 1
        else:
            extension_counts['(no extension)'] += 1
        total_files += 1

    # Sort extensions by count (descending) then alphabetically
    sorted_extensions = sorted(extension_counts.items(), key=lambda x: (-x[1], x[0]))

    print("\n" + "="*50)
    print("FILE EXTENSION STATISTICS")
    print("="*50)
    print(f"Total files with supported extensions: {total_files}")
    print(f"Repository: {repo_dir}")
    print(f"Supported extensions: {all_supported_extensions}")
    print("-" * 50)

    if sorted_extensions:
        # Calculate column widths for nice formatting
        max_ext_width = max(len(ext) for ext, _ in sorted_extensions)
        max_count_width = max(len(str(count)) for _, count in sorted_extensions)

        for extension, count in sorted_extensions:
            percentage = (count / total_files * 100) if total_files > 0 else 0
            print(f"{extension:<{max_ext_width}} : {count:>{max_count_width}} files ({percentage:5.1f}%)")
    else:
        print("No files found with supported extensions.")

    print("="*50)

try:
    from hindsight.utils.filtered_file_finder import FilteredFileFinder, load_config_filters
    from hindsight.core.lang_util.all_supported_extensions import ALL_SUPPORTED_EXTENSIONS

    def main_with_stats():
        """
        Enhanced main function that supports --print-stats argument.
        """
        parser = argparse.ArgumentParser(
            description="Find and display files in a repository with filtering options",
            formatter_class=argparse.RawDescriptionHelpFormatter,
            epilog="""
Examples:
  python count_files.py --repo /path/to/repo --extensions .py .java
  python count_files.py --repo /path/to/repo --include_dirs src tests --exclude_dirs __pycache__
  python count_files.py --repo /path/to/repo --config config.json
  python count_files.py --repo /path/to/repo --out_file filtered_files.txt
  python count_files.py --repo /path/to/repo --print-stats
            """
        )

        parser.add_argument(
            '--repo',
            required=True,
            help='Repository directory to search in'
        )

        parser.add_argument(
            '--config',
            help='JSON configuration file to load filtering parameters from (same format as code_analyzer)'
        )

        parser.add_argument(
            '--include_dirs',
            nargs='*',
            default=None,
            help='Directories to include (relative paths from repo root). Overrides config file if provided.'
        )

        parser.add_argument(
            '--exclude_dirs',
            nargs='*',
            default=None,
            help='Directories to exclude (relative paths from repo root). Overrides config file if provided.'
        )

        parser.add_argument(
            '--exclude_files',
            nargs='*',
            default=None,
            help='Files to exclude (relative paths from repo root). Overrides config file if provided.'
        )

        parser.add_argument(
            '--extensions',
            nargs='*',
            default=ALL_SUPPORTED_EXTENSIONS,
            help=f'File extensions to include (default: {ALL_SUPPORTED_EXTENSIONS}). Use empty list to include all files.'
        )

        parser.add_argument(
            '--out_file',
            help='Output file to write the tree structure to'
        )

        parser.add_argument(
            '--print-stats',
            action='store_true',
            help='Print statistics of file counts by extension for the entire repository (only supported extensions)'
        )

        parser.add_argument(
            '--strategy',
            choices=[strategy.value for strategy in DiffStrategy],
            default=DiffStrategy.ENTIRE_REPO.value,
            help=f'Analysis strategy: {", ".join([s.value for s in DiffStrategy])} (default: {DiffStrategy.ENTIRE_REPO.value})'
        )

        parser.add_argument(
            '--base_branch',
            help='Base branch for branch_based strategy (defaults to repository default branch)'
        )

        args = parser.parse_args()

        # Validation: prevent both strategy and base_branch being passed together
        # (except when strategy is branch_based)
        if args.base_branch and args.strategy != DiffStrategy.BRANCH_BASED.value:
            parser.error("--base_branch can only be used with --strategy branch_based")

        # Load config file if provided
        config_filters = {}
        if args.config:
            config_filters = load_config_filters(args.config)
            print(f"Loaded filtering parameters from config: {args.config}")

        # Use command line arguments if provided, otherwise use config values, otherwise use defaults
        include_dirs = args.include_dirs if args.include_dirs is not None else config_filters.get('include_directories', [])
        exclude_dirs = args.exclude_dirs if args.exclude_dirs is not None else config_filters.get('exclude_directories', [])
        exclude_files = args.exclude_files if args.exclude_files is not None else config_filters.get('exclude_files', [])

        # Print the filtering parameters being used
        if include_dirs:
            print(f"Include directories: {include_dirs}")
        if exclude_dirs:
            print(f"Exclude directories: {exclude_dirs}")
        if exclude_files:
            print(f"Exclude files: {exclude_files}")
        if args.extensions:
            print(f"File extensions: {args.extensions}")

        print("\n\n=============================\n\n")
        # Validate repository directory
        if not os.path.isdir(args.repo):
            print(f"Error: Repository directory '{args.repo}' does not exist or is not a directory.")
            return 1

        # Check if this is a git repository for strategy-based filtering
        strategy = DiffStrategy(args.strategy)
        if strategy != DiffStrategy.ENTIRE_REPO:
            if not os.path.exists(os.path.join(args.repo, '.git')):
                print(f"Error: Strategy '{args.strategy}' requires a git repository, but '{args.repo}' is not a git repository.")
                return 1

        # Handle strategy-based filtering
        if strategy != DiffStrategy.ENTIRE_REPO:
            print(f"Using strategy: {strategy.value}")

            # Get filtered files based on strategy
            strategy_files = filter_files_by_strategy(
                repo_dir=args.repo,
                strategy=strategy,
                base_branch=args.base_branch,
                include_dirs=include_dirs,
                exclude_dirs=exclude_dirs,
                exclude_files=exclude_files,
                extensions=args.extensions
            )

            if not strategy_files:
                print(f"No files found matching strategy '{strategy.value}' and filtering criteria.")
                return 0

            print(f"Found {len(strategy_files)} files matching strategy '{strategy.value}'")

            # Create a simple tree structure from the filtered files
            tree_output = create_tree_from_files(args.repo, strategy_files)
        else:
            # Use normal FilteredFileFinder for entire_repo strategy
            finder = FilteredFileFinder(
                repo_dir=args.repo,
                include_directories=include_dirs,
                exclude_directories=exclude_dirs,
                exclude_files=exclude_files,
                extensions=args.extensions
            )

            # Generate tree structure
            tree_output = finder.get_tree_structure()

        # Output to file or stdout
        if args.out_file:
            try:
                with open(args.out_file, 'w', encoding='utf-8') as f:
                    f.write(tree_output)
                print(f"Tree structure written to: {args.out_file}")
            except IOError as e:
                print(f"Error writing to file '{args.out_file}': {e}")
                return 1
        else:
            print(tree_output)

        # Print extension statistics if requested
        if args.print_stats:
            print_extension_stats(args.repo, ALL_SUPPORTED_EXTENSIONS)

        return 0

    if __name__ == '__main__':
        # Use our enhanced main function
        exit(main_with_stats())

except ImportError as e:
    print(f"Error importing hindsight.utils.filtered_file_finder: {e}")
    print("Make sure you're running this script from the correct directory and hindsight is properly installed.")

    # Fallback: copy the main function code if import fails
    import argparse
    import json
    from typing import List, Dict

    # Define ALL_SUPPORTED_EXTENSIONS as fallback
    ALL_SUPPORTED_EXTENSIONS = [".cpp", ".cc", ".c", ".mm", ".m", ".h", ".swift", ".kt", ".kts", ".java"]

    # Copy of the FilteredFileFinder class and related functions
    class FilteredFileFinder:
        """
        A class to find files in a repository directory based on various filtering criteria.

        Supports filtering by:
        - File extensions
        - Include/exclude directories
        - Exclude specific files
        """

        def __init__(
            self,
            repo_dir: str,
            include_directories: List[str] = None,
            exclude_directories: List[str] = None,
            exclude_files: List[str] = None,
            extensions: List[str] = None
        ):
            """
            Initialize the FilteredFileFinder.

            Args:
                repo_dir: Root directory to search in
                include_directories: List of relative directory paths to include (default: all directories)
                exclude_directories: List of relative directory paths to exclude
                exclude_files: List of relative file paths to exclude
                extensions: List of file extensions to include (e.g., ['.py', '.java'])
            """
            self.repo_dir = os.path.abspath(repo_dir)
            self.include_directories = include_directories or []
            self.exclude_directories = exclude_directories or []
            self.exclude_files = exclude_files or []
            self.extensions = extensions or []

            # Normalize paths to use forward slashes and ensure they're relative
            self.include_directories = [self._normalize_path(d) for d in self.include_directories]
            self.exclude_directories = [self._normalize_path(d) for d in self.exclude_directories]
            self.exclude_files = [self._normalize_path(f) for f in self.exclude_files]

        def _normalize_path(self, path: str) -> str:
            """Normalize path to use forward slashes and remove leading slash if present."""
            normalized = path.replace('\\', '/')
            if normalized.startswith('/'):
                normalized = normalized[1:]
            return normalized

        def _get_relative_path(self, full_path: str) -> str:
            """Get relative path from repo_dir."""
            rel_path = os.path.relpath(full_path, self.repo_dir)
            return self._normalize_path(rel_path)

        def _is_directory_excluded(self, rel_dir_path: str) -> bool:
            """
            Check if a directory should be excluded based on exclude_directories config.
            Supports both directory names and relative paths.

            Args:
                rel_dir_path: Relative directory path from repo root

            Returns:
                bool: True if directory should be excluded, False otherwise
            """
            for exclude_pattern in self.exclude_directories:
                # Case 1: Direct match with relative path (e.g., "Daemon/Shared")
                if rel_dir_path == exclude_pattern:
                    return True

                # Case 2: Directory is a subdirectory of excluded path
                if rel_dir_path.startswith(exclude_pattern + '/'):
                    return True

                # Case 3: Directory name matches (legacy behavior)
                # Split the path and check if any directory component matches
                path_parts = rel_dir_path.split('/')
                for part in path_parts:
                    if part == exclude_pattern:
                        return True

            return False

        @staticmethod
        def should_analyze_by_directory_filters(file_path: str, include_directories: list = None, exclude_directories: list = None, exclude_files: list = None) -> bool:
            """
            Static method to check if a file should be analyzed based on directory and file filters.
            This matches the logic from code_analyzer._should_analyze_function_by_directory_filters()
            to ensure consistent filtering order across the codebase.

            Filtering order:
            1. Check exclude_files first
            2. Check include_directories
            3. Check exclude_directories (excludes even if in include_directories)

            Args:
                file_path: File path to check (relative path)
                include_directories: List of directories to include (default: all directories)
                exclude_directories: List of directories to exclude
                exclude_files: List of files to exclude

            Returns:
                bool: True if the file should be analyzed, False otherwise
            """
            # Normalize file path
            normalized_file_path = file_path.lstrip('./')

            # Get filtering parameters with defaults
            include_directories = include_directories or []
            exclude_directories = exclude_directories or []
            exclude_files = exclude_files or []

            # Step 1: Check if file is in exclude_files list
            if exclude_files:
                for exclude_file in exclude_files:
                    normalized_exclude_file = exclude_file.lstrip('./')
                    if normalized_file_path == normalized_exclude_file or normalized_file_path.endswith('/' + normalized_exclude_file):
                        return False

            # Step 2: Check include_directories - if provided, only analyze files in these directories
            if include_directories:
                file_in_include_dir = False
                for include_dir in include_directories:
                    normalized_include_dir = include_dir.lstrip('./')
                    if normalized_file_path.startswith(normalized_include_dir + '/') or normalized_file_path == normalized_include_dir:
                        file_in_include_dir = True
                        break

                if not file_in_include_dir:
                    return False

            # Step 3: Check exclude_directories - exclude these even if they are in include_directories
            if exclude_directories:
                for exclude_dir in exclude_directories:
                    normalized_exclude_dir = exclude_dir.lstrip('./')
                    if normalized_file_path.startswith(normalized_exclude_dir + '/') or normalized_file_path == normalized_exclude_dir:
                        return False

            return True

        def _should_include_directory(self, dir_path: str) -> bool:
            """Check if a directory should be included based on include/exclude criteria."""
            rel_dir_path = self._get_relative_path(dir_path)

            # Check against exclude_directories (supports both directory names and relative paths)
            if self._is_directory_excluded(rel_dir_path):
                return False

            # If include_directories is empty, include all directories (except excluded ones)
            if not self.include_directories:
                return True

            # Check if directory is in include list or is a subdirectory of an included directory
            for include_dir in self.include_directories:
                if rel_dir_path.startswith(include_dir + '/') or rel_dir_path == include_dir:
                    return True
                # Also include if this directory is a parent of an included directory
                if include_dir.startswith(rel_dir_path + '/'):
                    return True

            return False

        def _should_include_file(self, file_path: str) -> bool:
            """Check if a file should be included based on extension and exclude criteria."""
            rel_file_path = self._get_relative_path(file_path)

            # Use the unified filtering method for directory and file filters
            if not self.should_analyze_by_directory_filters(
                rel_file_path,
                self.include_directories,
                self.exclude_directories,
                self.exclude_files
            ):
                return False

            # If no extensions specified, include all files
            if not self.extensions:
                return True

            # Check if file has one of the specified extensions
            file_ext = os.path.splitext(file_path)[1].lower()
            return file_ext in [ext.lower() for ext in self.extensions]

        def get(self) -> List[str]:
            """
            Get complete list of relative file paths that satisfy the filtering criteria.

            Returns:
                List[str]: List of relative file paths from repo_dir
            """
            files = []
            for root, dirs, file_list in os.walk(self.repo_dir):
                # Filter directories in-place to control os.walk traversal
                dirs[:] = [d for d in dirs if self._should_include_directory(os.path.join(root, d))]

                # Check if current directory should be included
                if not self._should_include_directory(root):
                    continue

                # Add files that meet criteria
                for file in file_list:
                    file_path = os.path.join(root, file)
                    if self._should_include_file(file_path):
                        files.append(self._get_relative_path(file_path))
            return files

        def get_tree_structure(self) -> str:
            """
            Generate a tree-style representation of the filtered files with file counts.

            Returns:
                str: Tree-style string representation of the directory structure with file counts in angular brackets
            """
            files = self.get()
            if not files:
                return "No files found matching the criteria.\n"

            # Build directory structure
            tree_dict = {}
            for file_path in files:
                parts = file_path.split('/')
                current = tree_dict

                # Build nested dictionary structure
                for i, part in enumerate(parts):
                    if i == len(parts) - 1:  # It's a file
                        if '_files' not in current:
                            current['_files'] = []
                        current['_files'].append(part)
                    else:  # It's a directory
                        if part not in current:
                            current[part] = {}
                        current = current[part]

            # Function to count total files in a directory (including subdirectories)
            def count_files_recursive(node: Dict) -> int:
                count = 0
                # Count files in current directory
                if '_files' in node:
                    count += len(node['_files'])
                # Count files in subdirectories
                for key, value in node.items():
                    if key != '_files' and isinstance(value, dict):
                        count += count_files_recursive(value)
                return count

            # Generate tree string with file counts
            def build_tree_string(node: Dict, prefix: str = "", is_last: bool = True) -> str:
                result = ""
                items = []

                # Add directories
                dirs = [k for k in node.keys() if k != '_files']
                dirs.sort()

                # Add files
                files = node.get('_files', [])
                files.sort()

                # Combine directories and files
                all_items = [(d, True) for d in dirs] + [(f, False) for f in files]

                for i, (item, is_dir) in enumerate(all_items):
                    is_item_last = (i == len(all_items) - 1)

                    if is_dir:
                        # Count files in this directory (including subdirectories)
                        file_count = count_files_recursive(node[item])
                        item_with_count = f"{item} ({file_count})"
                    else:
                        item_with_count = item

                    if is_item_last:
                        result += f"{prefix}└── {item_with_count}\n"
                        new_prefix = prefix + "    "
                    else:
                        result += f"{prefix}├── {item_with_count}\n"
                        new_prefix = prefix + "│   "

                    if is_dir and item in node:
                        result += build_tree_string(node[item], new_prefix, is_item_last)

                return result

            # Start with repo directory name and total file count
            repo_name = os.path.basename(self.repo_dir)
            total_files = count_files_recursive(tree_dict)
            tree_output = f"{repo_name}/ ({total_files})\n"
            tree_output += build_tree_string(tree_dict)

            return tree_output


    def load_config_filters(config_path: str) -> Dict[str, List[str]]:
        """
        Load filtering parameters from a JSON configuration file.

        Args:
            config_path: Path to the JSON configuration file

        Returns:
            Dict containing include_directories, exclude_directories, and exclude_files lists
        """
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                config = json.load(f)

            return {
                'include_directories': config.get('include_directories', []),
                'exclude_directories': config.get('exclude_directories', []),
                'exclude_files': config.get('exclude_files', [])
            }
        except (FileNotFoundError, json.JSONDecodeError, KeyError) as e:
            print(f"Error loading config file '{config_path}': {e}")
            return {
                'include_directories': [],
                'exclude_directories': [],
                'exclude_files': []
            }


    def main():
        """
        Main function to run FilteredFileFinder from command line.
        """
        parser = argparse.ArgumentParser(
            description="Find and display files in a repository with filtering options",
            formatter_class=argparse.RawDescriptionHelpFormatter,
            epilog="""
Examples:
  python count_files.py --repo /path/to/repo --extensions .py .java
  python count_files.py --repo /path/to/repo --include_dirs src tests --exclude_dirs __pycache__
  python count_files.py --repo /path/to/repo --config config.json
  python count_files.py --repo /path/to/repo --out_file filtered_files.txt
  python count_files.py --repo /path/to/repo --print-stats
            """
        )

        parser.add_argument(
            '--repo',
            required=True,
            help='Repository directory to search in'
        )

        parser.add_argument(
            '--config',
            help='JSON configuration file to load filtering parameters from (same format as code_analyzer)'
        )

        parser.add_argument(
            '--include_dirs',
            nargs='*',
            default=None,
            help='Directories to include (relative paths from repo root). Overrides config file if provided.'
        )

        parser.add_argument(
            '--exclude_dirs',
            nargs='*',
            default=None,
            help='Directories to exclude (relative paths from repo root). Overrides config file if provided.'
        )

        parser.add_argument(
            '--exclude_files',
            nargs='*',
            default=None,
            help='Files to exclude (relative paths from repo root). Overrides config file if provided.'
        )

        parser.add_argument(
            '--extensions',
            nargs='*',
            default=ALL_SUPPORTED_EXTENSIONS,
            help=f'File extensions to include (default: {ALL_SUPPORTED_EXTENSIONS}). Use empty list to include all files.'
        )

        parser.add_argument(
            '--out_file',
            help='Output file to write the tree structure to'
        )

        parser.add_argument(
            '--print-stats',
            action='store_true',
            help='Print statistics of file counts by extension for the entire repository (only supported extensions)'
        )

        parser.add_argument(
            '--strategy',
            choices=[strategy.value for strategy in DiffStrategy],
            default=DiffStrategy.ENTIRE_REPO.value,
            help=f'Analysis strategy: {", ".join([s.value for s in DiffStrategy])} (default: {DiffStrategy.ENTIRE_REPO.value})'
        )

        parser.add_argument(
            '--base_branch',
            help='Base branch for branch_based strategy (defaults to repository default branch)'
        )

        args = parser.parse_args()

        # Validation: prevent both strategy and base_branch being passed together
        # (except when strategy is branch_based)
        if args.base_branch and args.strategy != DiffStrategy.BRANCH_BASED.value:
            parser.error("--base_branch can only be used with --strategy branch_based")

        # Load config file if provided
        config_filters = {}
        if args.config:
            config_filters = load_config_filters(args.config)
            print(f"Loaded filtering parameters from config: {args.config}")

        # Use command line arguments if provided, otherwise use config values, otherwise use defaults
        include_dirs = args.include_dirs if args.include_dirs is not None else config_filters.get('include_directories', [])
        exclude_dirs = args.exclude_dirs if args.exclude_dirs is not None else config_filters.get('exclude_directories', [])
        exclude_files = args.exclude_files if args.exclude_files is not None else config_filters.get('exclude_files', [])

        # Print the filtering parameters being used
        if include_dirs:
            print(f"Include directories: {include_dirs}")
        if exclude_dirs:
            print(f"Exclude directories: {exclude_dirs}")
        if exclude_files:
            print(f"Exclude files: {exclude_files}")
        if args.extensions:
            print(f"File extensions: {args.extensions}")

        print("\n\n=============================\n\n")
        # Validate repository directory
        if not os.path.isdir(args.repo):
            print(f"Error: Repository directory '{args.repo}' does not exist or is not a directory.")
            return 1

        # Check if this is a git repository for strategy-based filtering
        strategy = DiffStrategy(args.strategy)
        if strategy != DiffStrategy.ENTIRE_REPO:
            if not os.path.exists(os.path.join(args.repo, '.git')):
                print(f"Error: Strategy '{args.strategy}' requires a git repository, but '{args.repo}' is not a git repository.")
                return 1

        # Handle strategy-based filtering
        if strategy != DiffStrategy.ENTIRE_REPO:
            print(f"Using strategy: {strategy.value}")

            # Get filtered files based on strategy
            strategy_files = filter_files_by_strategy(
                repo_dir=args.repo,
                strategy=strategy,
                base_branch=args.base_branch,
                include_dirs=include_dirs,
                exclude_dirs=exclude_dirs,
                exclude_files=exclude_files,
                extensions=args.extensions
            )

            if not strategy_files:
                print(f"No files found matching strategy '{strategy.value}' and filtering criteria.")
                return 0

            print(f"Found {len(strategy_files)} files matching strategy '{strategy.value}'")

            # Create a simple tree structure from the filtered files
            tree_output = create_tree_from_files(args.repo, strategy_files)
        else:
            # Use normal FilteredFileFinder for entire_repo strategy
            finder = FilteredFileFinder(
                repo_dir=args.repo,
                include_directories=include_dirs,
                exclude_directories=exclude_dirs,
                exclude_files=exclude_files,
                extensions=args.extensions
            )

            # Generate tree structure
            tree_output = finder.get_tree_structure()

        # Output to file or stdout
        if args.out_file:
            try:
                with open(args.out_file, 'w', encoding='utf-8') as f:
                    f.write(tree_output)
                print(f"Tree structure written to: {args.out_file}")
            except IOError as e:
                print(f"Error writing to file '{args.out_file}': {e}")
                return 1
        else:
            print(tree_output)

        # Print extension statistics if requested
        if args.print_stats:
            print_extension_stats(args.repo, ALL_SUPPORTED_EXTENSIONS)

        return 0


    if __name__ == '__main__':
        exit(main())