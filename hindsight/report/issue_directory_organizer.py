import os
import sys
import argparse
import pickle
from typing import Set, Optional
from pathlib import Path

from ..utils.file_content_provider import FileContentProvider
from ..utils.output_directory_provider import get_output_directory_provider
from ..utils.log_util import get_logger

# Initialize logger
logger = get_logger(__name__)

# Add project root to Python path for standalone execution
if __name__ == "__main__":
    # Get the project root (3 levels up from this file)
    project_root = Path(__file__).parent.parent.parent.parent
    sys.path.insert(0, str(project_root))


class DirectoryNode:
    """
    Represents a directory node in the tree structure.
    Each node contains sets of files and subdirectories.
    """

    def __init__(self, name: str, path: str):
        """
        Initialize a directory node.

        Args:
            name: The name of the directory
            path: The full path to the directory
        """
        self.name = name
        self.path = path
        self.files: Set[str] = set()  # Set of file names in this directory
        self.directories: Set['DirectoryNode'] = set()  # Set of subdirectory nodes
        self.parent: Optional['DirectoryNode'] = None
        self.issues: list = []  # List of issues assigned to this directory

    def add_file(self, filename: str) -> None:
        """Add a file to this directory node."""
        self.files.add(filename)

    def add_directory(self, directory_node: 'DirectoryNode') -> None:
        """Add a subdirectory node to this directory."""
        directory_node.parent = self
        self.directories.add(directory_node)

    def get_all_files(self) -> Set[str]:
        """Get all files in this directory."""
        return self.files.copy()

    def get_all_directories(self) -> Set['DirectoryNode']:
        """Get all subdirectory nodes."""
        return self.directories.copy()

    def find_directory(self, name: str) -> Optional['DirectoryNode']:
        """Find a subdirectory by name."""
        for directory in self.directories:
            if directory.name == name:
                return directory
        return None

    def add_issue(self, issue: dict) -> None:
        """Add an issue to this directory node."""
        self.issues.append(issue)

    def get_issues(self) -> list:
        """Get all issues assigned to this directory."""
        return self.issues.copy()

    def get_issues_by_severity(self, severity: str) -> list:
        """Get issues filtered by severity."""
        return [issue for issue in self.issues if issue.get('severity') == severity]

    def get_issues_by_type(self, issue_type: str) -> list:
        """Get issues filtered by type."""
        return [issue for issue in self.issues if issue.get('issueType') == issue_type]

    def get_issue_count(self) -> int:
        """Get total number of issues in this directory."""
        return len(self.issues)

    def get_severity_counts(self) -> dict:
        """Get count of issues by severity."""
        counts = {'critical': 0, 'high': 0, 'medium': 0, 'low': 0}
        for issue in self.issues:
            severity = issue.get('severity', 'unknown')
            if severity in counts:
                counts[severity] += 1
        return counts

    def get_path(self) -> str:
        """Get the path of this directory node."""
        return self.path

    def __str__(self) -> str:
        return f"DirectoryNode(name='{self.name}', path='{self.path}', files={len(self.files)}, dirs={len(self.directories)})"

    def __repr__(self) -> str:
        return self.__str__()

    def __hash__(self) -> int:
        return hash(self.path)

    def __eq__(self, other) -> bool:
        if not isinstance(other, DirectoryNode):
            return False
        return self.path == other.path


