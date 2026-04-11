import os
import json
import argparse
from typing import List, Generator, Dict
from ..core.lang_util.all_supported_extensions import ALL_SUPPORTED_EXTENSIONS


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
    def _is_file_in_directory(file_path: str, directory: str) -> bool:
        """
        Helper method to check if a file is in a specific directory.
        
        Args:
            file_path: Normalized file path
            directory: Normalized directory path
            
        Returns:
            bool: True if file is in the directory
        """
        normalized_dir = directory.lstrip('./')
        normalized_file = file_path.lstrip('./')
        return normalized_file.startswith(normalized_dir + '/') or normalized_file == normalized_dir

    @staticmethod
    def _matches_directory_component(file_path: str, directory_name: str) -> bool:
        """
        Helper method to check if any directory component in the file path matches the directory name.
        
        Args:
            file_path: Normalized file path
            directory_name: Directory name to match
            
        Returns:
            bool: True if any directory component matches
        """
        file_path_parts = file_path.split('/')
        if len(file_path_parts) > 1:  # Ensure there's at least one directory component
            for i in range(len(file_path_parts) - 1):  # Exclude the filename
                if file_path_parts[i] == directory_name:
                    return True
        return False

    @staticmethod
    def should_analyze_by_directory_filters(file_path: str, include_directories: list = None, exclude_directories: list = None, exclude_files: list = None) -> bool:
        """
        Static method to check if a file should be analyzed based on directory and file filters.
        This matches the logic from code_analyzer._should_analyze_function_by_directory_filters()
        to ensure consistent filtering order across the codebase.

        Filtering order and precedence rules:
        1. Check exclude_files first (always takes precedence)
        2. Check include_directories - if provided, only analyze files in these directories
        3. Check exclude_directories with the following precedence rules:
           
           PRECEDENCE RULES:
           - If include_dir is PARENT/GRANDPARENT of exclude_dir: exclude_dir is still excluded
             Example: include=["src"], exclude=["src/tests"] → "src/tests/" files are EXCLUDED
           
           - If include_dir is CHILD/GRANDCHILD of exclude_dir: include_dir takes precedence
             Example: include=["src/main"], exclude=["src"] → "src/main/" files are INCLUDED
           
           - If include_dir is SAME LEVEL as exclude_dir: include_dir takes precedence
             Example: include=["statistics"], exclude=["statistics"] → "statistics/" files are INCLUDED

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
            matching_include_dir = None
            for include_dir in include_directories:
                normalized_include_dir = include_dir.lstrip('./')

                # Case 1: Complete relative path match
                if FilteredFileFinder._is_file_in_directory(normalized_file_path, normalized_include_dir):
                    file_in_include_dir = True
                    matching_include_dir = normalized_include_dir
                    break

                # Case 2: Directory name match
                if FilteredFileFinder._matches_directory_component(normalized_file_path, normalized_include_dir):
                    file_in_include_dir = True
                    matching_include_dir = normalized_include_dir
                    break

            if not file_in_include_dir:
                return False

            # Step 3: Check exclude_directories - but only exclude if:
            # a) exclude_dir is a subdirectory of the matching include_dir, OR
            # b) exclude_dir is at the same level or higher than include_dir
            if exclude_directories and matching_include_dir:
                for exclude_dir in exclude_directories:
                    normalized_exclude_dir = exclude_dir.lstrip('./')
                    
                    # Check if file matches the exclude pattern
                    if normalized_file_path.startswith(normalized_exclude_dir + '/') or normalized_file_path == normalized_exclude_dir:
                        # If exclude_dir is the same as include_dir, include takes precedence
                        if normalized_exclude_dir == matching_include_dir:
                            continue  # Skip this exclusion, include takes precedence
                        
                        # If exclude_dir is a subdirectory of include_dir, exclude it
                        if normalized_exclude_dir.startswith(matching_include_dir + '/'):
                            return False
                        
                        # If exclude_dir is a parent or sibling of include_dir, exclude it
                        if not matching_include_dir.startswith(normalized_exclude_dir + '/'):
                            return False

        else:
            # Step 3 (alternative): If no include_directories specified, just check exclude_directories
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

    def enumerate(self) -> Generator[str, None, None]:
        """
        Enumerate and yield one relative file path at a time.

        Yields:
            str: Relative file path from repo_dir
        """
        for root, dirs, files in os.walk(self.repo_dir):
            # Filter directories in-place to control os.walk traversal
            dirs[:] = [d for d in dirs if self._should_include_directory(os.path.join(root, d))]

            # Check if current directory should be included
            if not self._should_include_directory(root):
                continue

            # Yield files that meet criteria
            for file in files:
                file_path = os.path.join(root, file)
                if self._should_include_file(file_path):
                    yield self._get_relative_path(file_path)

    def get(self) -> List[str]:
        """
        Get complete list of relative file paths that satisfy the filtering criteria.

        Returns:
            List[str]: List of relative file paths from repo_dir
        """
        return list(self.enumerate())

    @staticmethod
    def count_files_with_supported_extensions(
        repo_dir: str,
        include_directories: List[str] = None,
        exclude_directories: List[str] = None
    ) -> int:
        """
        Count files with supported extensions in a directory after recursively scanning.
        
        This is a static utility method that counts all files with extensions in ALL_SUPPORTED_EXTENSIONS,
        applying include and exclude directory filters.
        
        Args:
            repo_dir: Root directory to search in
            include_directories: List of directories to include (default: all directories)
            exclude_directories: List of directories to exclude
            
        Returns:
            int: Count of files with supported extensions
            
        Example:
            >>> count = FilteredFileFinder.count_files_with_supported_extensions(
            ...     repo_dir="/path/to/repo",
            ...     include_directories=["src", "lib"],
            ...     exclude_directories=["src/tests", "build"]
            ... )
            >>> print(f"Found {count} files")
        """
        # Create a finder instance with supported extensions
        finder = FilteredFileFinder(
            repo_dir=repo_dir,
            include_directories=include_directories or [],
            exclude_directories=exclude_directories or [],
            exclude_files=[],
            extensions=ALL_SUPPORTED_EXTENSIONS
        )
        
        # Count files by iterating through the generator
        count = 0
        for _ in finder.enumerate():
            count += 1
        
        return count

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
            # is_last parameter is unused but kept for interface compatibility
            result = ""
            # items = []  # Unused variable

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
  python filtered_file_finder.py --repo /path/to/repo --extensions .py .java
  python filtered_file_finder.py --repo /path/to/repo --include_dirs src tests --exclude_dirs __pycache__
  python filtered_file_finder.py --repo /path/to/repo --config config.json
  python filtered_file_finder.py --repo /path/to/repo --out_file filtered_files.txt
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

    args = parser.parse_args()

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

    # Validate repository directory
    if not os.path.isdir(args.repo):
        print(f"Error: Repository directory '{args.repo}' does not exist or is not a directory.")
        return 1

    # Create FilteredFileFinder instance
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

    return 0


if __name__ == '__main__':
    exit(main())