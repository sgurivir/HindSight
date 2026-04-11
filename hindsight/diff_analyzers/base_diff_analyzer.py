#!/usr/bin/env python3
"""
Base Diff Analyzer
Base class for git diff analyzers that provides common functionality
for repository management, commit handling, and file filtering.
"""

import os
import sys
import subprocess
import shutil
import traceback
from pathlib import Path
from typing import Optional, List, TYPE_CHECKING

try:
    import git
    HAS_GITPYTHON = True
except ImportError:
    HAS_GITPYTHON = False
    git = None

if TYPE_CHECKING:
    from ..utils.file_content_provider import FileContentProvider
from abc import ABC, abstractmethod

# Add the project root to Python path for imports
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from ..utils.file_util import clear_directory_contents
from ..utils.log_util import setup_default_logging, get_logger
from ..utils.file_content_provider import FileContentProvider


class BaseDiffAnalyzer(ABC):
    """Base class for git diff analyzers with common repository and commit management functionality."""

    def __init__(self, repo_dir: str, config: dict, out_dir: str,
                 c1: Optional[str] = None, c2: Optional[str] = None,
                 branch1: Optional[str] = None, branch2: Optional[str] = None,
                 branch: Optional[str] = None):
        """
        Initialize the Base Diff Analyzer.

        Args:
            repo_dir: Directory where the repository is already checked out
            config: Configuration dictionary
            out_dir: Output directory for diff results
            c1: First commit hash (optional if using branches)
            c2: Second commit hash (optional if using branches)
            branch1: First branch name (optional if using commits)
            branch2: Second branch name (optional if using commits)
            branch: Branch to checkout from origin (optional - defaults to current branch)
        """
        self.repo_checkout_dir = Path(repo_dir).resolve()
        self.config = config
        
        # Extract repository name from the directory name
        repo_name = self.repo_checkout_dir.name
        
        # Create the new directory structure: <repo>_diff_analysis/
        self.base_out_dir = Path(out_dir).resolve()
        self.out_dir = self.base_out_dir / f"{repo_name}_diff_analysis"
        
        # Create subdirectories
        self.code_dir = self.out_dir / "code"
        self.analysis_dir = self.out_dir / "analysis"
        
        # Create analysis subdirectories
        self.analysis_input_dir = self.analysis_dir / "analysis_input"
        self.code_insights_dir = self.analysis_dir / "code_insights"
        self.directory_structure_dir = self.analysis_dir / "directory_structure"
        self.prompts_sent_dir = self.analysis_dir / "prompts_sent"
        self.results_dir = self.analysis_dir / "results"
        self.c1 = c1
        self.c2 = c2
        self.branch1 = branch1
        self.branch2 = branch2
        self.branch = branch

        # Setup logging
        setup_default_logging(repo_path=str(self.repo_checkout_dir))
        self.logger = get_logger(__name__)

        # Verify that the repository directory exists and is a git repository
        if not self.repo_checkout_dir.exists():
            raise ValueError(f"Repository directory does not exist: {self.repo_checkout_dir}")
        
        if not (self.repo_checkout_dir / '.git').exists():
            raise ValueError(f"Directory is not a git repository: {self.repo_checkout_dir}")

        # Initialize GitPython repo object if available
        if HAS_GITPYTHON:
            try:
                self.git_repo = git.Repo(str(self.repo_checkout_dir))
                self.logger.info("Using GitPython for git operations")
            except Exception as e:
                self.logger.warning(f"Failed to initialize GitPython repo: {e}, falling back to subprocess")
                self.git_repo = None
        else:
            self.logger.warning("GitPython not available, using subprocess for git operations")
            self.git_repo = None

        # Ensure output directories exist
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.code_dir.mkdir(parents=True, exist_ok=True)
        self.analysis_dir.mkdir(parents=True, exist_ok=True)
        self.analysis_input_dir.mkdir(parents=True, exist_ok=True)
        self.code_insights_dir.mkdir(parents=True, exist_ok=True)
        self.directory_structure_dir.mkdir(parents=True, exist_ok=True)
        self.prompts_sent_dir.mkdir(parents=True, exist_ok=True)
        self.results_dir.mkdir(parents=True, exist_ok=True)

        # Commit hashes (will be determined during analysis)
        self.old_commit_hash = None
        self.new_commit_hash = None
        self.changed_files = []
        
        # FileContentProvider for file resolution and reading
        self.file_content_provider = None
        
        # Initialize FileContentProvider since repository already exists
        self._initialize_file_content_provider()

    def setup_repository(self) -> None:
        """Setup repository - since it's already checked out, just verify and optionally checkout branch."""
        self.logger.info(f"Using existing repository at {self.repo_checkout_dir}")

        try:
            # Checkout the specified branch if provided
            if self.branch:
                self._checkout_branch()
            else:
                # Log current branch
                if self.git_repo:
                    try:
                        current_branch = self.git_repo.active_branch.name
                        self.logger.info(f"Using current branch: {current_branch}")
                    except Exception as e:
                        self.logger.warning(f"Could not get current branch via GitPython: {e}")
                else:
                    result = subprocess.run(['git', 'branch', '--show-current'],
                                          cwd=self.repo_checkout_dir, capture_output=True, text=True)
                    if result.returncode == 0:
                        current_branch = result.stdout.strip()
                        self.logger.info(f"Using current branch: {current_branch}")
                    
        except subprocess.CalledProcessError as e:
            self.logger.error(f"Failed to setup repository: {e}")
            raise

    def _checkout_branch(self) -> None:
        """
        Checkout the specified branch from origin.
        """
        try:
            # First, try to checkout the branch if it exists locally
            result = subprocess.run(['git', 'checkout', self.branch],
                                  cwd=self.repo_checkout_dir, capture_output=True, text=True)
            
            if result.returncode == 0:
                self.logger.info(f"Checked out existing local branch: {self.branch}")
            else:
                # Branch doesn't exist locally, try to checkout from origin
                self.logger.info(f"Local branch {self.branch} not found, checking out from origin")
                subprocess.run(['git', 'checkout', '-b', self.branch, f'origin/{self.branch}'],
                             cwd=self.repo_checkout_dir, check=True, capture_output=True)
                self.logger.info(f"Checked out branch from origin: {self.branch}")
                
        except subprocess.CalledProcessError as e:
            self.logger.error(f"Failed to checkout branch {self.branch}: {e}")
            raise

    def find_parent_commit(self, commit_hash: str) -> str:
        """
        Find the parent commit of the given commit.

        Args:
            commit_hash: The commit hash to find parent for

        Returns:
            Parent commit hash
        """
        try:
            result = subprocess.run(
                ['git', 'rev-parse', f'{commit_hash}^'],
                cwd=self.repo_checkout_dir,
                capture_output=True,
                text=True,
                check=True
            )
            parent_hash = result.stdout.strip()
            self.logger.info(f"Found parent commit {parent_hash} for {commit_hash}")
            return parent_hash
        except subprocess.CalledProcessError as e:
            self.logger.error(f"Failed to find parent commit for {commit_hash}: {e}")
            raise

    def get_commit_timestamp(self, commit_hash: str) -> int:
        """
        Get the timestamp of a commit.

        Args:
            commit_hash: The commit hash

        Returns:
            Unix timestamp of the commit
        """
        try:
            result = subprocess.run(
                ['git', 'show', '-s', '--format=%ct', commit_hash],
                cwd=self.repo_checkout_dir,
                capture_output=True,
                text=True,
                check=True
            )
            timestamp = int(result.stdout.strip())
            return timestamp
        except subprocess.CalledProcessError as e:
            self.logger.error(f"Failed to get timestamp for commit {commit_hash}: {e}")
            raise

    def resolve_branch_to_commit(self, branch_name: str) -> str:
        """
        Resolve a branch name to its commit hash.

        Args:
            branch_name: The branch name to resolve

        Returns:
            Commit hash of the branch tip
        """
        try:
            result = subprocess.run(
                ['git', 'rev-parse', branch_name],
                cwd=self.repo_checkout_dir,
                capture_output=True,
                text=True,
                check=True
            )
            commit_hash = result.stdout.strip()
            self.logger.info(f"Resolved branch '{branch_name}' to commit {commit_hash}")
            return commit_hash
        except subprocess.CalledProcessError as e:
            self.logger.error(f"Failed to resolve branch '{branch_name}' to commit: {e}")
            raise

    def determine_commit_order(self) -> None:
        """Determine which commit is newer and set old_commit_hash and new_commit_hash."""
        # Resolve branches to commits if branches are provided
        if self.branch1 or self.branch2:
            if not self.branch1 or not self.branch2:
                raise ValueError("Both branch1 and branch2 must be provided when using branch-based diffing")
            
            self.logger.info(f"Using branch-based diffing: {self.branch1} vs {self.branch2}")
            self.c1 = self.resolve_branch_to_commit(self.branch1)
            self.c2 = self.resolve_branch_to_commit(self.branch2)
        elif self.c1:
            # If c2 is not provided, find the parent of c1
            if not self.c2:
                self.c2 = self.find_parent_commit(self.c1)
                self.logger.info(f"No second commit provided, using parent commit: {self.c2}")
        else:
            raise ValueError("Either commits (c1, c2) or branches (branch1, branch2) must be provided")

        # Get timestamps to determine which is newer
        c1_timestamp = self.get_commit_timestamp(self.c1)
        c2_timestamp = self.get_commit_timestamp(self.c2)

        if c1_timestamp > c2_timestamp:
            self.new_commit_hash = self.c1
            self.old_commit_hash = self.c2
        else:
            self.new_commit_hash = self.c2
            self.old_commit_hash = self.c1

        self.logger.info(f"Determined commit order: old={self.old_commit_hash}, new={self.new_commit_hash}")

    def get_changed_files(self) -> List[str]:
        """Get the list of files changed between the two commits."""
        self.logger.info(f"Getting changed files between {self.old_commit_hash} and {self.new_commit_hash}")

        try:
            # Get the list of changed files
            result = subprocess.run(
                ['git', 'diff', '--name-only', self.old_commit_hash, self.new_commit_hash],
                cwd=self.repo_checkout_dir,
                capture_output=True,
                text=True,
                check=True
            )
            
            all_changed_files = result.stdout.strip().split('\n') if result.stdout.strip() else []
            self.logger.info(f"Found {len(all_changed_files)} total changed files")

            # Apply exclude_directories filtering
            self.changed_files = self._filter_files_by_exclude_directories(all_changed_files)
            self.logger.info(f"After applying exclude_directories filter: {len(self.changed_files)} files remain")

            # Log the changed files for debugging
            for file_path in self.changed_files:
                self.logger.debug(f"Changed file: {file_path}")

            return self.changed_files
            
        except subprocess.CalledProcessError as e:
            self.logger.error(f"Failed to get changed files: {e}")
            raise

    def _filter_files_by_exclude_directories(self, files: List[str]) -> List[str]:
        """
        Filter out files that are in directories specified in exclude_directories config.
        Supports both directory names and relative paths.

        Args:
            files: List of file paths to filter

        Returns:
            List of file paths that are not in ignored directories
        """
        exclude_directories = self.config.get('exclude_directories', [])
        if not exclude_directories:
            return files

        filtered_files = []
        ignored_count = 0

        for file_path in files:
            # Normalize file path (remove leading ./ if present, but preserve leading dots for directories like .git)
            if file_path.startswith('./'):
                normalized_path = file_path[2:]  # Remove only the './' prefix
            else:
                normalized_path = file_path

            # Check if file should be excluded
            if self._is_file_in_excluded_directory(normalized_path, exclude_directories):
                ignored_count += 1
                self.logger.debug(f"Ignoring file in excluded directory: {file_path}")
            else:
                filtered_files.append(file_path)

        if ignored_count > 0:
            self.logger.info(f"Filtered out {ignored_count} files from ignored directories: {exclude_directories}")

        return filtered_files

    def _is_file_in_excluded_directory(self, file_path: str, exclude_patterns: List[str]) -> bool:
        """
        Check if a file is in an excluded directory.
        Supports both directory names and relative paths.

        Args:
            file_path: Normalized file path
            exclude_patterns: List of exclude patterns (directory names or relative paths)

        Returns:
            bool: True if file should be excluded, False otherwise
        """
        # Get the directory path of the file
        file_dir = '/'.join(file_path.split('/')[:-1]) if '/' in file_path else ''

        for exclude_pattern in exclude_patterns:
            # Case 1: Direct match with relative path (e.g., "Daemon/Shared")
            if file_dir == exclude_pattern or file_dir.startswith(exclude_pattern + '/'):
                return True

            # Case 2: Directory name matches (legacy behavior)
            # Split the file path and check if any directory component matches
            path_parts = file_path.split('/')[:-1]  # Exclude the filename itself
            for part in path_parts:
                if part.lower() == exclude_pattern.lower():
                    return True

        return False

    def checkout_commit(self, commit_hash: str) -> None:
        """
        Checkout a specific commit.

        Args:
            commit_hash: The commit hash to checkout
        """
        try:
            subprocess.run(['git', 'checkout', commit_hash],
                         cwd=self.repo_checkout_dir, check=True, capture_output=True)
            self.logger.info(f"Checked out commit {commit_hash}")
        except subprocess.CalledProcessError as e:
            self.logger.error(f"Failed to checkout commit {commit_hash}: {e}")
            raise

    def _clear_diff_output_directory(self) -> None:
        """
        Clear all contents in the diff output directory before starting analysis.
        This ensures a clean state for each analysis run.
        """
        success = clear_directory_contents(str(self.out_dir))
        if not success:
            self.logger.error(f"Failed to clear diff output directory: {self.out_dir}")
            raise RuntimeError(f"Could not clear diff output directory: {self.out_dir}")

    def _initialize_file_content_provider(self) -> None:
        """
        Initialize FileContentProvider for file resolution and reading capabilities.
        This enables tool requests for reading files during analysis.
        """
        try:
            # Check if FileContentProvider singleton already exists
            try:
                existing_provider = FileContentProvider.get()
                self.file_content_provider = existing_provider
                self.logger.info("Using existing FileContentProvider singleton")
                return
            except RuntimeError:
                # No existing singleton, create new one
                pass
            
            # Create FileContentProvider from repository using simplified API
            self.logger.info("Initializing FileContentProvider for diff analyzer")
            self.file_content_provider = FileContentProvider.from_repo(str(self.repo_checkout_dir))
            self.logger.info("FileContentProvider initialized successfully")
            
        except Exception as e:
            self.logger.warning(f"Failed to initialize FileContentProvider: {e}")
            self.file_content_provider = None

    def get_file_content_provider(self) -> Optional['FileContentProvider']:
        """
        Get the FileContentProvider instance.
        
        Returns:
            FileContentProvider instance or None if not initialized
        """
        return self.file_content_provider

    @abstractmethod
    def run_analysis(self) -> str:
        """
        Run the complete analysis workflow.
        This method must be implemented by subclasses.

        Returns:
            Path to the generated analysis report
        """
        pass