class RepositoryDirHierarchy:
    """
    Utility class for building and managing a tree index of directory structure.
    Takes a repository path and builds a complete tree starting from the root.
    Provides directory structure caching and tree formatting functionality.
    """

    def __init__(self, repository_path: str, pickled_index_path: str = None):
        """
        Initialize the RepositoryDirHierarchy with a repository path.

        Args:
            repository_path: Path to the repository root
            pickled_index_path: Optional path to pickled index for FileContentProvider
        """
        self.repository_path = Path(repository_path).resolve()
        self.root_node: Optional[DirectoryNode] = None
        self._path_to_node_map = {}  # Cache for quick node lookup by path
        self.pickled_index_path = pickled_index_path

        # Build the tree index immediately upon initialization
        self._build_tree_index()

    @staticmethod
    def get_directory_structure_for_repo(repo_path: str, max_depth: int = 6) -> str:
        """
        Get directory structure for a given repository path as a tree string.

        Args:
            repo_path: Path to the repository root
            max_depth: Maximum depth to traverse (default: 6)

        Returns:
            str: Directory structure as a tree string with |- formatting
        """

        # Check if cached structure exists
        cache_path = RepositoryDirHierarchy._get_cache_path(repo_path)
        cached_structure = RepositoryDirHierarchy._load_cached_structure(cache_path)

        if cached_structure:
            return cached_structure

        # Build new structure
        hierarchy = RepositoryDirHierarchy(repo_path)
        structure = hierarchy.get_tree_structure(max_depth=max_depth)

        # Cache the structure
        RepositoryDirHierarchy._save_cached_structure(cache_path, structure)

        return structure

    @staticmethod
    def _get_cache_path(repo_path: str) -> str:
        """Get the cache file path for a repository using OutputDirectoryProvider singleton."""
        output_provider = get_output_directory_provider()
        cache_dir = f"{output_provider.get_repo_artifacts_dir(repo_path)}/directory_structure"

        os.makedirs(cache_dir, exist_ok=True)
        return os.path.join(cache_dir, "structure.pkl")

    @staticmethod
    def _load_cached_structure(cache_path: str) -> Optional[str]:
        """Load cached directory structure if it exists and is recent."""
        try:
            if os.path.exists(cache_path):
                with open(cache_path, 'rb') as f:
                    cached_data = pickle.load(f)
                    return cached_data.get('structure')
        except Exception:
            # If cache is corrupted or incompatible, ignore it
            pass
        return None

    @staticmethod
    def _save_cached_structure(cache_path: str, structure: str) -> None:
        """Save directory structure to cache."""
        try:
            cache_data = {
                'structure': structure,
                'timestamp': os.path.getmtime(cache_path) if os.path.exists(cache_path) else 0
            }
            with open(cache_path, 'wb') as f:
                pickle.dump(cache_data, f)
        except Exception:
            # If caching fails, continue without caching
            pass

    def get_tree_structure(self, max_depth: int = 6) -> str:
        """
        Get the directory structure as a formatted tree string.

        Args:
            max_depth: Maximum depth to traverse

        Returns:
            str: Formatted tree structure with |- for child directories
        """
        if not self.root_node:
            return "No directory structure available"

        lines = []
        self._format_tree_recursive(self.root_node, lines, "", 0, max_depth)
        return "\n".join(lines)

    def _format_tree_recursive(self, node: DirectoryNode, lines: list, prefix: str, depth: int, max_depth: int) -> None:
        """
        Recursively format the tree structure with |- formatting.

        Args:
            node: Current directory node
            lines: List to append formatted lines to
            prefix: Current prefix for indentation
            depth: Current depth level
            max_depth: Maximum depth to traverse
        """
        if depth > max_depth:
            return

        # Add current directory
        if depth == 0:
            lines.append(f"{node.name}/")
        else:
            lines.append(f"{prefix}|- {node.name}/")

        # Sort subdirectories for consistent output
        sorted_dirs = sorted(node.directories, key=lambda d: d.name)

        # Process subdirectories
        for i, subdir in enumerate(sorted_dirs):
            is_last = (i == len(sorted_dirs) - 1)

            if depth == 0:
                new_prefix = "   "
            else:
                new_prefix = prefix + ("   " if is_last else "|  ")

            self._format_tree_recursive(subdir, lines, new_prefix, depth + 1, max_depth)

    def find_directories_by_name(self, directory_name: str) -> list:
        """
        Find all directories that match the given name.

        Args:
            directory_name: Name of the directory to search for

        Returns:
            list: List of DirectoryNode objects that match the name
        """
        matches = []

        def search_recursive(node: DirectoryNode):
            if node.name == directory_name:
                matches.append(node)

            for subdir in node.directories:
                search_recursive(subdir)

        if self.root_node:
            search_recursive(self.root_node)

        return matches

    def get_directory_hierarchy_by_path(self, relative_path: str) -> Optional[str]:
        """
        Get directory hierarchy for a specific path.

        Args:
            relative_path: Relative path from repository root

        Returns:
            str: Directory hierarchy string or None if not found
        """
        if not relative_path or relative_path == ".":
            return self.get_tree_structure()

        # Find the target directory
        target_path = self.repository_path / relative_path
        target_node = self.find_node_by_path(str(target_path))

        if not target_node:
            return None

        # Generate hierarchy for this specific directory
        lines = []
        self._format_tree_recursive(target_node, lines, "", 0, 6)
        return "\n".join(lines)

    def _build_tree_index(self) -> None:
        """Build the complete tree index starting from the repository root."""
        if not self.repository_path.exists():
            logger.warning(f"Repository path does not exist: {self.repository_path}")
            # Create an empty root node to allow graceful handling
            self.root_node = DirectoryNode(
                name=self.repository_path.name,
                path=str(self.repository_path)
            )
            self._path_to_node_map[str(self.repository_path)] = self.root_node
            return

        if not self.repository_path.is_dir():
            logger.warning(f"Repository path is not a directory: {self.repository_path}")
            # Create an empty root node to allow graceful handling
            self.root_node = DirectoryNode(
                name=self.repository_path.name,
                path=str(self.repository_path)
            )
            self._path_to_node_map[str(self.repository_path)] = self.root_node
            return

        # Create root node
        self.root_node = DirectoryNode(
            name=self.repository_path.name,
            path=str(self.repository_path)
        )
        self._path_to_node_map[str(self.repository_path)] = self.root_node

        # Recursively build the tree
        self._build_directory_tree(self.root_node)

    def _build_directory_tree(self, current_node: DirectoryNode) -> None:
        """
        Recursively build the directory tree starting from the current node.

        Args:
            current_node: The current directory node to process
        """
        try:
            current_path = Path(current_node.path)

            # Iterate through all items in the current directory
            for item in current_path.iterdir():
                if item.is_file():
                    # Add file to current node
                    current_node.add_file(item.name)
                elif item.is_dir():
                    # Create new directory node and add to current node
                    dir_node = DirectoryNode(
                        name=item.name,
                        path=str(item)
                    )
                    current_node.add_directory(dir_node)
                    self._path_to_node_map[str(item)] = dir_node

                    # Recursively process subdirectory
                    self._build_directory_tree(dir_node)

        except PermissionError:
            # Skip directories we don't have permission to read
            pass
        except OSError:
            # Skip directories that cause OS errors
            pass

    def get_root_node(self) -> Optional[DirectoryNode]:
        """Get the root node of the directory tree."""
        return self.root_node

    def find_node_by_path(self, path: str) -> Optional[DirectoryNode]:
        """
        Find a directory node by its path.

        Args:
            path: The path to search for

        Returns:
            DirectoryNode if found, None otherwise
        """
        resolved_path = str(Path(path).resolve())
        return self._path_to_node_map.get(resolved_path)

    def get_all_files_in_directory(self, directory_path: str) -> Set[str]:
        """
        Get all files in a specific directory.

        Args:
            directory_path: Path to the directory

        Returns:
            Set of file names in the directory
        """
        node = self.find_node_by_path(directory_path)
        if node:
            return node.get_all_files()
        return set()

    def get_all_subdirectories(self, directory_path: str) -> Set[DirectoryNode]:
        """
        Get all subdirectory nodes for a specific directory.

        Args:
            directory_path: Path to the directory

        Returns:
            Set of DirectoryNode objects representing subdirectories
        """
        node = self.find_node_by_path(directory_path)
        if node:
            return node.get_all_directories()
        return set()

    def print_tree(self, node: Optional[DirectoryNode] = None, indent: int = 0) -> None:
        """
        Print the directory tree structure.

        Args:
            node: Starting node (defaults to root)
            indent: Current indentation level
        """
        if node is None:
            node = self.root_node

        if node is None:
            print("No tree structure available")
            return

        # Print current directory
        print("  " * indent + f"📁 {node.name}/")

        # Print files in current directory
        for file in sorted(node.files):
            print("  " * (indent + 1) + f"📄 {file}")

        # Recursively print subdirectories
        for directory in sorted(node.directories, key=lambda d: d.name):
            self.print_tree(directory, indent + 1)

    def get_tree_statistics(self) -> dict:
        """
        Get statistics about the directory tree.

        Returns:
            Dictionary containing tree statistics
        """
        if not self.root_node:
            return {"total_directories": 0, "total_files": 0}

        total_dirs = 0
        total_files = 0

        def count_recursive(node: DirectoryNode):
            nonlocal total_dirs, total_files
            total_dirs += 1
            total_files += len(node.files)

            for subdir in node.directories:
                count_recursive(subdir)

        count_recursive(self.root_node)

        return {
            "total_directories": total_dirs,
            "total_files": total_files,
            "root_path": self.repository_path
        }

    def create_file_content_provider(self) -> Optional['FileContentProvider']:
        """
        Create a FileContentProvider instance from the pickled index path if available.

        Returns:
            FileContentProvider instance or None if no pickled index path provided
        """
        if not self.pickled_index_path:
            return None

        try:
            return FileContentProvider.get()
        except RuntimeError as e:
            print(f"Warning: FileContentProvider singleton not initialized: {e}")
            return None

    def create_issue_directory_organizer(self, file_content_provider: 'FileContentProvider' = None) -> 'IssueDirectoryOrganizer':
        """
        Create an IssueDirectoryOrganizer with this hierarchy and a FileContentProvider.

        Args:
            file_content_provider: Optional FileContentProvider instance. If not provided,
                                 will attempt to create one from pickled_index_path

        Returns:
            IssueDirectoryOrganizer instance
        """
        if file_content_provider is None:
            file_content_provider = self.create_file_content_provider()

        if file_content_provider is None:
            # Try to get existing FileContentProvider singleton as fallback
            try:
                file_content_provider = FileContentProvider.get()
            except RuntimeError:
                print("Warning: FileContentProvider singleton not initialized, some file resolution may not work")
                file_content_provider = None

        return IssueDirectoryOrganizer(self, file_content_provider)


class IssueDirectoryOrganizer:
    """
    Helper class that uses RepositoryDirHierarchy to organize issues into directory structure.
    Assigns reports/issues to directories based on the files mentioned in the issues.
    """

    def __init__(self, repository_hierarchy: RepositoryDirHierarchy, file_content_provider: 'FileContentProvider'):
        """
        Initialize the organizer with a repository hierarchy and file content provider.

        Args:
            repository_hierarchy: An instance of RepositoryDirHierarchy
            file_content_provider: An instance of FileContentProvider for file path resolution
        """
        self.hierarchy = repository_hierarchy
        self.file_content_provider = file_content_provider
        self.unassigned_issues = []  # Issues that couldn't be assigned to any directory
        self.issue_to_directory_map = {}  # Map from issue ID to directory node
        self.exclude_directories = []  # List of directory patterns to exclude

    def set_exclude_directories(self, exclude_directories: list) -> None:
        """
        Set the list of directory patterns to exclude from issue assignment.
        
        Args:
            exclude_directories: List of directory patterns to exclude
        """
        self.exclude_directories = exclude_directories or []
        logger.info(f"Set {len(self.exclude_directories)} exclude directory patterns")

    def _is_directory_excluded(self, directory_path: str) -> bool:
        """
        Check if a directory should be excluded based on exclude patterns.
        
        Args:
            directory_path: Path to check against exclude patterns
            
        Returns:
            bool: True if directory should be excluded, False otherwise
        """
        if not self.exclude_directories or not directory_path:
            return False
            
        # Convert to relative path for pattern matching
        repo_path_str = str(self.hierarchy.repository_path)
        if directory_path.startswith(repo_path_str):
            relative_path = directory_path[len(repo_path_str):].lstrip('/')
        else:
            relative_path = directory_path
            
        # Check each exclude pattern
        for pattern in self.exclude_directories:
            if pattern in relative_path or relative_path.startswith(pattern):
                return True
                
        return False

    def assign_issues_to_directories(self, issues: list) -> dict:
        """
        Assign a list of issues to directories based on the file paths in the issues.

        Args:
            issues: List of issue dictionaries (from report generators)

        Returns:
            dict: Statistics about issue assignment
        """
        assigned_count = 0
        unassigned_count = 0

        for issue in issues:
            if self._assign_single_issue(issue):
                assigned_count += 1
            else:
                unassigned_count += 1
                self.unassigned_issues.append(issue)

        return {
            'total_issues': len(issues),
            'assigned': assigned_count,
            'unassigned': unassigned_count,
            'assignment_rate': (assigned_count / len(issues)) * 100 if issues else 0
        }

    def _assign_single_issue(self, issue: dict) -> bool:
        """
        Assign a single issue to the appropriate directory using enhanced logic.

        Strategy:
        1. First try file_name (same as callstack matching)
        2. If file_name results in multiple files, try file_path + "/" + file_name for disambiguation
        3. If FileContentProvider fails, use file_path directly to find directory
        4. Only fall back to current logic if all fail

        Args:
            issue: Issue dictionary

        Returns:
            bool: True if successfully assigned, False otherwise
        """
        # Extract issue details for logging
        file_name = issue.get('file_name', '')
        file_path = issue.get('file_path', '') or issue.get('file', '')
        function_name = issue.get('function_name', '') or issue.get('function', '')

        logger.debug(f"Attempting to assign issue: function='{function_name}', file_name='{file_name}', file_path='{file_path}'")

        # Step 1: Try file_name first (same as callstack matching)
        if file_name:
            logger.debug(f"Step 1: Trying file_name '{file_name}' with enhanced logic")
            target_directory = self._find_directory_for_file_with_enhanced_logic(file_name, issue)
            if target_directory:
                target_directory.add_issue(issue)
                issue_key = id(issue)
                self.issue_to_directory_map[issue_key] = target_directory
                logger.debug(f"✅ Successfully assigned issue to directory: {target_directory.path}")
                return True
            else:
                logger.debug(f"❌ Step 1 failed: Could not find directory for file_name '{file_name}'")

        # Step 2: If file_name failed, try using file_path directly to find directory
        if file_path:
            logger.debug(f"Step 2: Trying file_path '{file_path}' direct directory extraction")
            # Try to extract directory from file_path and find it in hierarchy
            target_directory = self._find_directory_from_path(file_path)
            if target_directory:
                target_directory.add_issue(issue)
                issue_key = id(issue)
                self.issue_to_directory_map[issue_key] = target_directory
                logger.debug(f"✅ Successfully assigned issue to directory via file_path: {target_directory.path}")
                return True
            else:
                logger.debug(f"❌ Step 2 failed: Could not extract directory from file_path '{file_path}'")

            # Fall back to original logic
            logger.debug(f"Step 3: Trying original file resolution logic for '{file_path}'")
            target_directory = self._find_directory_for_file(file_path)
            if target_directory:
                target_directory.add_issue(issue)
                issue_key = id(issue)
                self.issue_to_directory_map[issue_key] = target_directory
                logger.debug(f"✅ Successfully assigned issue to directory via original logic: {target_directory.path}")
                return True
            else:
                logger.debug(f"❌ Step 3 failed: Original file resolution failed for '{file_path}'")

        logger.warning(f"🚫 ASSIGNMENT FAILED: Could not assign issue to any directory - function='{function_name}', file_name='{file_name}', file_path='{file_path}' - will be placed in 'Unknown' directory")
        return False

    def _find_directory_from_path(self, file_path: str) -> Optional[DirectoryNode]:
        """
        Find directory node directly from file_path by extracting the directory portion.
        This handles cases where file_path contains the full directory path.

        Args:
            file_path: Full path that might be a directory path or contain directory info

        Returns:
            DirectoryNode if found, None otherwise
        """
        if not file_path:
            logger.debug("_find_directory_from_path: file_path is empty")
            return None

        logger.debug(f"_find_directory_from_path: Processing file_path '{file_path}'")

        # Handle different cases of file_path
        directory_path = None

        # Case 1: file_path is already a directory path (e.g., "/Volumes/Data/src/coretime/common")
        if file_path.startswith('/'):
            logger.debug(f"Case 1: Absolute path detected")
            # Extract relative path from repository root
            repo_path_str = str(self.hierarchy.repository_path)
            if file_path.startswith(repo_path_str):
                # Remove repository root to get relative path
                relative_path = file_path[len(repo_path_str):].lstrip('/')
                if relative_path:
                    directory_path = relative_path
                    logger.debug(f"Extracted relative path: '{relative_path}'")
                else:
                    # Path points to repository root
                    logger.debug("Path points to repository root")
                    return self.hierarchy.root_node
            else:
                # Try to find a matching subdirectory name
                path_parts = file_path.split('/')
                logger.debug(f"Path parts: {path_parts}")
                # Look for common directory names like "common", "daemon", etc.
                for part in reversed(path_parts):
                    if part and not part.startswith('.'):
                        # Try to find this directory name in hierarchy
                        found_dirs = self.hierarchy.find_directories_by_name(part)
                        logger.debug(f"Searching for directory named '{part}': found {len(found_dirs)} matches")
                        if len(found_dirs) == 1:
                            logger.debug(f"Found unique directory: {found_dirs[0].path}")
                            return found_dirs[0]
                        elif len(found_dirs) > 1:
                            logger.warning(f"Multiple directories named '{part}' found, assigning to 'Unknown'")
                            return None

        # Case 2: file_path is a relative path (e.g., "common" or "common/subdir" or "common/file.c")
        else:
            logger.debug(f"Case 2: Relative path detected")
            # Extract directory portion by removing the filename
            if '/' in file_path:
                # Split and take all but the last part (which is the filename)
                path_parts = file_path.split('/')
                if len(path_parts) > 1:
                    directory_path = '/'.join(path_parts[:-1])
                    logger.debug(f"Extracted directory path from relative path: '{directory_path}'")
                else:
                    # Single component, treat as directory name
                    directory_path = file_path
            else:
                # No path separator, treat as directory name
                directory_path = file_path

        # Try to find the directory node using the extracted path
        if directory_path:
            full_path = self.hierarchy.repository_path / directory_path
            logger.debug(f"Trying to find directory node at: {full_path}")
            target_node = self.hierarchy.find_node_by_path(str(full_path))
            if target_node:
                logger.debug(f"Found directory node: {target_node.path}")
                return target_node
            else:
                # Try finding by directory name only
                dir_name = directory_path.split('/')[-1] if '/' in directory_path else directory_path
                logger.debug(f"Fallback: Searching for directory named '{dir_name}'")
                found_dirs = self.hierarchy.find_directories_by_name(dir_name)
                logger.debug(f"Found {len(found_dirs)} directories named '{dir_name}'")
                if len(found_dirs) == 1:
                    logger.debug(f"Found unique directory: {found_dirs[0].path}")
                    return found_dirs[0]
                elif len(found_dirs) > 1:
                    logger.warning(f"Multiple directories named '{dir_name}' found, assigning to 'Unknown'")
                    return None

        logger.debug("_find_directory_from_path: No directory found")
        return None

    def _find_directory_for_file(self, file_path: str) -> Optional[DirectoryNode]:
        """
        Find the directory node that should contain the given file.
        Uses FileContentProvider to resolve the actual file location.
        If a file exists in multiple directories, return None to assign to "Unknown".

        Args:
            file_path: Path to the file (only filename is used, directory path is ignored)

        Returns:
            DirectoryNode if found in exactly one location, None if found in multiple locations or not found
        """
        # Extract filename only - IGNORE LLM-provided directory path completely
        if '/' in file_path:
            filename = file_path.split('/')[-1]
        elif '\\' in file_path:
            filename = file_path.split('\\')[-1]
        else:
            filename = file_path

        logger.debug(f"_find_directory_for_file: Extracted filename '{filename}' from file_path '{file_path}'")

        # Use FileContentProvider to resolve the file path
        try:
            resolved_path = FileContentProvider.resolve_file_path(filename, file_path)
            logger.debug(f"FileContentProvider.resolve_file_path returned: {resolved_path}")
        except RuntimeError as e:
            logger.debug(f"FileContentProvider.resolve_file_path failed with RuntimeError: {e}")
            resolved_path = None
        except Exception as e:
            logger.debug(f"FileContentProvider.resolve_file_path failed with unexpected error: {e}")
            resolved_path = None

        if resolved_path:
            # Ensure resolved_path is a Path object
            if isinstance(resolved_path, str):
                resolved_path = Path(resolved_path)

            # Get the directory part of the resolved path
            resolved_dir = str(resolved_path.parent) if resolved_path.parent != resolved_path else ''
            logger.debug(f"Resolved directory: '{resolved_dir}' from path: {resolved_path}")

            if resolved_dir and resolved_dir != '.':
                # Try to find the directory node using the resolved path
                full_resolved_path = self.hierarchy.repository_path / resolved_dir
                target_node = self.hierarchy.find_node_by_path(str(full_resolved_path))
                if target_node:
                    logger.debug(f"Found directory node: {target_node.path}")
                    return target_node
                else:
                    logger.warning(f"Directory '{resolved_dir}' not found in hierarchy for file '{filename}' (full path: {full_resolved_path})")
            else:
                # File is in root directory
                logger.debug(f"File '{filename}' is in root directory")
                return self.hierarchy.root_node

        # If FileContentProvider resolution doesn't work, the file doesn't exist
        # Don't fall back to hierarchy search for non-existent files
        logger.warning(f"FileContentProvider resolution failed for '{filename}', file does not exist, assigning to 'Unknown'")
        return None

    def _find_directory_for_file_with_enhanced_logic(self, file_name: str, issue: dict) -> Optional[DirectoryNode]:
        """
        Enhanced directory finding logic that prioritizes file_name but uses file_path for disambiguation.
        Now also filters out excluded directories.

        Args:
            file_name: Name of the file to find
            issue: Full issue dictionary for additional context

        Returns:
            DirectoryNode if found uniquely, None otherwise
        """
        # Step 1: Check if file_name exists in FileContentProvider index
        if hasattr(self.file_content_provider, 'name_to_path_mapping') and file_name in self.file_content_provider.name_to_path_mapping:
            file_infos = self.file_content_provider.name_to_path_mapping[file_name]

            # Filter out excluded directories
            filtered_file_infos = []
            for file_info in file_infos:
                # Handle both string and dictionary formats
                if isinstance(file_info, dict):
                    file_path = file_info.get('path', '')
                elif isinstance(file_info, str):
                    file_path = file_info
                else:
                    logger.warning(f"Unexpected file_info type {type(file_info)} for '{file_name}', skipping")
                    continue

                if file_path:
                    resolved_path = Path(file_path)
                    resolved_dir = str(resolved_path.parent) if resolved_path.parent != resolved_path else ''
                    
                    # Check if this directory should be excluded
                    if resolved_dir and self._is_directory_excluded(resolved_dir):
                        logger.debug(f"Excluding file '{file_name}' in directory '{resolved_dir}' due to exclude pattern")
                        continue
                        
                    filtered_file_infos.append(file_info)

            if len(filtered_file_infos) == 1:
                # Single location found after filtering - resolve to directory node
                file_info = filtered_file_infos[0]
                # Handle both string and dictionary formats
                if isinstance(file_info, dict):
                    file_path = file_info.get('path', '')
                elif isinstance(file_info, str):
                    file_path = file_info
                else:
                    logger.warning(f"Unexpected file_info type {type(file_info)} for '{file_name}', skipping")
                    file_path = ''

                if file_path:
                    resolved_path = Path(file_path)
                    resolved_dir = str(resolved_path.parent) if resolved_path.parent != resolved_path else ''
                    if resolved_dir and resolved_dir != '.':
                        full_resolved_path = self.hierarchy.repository_path / resolved_dir
                        target_node = self.hierarchy.find_node_by_path(str(full_resolved_path))
                        if target_node:
                            return target_node
                    else:
                        logger.debug(f"File '{file_name}' is in root directory")
                        return self.hierarchy.root_node

            elif len(filtered_file_infos) > 1:
                # Multiple locations found after filtering - try to disambiguate using file_path
                logger.info(f"File '{file_name}' found in {len(filtered_file_infos)} non-excluded locations, attempting disambiguation...")

                # Get file_path from issue for disambiguation
                issue_file_path = issue.get('file_path', '') or issue.get('file', '')
                if issue_file_path:
                    # Try to find a match using file_path context
                    for file_info in filtered_file_infos:
                        # Handle both string and dictionary formats
                        if isinstance(file_info, dict):
                            indexed_path = file_info.get('path', '')
                        elif isinstance(file_info, str):
                            indexed_path = file_info
                        else:
                            logger.warning(f"Unexpected file_info type {type(file_info)} for '{file_name}', skipping")
                            continue

                        # Check if the indexed path matches or contains the issue file_path
                        if issue_file_path in indexed_path or indexed_path.endswith(issue_file_path):
                            resolved_path = Path(indexed_path)
                            resolved_dir = str(resolved_path.parent) if resolved_path.parent != resolved_path else ''
                            if resolved_dir and resolved_dir != '.':
                                full_resolved_path = self.hierarchy.repository_path / resolved_dir
                                target_node = self.hierarchy.find_node_by_path(str(full_resolved_path))
                                if target_node:
                                    logger.info(f"Disambiguated '{file_name}' using file_path context to: {target_node.path}")
                                    return target_node
                            else:
                                logger.info(f"Disambiguated '{file_name}' to root directory using file_path context")
                                return self.hierarchy.root_node

                # If disambiguation failed, fall back to assigning to Unknown
                logger.warning(f"Could not disambiguate '{file_name}' from {len(filtered_file_infos)} non-excluded locations, assigning to 'Unknown':")
                for file_info in filtered_file_infos:
                    # Handle both string and dictionary formats
                    if isinstance(file_info, dict):
                        path_str = file_info.get('path', 'Unknown path')
                    elif isinstance(file_info, str):
                        path_str = file_info
                    else:
                        path_str = f"Unknown path (type: {type(file_info)})"
                    logger.warning(f"  - {path_str}")
                return None
            elif len(file_infos) > len(filtered_file_infos):
                # All locations were excluded
                logger.info(f"All {len(file_infos)} locations for file '{file_name}' were excluded by directory filters")
                return None

        # Step 2: If not in FileContentProvider, fall back to hierarchy search
        return self._search_for_file_in_hierarchy(file_name)

    def _find_directory_for_file_with_path(self, file_name: str, file_path: str) -> Optional[DirectoryNode]:
        """
        Find the directory node that should contain the given file using FileContentProvider exclusively.

        Args:
            file_name: Name of the file to find
            file_path: Path that could be a file or directory

        Returns:
            DirectoryNode if found, None otherwise
        """
        # Use FileContentProvider to resolve the file path
        try:
            resolved_path = FileContentProvider.resolve_file_path(file_name, file_path)
        except RuntimeError:
            resolved_path = None
        if resolved_path:
            # Ensure resolved_path is a Path object
            if isinstance(resolved_path, str):
                resolved_path = Path(resolved_path)

            # Get the directory part of the resolved path
            resolved_dir = str(resolved_path.parent) if resolved_path.parent != resolved_path else ''
            if resolved_dir and resolved_dir != '.':
                # Try to find the directory node using the resolved path
                full_resolved_path = self.hierarchy.repository_path / resolved_dir
                target_node = self.hierarchy.find_node_by_path(str(full_resolved_path))
                if target_node:
                    return target_node
                else:
                    print(f"Warning: Directory '{resolved_dir}' not found in hierarchy for file '{file_name}'")
            else:
                # File is in root directory
                return self.hierarchy.root_node

        return None

    def _search_for_file_in_hierarchy(self, filename: str) -> Optional[DirectoryNode]:
        """
        Search through the entire hierarchy to find a directory containing the file.
        Uses FileContentProvider to check if file exists in multiple locations.
        If file is found in multiple directories, return None to assign to "Unknown".
        Now also filters out excluded directories.

        Args:
            filename: Name of the file to search for

        Returns:
            DirectoryNode if found in exactly one location, None if found in multiple locations or not found
        """
        # First check FileContentProvider for multiple locations
        if hasattr(self.file_content_provider, 'name_to_path_mapping') and filename in self.file_content_provider.name_to_path_mapping:
            file_infos = self.file_content_provider.name_to_path_mapping[filename]
            
            # Filter out excluded directories
            filtered_file_infos = []
            for file_info in file_infos:
                # Handle both string and dictionary formats
                if isinstance(file_info, dict):
                    file_path = file_info.get('path', '')
                elif isinstance(file_info, str):
                    file_path = file_info
                else:
                    logger.warning(f"Unexpected file_info type {type(file_info)} for '{filename}', skipping")
                    continue

                if file_path:
                    resolved_path = Path(file_path)
                    resolved_dir = str(resolved_path.parent) if resolved_path.parent != resolved_path else ''
                    
                    # Check if this directory should be excluded
                    if resolved_dir and self._is_directory_excluded(resolved_dir):
                        logger.debug(f"Excluding file '{filename}' in directory '{resolved_dir}' due to exclude pattern")
                        continue
                        
                    filtered_file_infos.append(file_info)
            
            if len(filtered_file_infos) > 1:
                logger.warning(f"File '{filename}' found in {len(filtered_file_infos)} non-excluded locations via FileContentProvider, assigning to 'Unknown' directory:")
                for file_info in filtered_file_infos:
                    # Handle both string and dictionary formats
                    if isinstance(file_info, dict):
                        path_str = file_info.get('path', 'Unknown path')
                    elif isinstance(file_info, str):
                        path_str = file_info
                    else:
                        path_str = f"Unknown path (type: {type(file_info)})"
                    logger.warning(f"  - {path_str}")
                return None
            elif len(filtered_file_infos) == 1:
                # Single location found after filtering - resolve to directory node
                file_info = filtered_file_infos[0]
                # Handle both string and dictionary formats
                if isinstance(file_info, dict):
                    file_path = file_info.get('path', '')
                elif isinstance(file_info, str):
                    file_path = file_info
                else:
                    logger.warning(f"Unexpected file_info type {type(file_info)} for '{filename}', skipping")
                    file_path = ''

                if file_path:
                    resolved_path = Path(file_path)
                    resolved_dir = str(resolved_path.parent) if resolved_path.parent != resolved_path else ''
                    if resolved_dir and resolved_dir != '.':
                        full_resolved_path = self.hierarchy.repository_path / resolved_dir
                        target_node = self.hierarchy.find_node_by_path(str(full_resolved_path))
                        if target_node:
                            return target_node
                    else:
                        return self.hierarchy.root_node
            elif len(file_infos) > len(filtered_file_infos):
                # All locations were excluded
                logger.info(f"All {len(file_infos)} locations for file '{filename}' were excluded by directory filters")
                return None

        # Fallback to hierarchy search if FileContentProvider doesn't have the file
        found_nodes = []

        def search_recursive(node: DirectoryNode) -> None:
            # Skip excluded directories
            if self._is_directory_excluded(node.path):
                logger.debug(f"Skipping excluded directory: {node.path}")
                return
                
            # Check if this node contains the file (exact match)
            if filename in node.files:
                found_nodes.append(node)

            # Also check for partial filename matches (without extension)
            base_filename = filename.split('.')[0] if '.' in filename else filename
            for file_in_node in node.files:
                if base_filename in file_in_node or file_in_node.split('.')[0] == base_filename:
                    if node not in found_nodes:  # Avoid duplicates
                        found_nodes.append(node)
                    break  # Only add once per directory

            # Recursively search subdirectories
            for subdir in node.directories:
                search_recursive(subdir)

        if self.hierarchy.root_node:
            search_recursive(self.hierarchy.root_node)

            if len(found_nodes) == 1:
                return found_nodes[0]
            elif len(found_nodes) > 1:
                logger.warning(f"File '{filename}' found in {len(found_nodes)} non-excluded directories in hierarchy, assigning to 'Unknown' directory:")
                for node in found_nodes:
                    logger.warning(f"  - {node.path}")
                return None

        # If no match found, return None
        return None

    def get_directory_issue_summary(self, directory_path: str) -> dict:
        """
        Get a summary of issues for a specific directory.

        Args:
            directory_path: Path to the directory

        Returns:
            dict: Summary of issues in the directory
        """
        node = self.hierarchy.find_node_by_path(directory_path)
        if not node:
            return {'error': 'Directory not found'}

        severity_counts = node.get_severity_counts()
        issues = node.get_issues()

        # Group issues by type
        issue_types = {}
        for issue in issues:
            issue_type = issue.get('issueType', 'unknown')
            if issue_type not in issue_types:
                issue_types[issue_type] = 0
            issue_types[issue_type] += 1

        return {
            'directory_path': directory_path,
            'total_issues': len(issues),
            'severity_counts': severity_counts,
            'issue_types': issue_types,
            'files_in_directory': len(node.files),
            'subdirectories': len(node.directories)
        }

    def get_all_directories_with_issues(self) -> list:
        """
        Get a list of all directories that have issues assigned to them.

        Returns:
            list: List of dictionaries with directory info and issue counts
        """
        directories_with_issues = []

        def collect_recursive(node: DirectoryNode):
            if node.get_issue_count() > 0:
                directories_with_issues.append({
                    'path': node.path,
                    'name': node.name,
                    'issue_count': node.get_issue_count(),
                    'severity_counts': node.get_severity_counts(),
                    'files_count': len(node.files),
                    'subdirs_count': len(node.directories)
                })

            # Recursively check subdirectories
            for subdir in node.directories:
                collect_recursive(subdir)

        if self.hierarchy.root_node:
            collect_recursive(self.hierarchy.root_node)

        return directories_with_issues

    def get_unassigned_issues(self) -> list:
        """
        Get the list of issues that couldn't be assigned to any directory.

        Returns:
            list: List of unassigned issues
        """
        return self.unassigned_issues.copy()

    def get_issue_directory(self, issue: dict) -> Optional[DirectoryNode]:
        """
        Get the directory node that an issue was assigned to.

        Args:
            issue: Issue dictionary

        Returns:
            DirectoryNode if the issue was assigned to a directory, None otherwise
        """
        issue_key = id(issue)
        return self.issue_to_directory_map.get(issue_key)

    def print_issue_distribution(self) -> None:
        """Print a summary of how issues are distributed across directories."""
        directories_with_issues = self.get_all_directories_with_issues()

        print("\n" + "=" * 60)
        print("ISSUE DISTRIBUTION ACROSS DIRECTORIES")
        print("=" * 60)

        if not directories_with_issues:
            print("No directories have issues assigned.")
            return

        # Sort by issue count (descending)
        directories_with_issues.sort(key=lambda d: d['issue_count'], reverse=True)

        for dir_info in directories_with_issues:
            print(f"\n📁 {dir_info['name']} ({dir_info['path']})")
            print(f"   Issues: {dir_info['issue_count']}")
            print(f"   Files: {dir_info['files_count']}, Subdirs: {dir_info['subdirs_count']}")

            severity_counts = dir_info['severity_counts']
            severity_summary = []
            for severity, count in severity_counts.items():
                if count > 0:
                    severity_summary.append(f"{severity}: {count}")

            if severity_summary:
                print(f"   Severity: {', '.join(severity_summary)}")

        # Print unassigned issues summary
        if self.unassigned_issues:
            print(f"\n⚠️  Unassigned Issues: {len(self.unassigned_issues)}")
            print("   These issues could not be matched to any directory in the repository.")




def main():
    """
    Main function to demonstrate the RepositoryDirHierarchy.
    Takes a root directory path as command line argument and prints the hierarchical structure.
    Can also search for files using FileContentProvider when -p, -g, and -r arguments are provided.
    """
    parser = argparse.ArgumentParser(
        description="Print directory structure in hierarchical order or search for files"
    )
    parser.add_argument(
        "root_directory",
        help="Path to the root directory to analyze"
    )
    parser.add_argument(
        "--stats",
        action="store_true",
        help="Show statistics about the directory tree"
    )
    parser.add_argument(
        "-p", "--pickled_index",
        help="Path to pickled index needed by FileContentProvider"
    )
    parser.add_argument(
        "-g", "--search_file_name",
        help="File name to search for"
    )
    parser.add_argument(
        "-r", "--search_file_path",
        help="File path to search in"
    )

    args = parser.parse_args()

    try:
        # Create RepositoryDirHierarchy instance
        print(f"Analyzing directory structure for: {args.root_directory}")
        print("=" * 60)

        dir_util = RepositoryDirHierarchy(args.root_directory)

        # Check if file search functionality is requested
        if args.pickled_index and args.search_file_name:
            print(f"Loading FileContentProvider from pickled index: {args.pickled_index}")

            # Get FileContentProvider singleton instance
            try:
                file_provider = FileContentProvider.get()
                print(f"Successfully accessed FileContentProvider singleton")

                # Search for the file
                search_file_path = args.search_file_path or ""
                print(f"\nSearching for file: '{args.search_file_name}' in path: '{search_file_path}'")

                # Use FileContentProvider to guess the file path (better handles search_file_path)
                resolved_path_str = FileContentProvider.guess_path(args.search_file_name, search_file_path)
                resolved_path = Path(resolved_path_str) if resolved_path_str else None

                if resolved_path:
                    print(f"File resolved to: {resolved_path}")

                    # Find the DirectoryNode that contains this file
                    resolved_dir = str(resolved_path.parent) if resolved_path.parent != resolved_path else ''
                    if resolved_dir and resolved_dir != '.':
                        # Try to find the directory node using the resolved path
                        full_resolved_path = dir_util.repository_path / resolved_dir
                        target_node = dir_util.find_node_by_path(str(full_resolved_path))
                        if target_node:
                            print(f"File maps to DirectoryNode: {target_node}")
                            print(f"  Directory path: {target_node.path}")
                            print(f"  Directory name: {target_node.name}")
                            print(f"  Files in directory: {len(target_node.files)}")
                            print(f"  Subdirectories: {len(target_node.directories)}")
                        else:
                            print(f"Warning: Directory '{resolved_dir}' not found in hierarchy")
                    else:
                        # File is in root directory
                        print(f"File maps to root DirectoryNode: {dir_util.root_node}")
                        if dir_util.root_node:
                            print(f"  Directory path: {dir_util.root_node.path}")
                            print(f"  Directory name: {dir_util.root_node.name}")
                            print(f"  Files in directory: {len(dir_util.root_node.files)}")
                            print(f"  Subdirectories: {len(dir_util.root_node.directories)}")
                else:
                    print(f"File '{args.search_file_name}' not found")

                    # Show debug information
                    if args.search_file_name in file_provider.name_to_path_mapping:
                        file_infos = file_provider.name_to_path_mapping[args.search_file_name]
                        print(f"However, '{args.search_file_name}' exists in index with {len(file_infos)} match(es):")
                        for i, file_info in enumerate(file_infos, 1):
                            print(f"  {i}. {file_info['path']}")
                    else:
                        print(f"'{args.search_file_name}' not found in index at all")

            except Exception as e:
                print(f"Error loading FileContentProvider: {e}", file=sys.stderr)
                sys.exit(1)

        elif args.pickled_index or args.search_file_name or args.search_file_path:
            print("Warning: File search requires both -p (pickled_index) and -g (search_file_name) arguments")
            print("Proceeding with normal directory structure display...")

        # Print the hierarchical structure (unless only doing file search)
        if not (args.pickled_index and args.search_file_name):
            dir_util.print_tree()

        # Print statistics if requested
        if args.stats:
            print("\n" + "=" * 60)
            print("DIRECTORY STATISTICS:")
            stats = dir_util.get_tree_statistics()
            print(f"Root Path: {stats['root_path']}")
            print(f"Total Directories: {stats['total_directories']}")
            print(f"Total Files: {stats['total_files']}")

    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Unexpected error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()