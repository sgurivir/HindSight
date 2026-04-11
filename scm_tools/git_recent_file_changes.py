import os
import subprocess
import json
import argparse
from typing import List, Optional
from datetime import datetime, timedelta

# Import the supported extensions
import sys
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))
from hindsight.core.lang_util.all_supported_extensions import ALL_SUPPORTED_EXTENSIONS
from hindsight.core.constants import DEFAULT_DIFF_DAYS


class GitModifiedFilesListGenerator:
    """
    A class to generate a list of modified files from a git repository
    based on specified criteria and time ranges.
    """

    def __init__(self, repo_path: str, max_count: Optional[int] = None,
                 default_days: int = DEFAULT_DIFF_DAYS, exclude_directories: Optional[List[str]] = None,
                 artifacts_output_dir: Optional[str] = None,
                 later_branch: Optional[str] = None, earlier_branch: Optional[str] = None,
                 later_commit: Optional[str] = None, earlier_commit: Optional[str] = None):
        """
        Initialize GitFileDiffGenerator with time-based, branch-based, or commit-based parameters.

        Args:
            repo_path: Path to the git repository
            max_count: Maximum number of files to return (None for no limit)
            default_days: Number of days to look back for modified files (used when commits/branches not specified)
            exclude_directories: List of directories to exclude
            artifacts_output_dir: Directory for output artifacts
            later_branch: Later branch for comparison (optional)
            earlier_branch: Earlier branch for comparison (optional)
            later_commit: Later commit hash for comparison (optional, highest priority)
            earlier_commit: Earlier commit hash for comparison (optional, highest priority)
        """
        self.repo_path = os.path.abspath(repo_path)
        self.max_count = max_count
        self.default_days = default_days
        self.exclude_directories = exclude_directories or []
        self.artifacts_output_dir = artifacts_output_dir
        self.later_branch = later_branch
        self.earlier_branch = earlier_branch
        self.later_commit = later_commit
        self.earlier_commit = earlier_commit

        # Validate repository path
        if not os.path.exists(self.repo_path):
            raise ValueError(f"Repository path does not exist: {self.repo_path}")
        if not os.path.exists(os.path.join(self.repo_path, '.git')):
            raise ValueError(f"Not a git repository: {self.repo_path}")

    def _run_git_command(self, command: List[str]) -> str:
        """Run a git command and return the output."""
        try:
            result = subprocess.run(
                command,
                cwd=self.repo_path,
                capture_output=True,
                text=True,
                check=True
            )
            return result.stdout.strip()
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"Git command failed: {' '.join(command)}\nError: {e.stderr}")

    def _get_modified_files_since_days(self, days: int) -> List[str]:
        """Get list of files modified in the last N days before the repository's last modification."""
        # Get the last commit date
        try:
            last_commit_command = ['git', 'log', '-1', '--format=%ci']
            last_commit_output = self._run_git_command(last_commit_command)
            if not last_commit_output:
                return []

            # Parse the last commit date
            last_commit_date = datetime.fromisoformat(last_commit_output.replace(' ', 'T', 1).rsplit(' ', 1)[0])

            # Handle edge case where days is 0 or very small
            if days <= 0:
                return []

            # Calculate the date range: from (last_commit_date - days) to last_commit_date
            since_date = (last_commit_date - timedelta(days=days)).strftime('%Y-%m-%d')
            until_date = last_commit_date.strftime('%Y-%m-%d')

            # Get files modified in the specified date range
            command = ['git', 'log', '--name-only', '--pretty=format:', f'--since={since_date}', f'--until={until_date}']
            output = self._run_git_command(command)

            if not output:
                return []

            # Filter out empty lines and get unique files
            files = [line.strip() for line in output.split('\n') if line.strip()]
            return list(set(files))  # Remove duplicates

        except Exception:
            return []

    def _get_modified_files_between_branches(self) -> List[str]:
        """Get list of files modified between two branches."""
        if not self.later_branch or not self.earlier_branch:
            return []

        # Get files that differ between the two branches
        command = ['git', 'diff', '--name-only', f'{self.earlier_branch}..{self.later_branch}']
        output = self._run_git_command(command)

        if not output:
            return []

        # Filter out empty lines and get unique files
        files = [line.strip() for line in output.split('\n') if line.strip()]
        return files

    def _get_modified_files_between_commits(self) -> List[str]:
        """Get list of files modified between two commits."""
        if not self.later_commit or not self.earlier_commit:
            return []

        # Get files that differ between the two commits
        command = ['git', 'diff', '--name-only', f'{self.earlier_commit}..{self.later_commit}']
        output = self._run_git_command(command)

        if not output:
            return []

        # Filter out empty lines and get unique files
        files = [line.strip() for line in output.split('\n') if line.strip()]
        return files

    def _filter_files_by_extension(self, files: List[str]) -> List[str]:
        """Filter files to only include those with supported extensions."""
        filtered_files = []
        for file_path in files:
            # Get file extension
            _, ext = os.path.splitext(file_path)
            if ext.lower() in ALL_SUPPORTED_EXTENSIONS:
                filtered_files.append(file_path)
        return filtered_files

    def _filter_files_by_exclude_directories(self, files: List[str]) -> List[str]:
        """Filter out files that are in excluded directories."""
        filtered_files = []
        for file_path in files:
            # Check if file is in any excluded directory
            is_excluded = False
            for exclude_dir in self.exclude_directories:
                if file_path.startswith(exclude_dir + '/') or file_path.startswith(exclude_dir + os.sep):
                    is_excluded = True
                    break

            if not is_excluded:
                filtered_files.append(file_path)

        return filtered_files

    def _get_modified_files_with_fallback(self) -> List[str]:
        """
        Get modified files with fallback logic:
        Priority order:
        1. Use commit hashes if provided
        2. If no commit hashes and branches are provided, use branch comparison
        3. Otherwise use time-based logic:
           - Try default_days
           - If no files, try default_days * 3
           - Keep backing up by 30 days until files are found
        """
        # Priority 1: If commit hashes are specified, use commit comparison
        if self.later_commit and self.earlier_commit:
            return self._get_modified_files_between_commits()

        # Priority 2: If branches are specified, use branch comparison
        if self.later_branch and self.earlier_branch:
            return self._get_modified_files_between_branches()

        # Priority 3: Use time-based fallback logic
        days_to_try = self.default_days

        # Handle edge case where default_days is 0 or negative
        if days_to_try <= 0:
            return []

        while True:
            files = self._get_modified_files_since_days(days_to_try)
            if files:
                return files

            # If no files found and we've tried the initial period, try 3x
            if days_to_try == self.default_days:
                days_to_try = self.default_days * 3
            else:
                # Keep backing up by 30 days
                days_to_try += 30

            # Safety check to avoid infinite loop (e.g., stop at 1 year)
            if days_to_try > 365:
                return []

    def _get_last_modification_date(self) -> Optional[str]:
        """Get the date when any file in the repository was last modified."""
        try:
            # Get the most recent commit date
            command = ['git', 'log', '-1', '--format=%ci']
            output = self._run_git_command(command)
            if output:
                # Parse the date and return in a readable format
                commit_date = datetime.fromisoformat(output.replace(' ', 'T', 1).rsplit(' ', 1)[0])
                return commit_date.strftime('%Y-%m-%d %H:%M:%S')
            return None
        except Exception:
            return None

    def generate(self, out_file: str) -> List[str]:
        """
        Generate the list of modified files and write to output file.

        Args:
            out_file: Path to output file where file paths will be written

        Returns:
            List[str]: List of relative file paths that were generated
        """
        # Get modified files with fallback logic
        modified_files = self._get_modified_files_with_fallback()

        if not modified_files:
            last_mod_date = self._get_last_modification_date()
            if last_mod_date:
                print(f"No modified files found in the repository. Last modification: {last_mod_date}")
            else:
                print("No modified files found in the repository.")
            # Create empty output file
            with open(out_file, 'w') as f:
                pass
            return []

        # Filter by supported extensions
        filtered_files = self._filter_files_by_extension(modified_files)

        # Filter out excluded directories
        filtered_files = self._filter_files_by_exclude_directories(filtered_files)

        # Apply max_count limit if specified
        if self.max_count and len(filtered_files) > self.max_count:
            filtered_files = filtered_files[:self.max_count]

        # Write to output file
        with open(out_file, 'w') as f:
            for file_path in filtered_files:
                f.write(file_path + '\n')

        print(f"Generated {len(filtered_files)} file paths in {out_file}")

        # Determine the context for the header
        if self.later_commit and self.earlier_commit:
            context = f"since commit {self.earlier_commit}..{self.later_commit}"
        elif self.later_branch and self.earlier_branch:
            context = f"since branch {self.earlier_branch}..{self.later_branch}"
        else:
            context = f"in last {self.default_days} days"

        # Always print directory structure to console
        self.print_files_as_directory_structure(filtered_files, context)

        # Return the list of filtered files
        return filtered_files

    def print_files_as_directory_structure(self, files: List[str], context: str = "") -> None:
        """
        Print files in a directory structure format with proper indentation.

        Args:
            files: List of file paths to organize and print
            context: Context description for the header (e.g., "in last 7 days" or "since branch main..feature")
        """
        if not files:
            print("No files to display")
            return

        # Build directory structure
        directory_tree = {}

        for file_path in files:
            parts = file_path.split('/')
            current_level = directory_tree

            # Navigate through directory parts
            for i, part in enumerate(parts):
                if i == len(parts) - 1:  # This is a file
                    if 'files' not in current_level:
                        current_level['files'] = []
                    current_level['files'].append(part)
                else:  # This is a directory
                    if part not in current_level:
                        current_level[part] = {}
                    current_level = current_level[part]

        # Print the directory structure with context-aware header
        if context:
            print(f"\nFiles changed ({context}) - {len(files)} files:")
        else:
            print(f"\nDirectory structure ({len(files)} files):")
        print("=" * 50)
        self._print_directory_level(directory_tree, 0)

    def _print_directory_level(self, level: dict, indent: int) -> None:
        """
        Recursively print directory structure with indentation.

        Args:
            level: Current directory level dictionary
            indent: Current indentation level
        """
        indent_str = "  " * indent

        # Print directories first
        for key, value in sorted(level.items()):
            if key != 'files' and isinstance(value, dict):
                print(f"{indent_str}{key}/")
                self._print_directory_level(value, indent + 1)

        # Print files in this directory
        if 'files' in level:
            for file_name in sorted(level['files']):
                print(f"{indent_str}{file_name}")

    def generate_and_print_structure(self, out_file: str) -> List[str]:
        """
        Generate the list of modified files, write to output file, and print as directory structure.

        Args:
            out_file: Path to output file where file paths will be written

        Returns:
            List[str]: List of relative file paths that were generated
        """
        # Generate the file list using existing method (which already prints structure)
        filtered_files = self.generate(out_file)

        return filtered_files


def _load_exclude_directories_from_config(config_file: str) -> List[str]:
    """Load exclude_directories from config file."""
    try:
        with open(config_file, 'r') as f:
            config = json.load(f)
        return config.get('exclude_directories', [])
    except (FileNotFoundError, json.JSONDecodeError) as e:
        raise ValueError(f"Error loading config file {config_file}: {e}")


def main():
    """Main function with argparse to handle command line arguments."""
    parser = argparse.ArgumentParser(description='Generate list of modified files from git repository')
    parser.add_argument('repo_path', help='Path to git repository')
    parser.add_argument('--max-count', type=int, help='Maximum number of files to include')
    parser.add_argument('--default-days', type=int, default=DEFAULT_DIFF_DAYS,
                       help=f'Number of days to look back for modified files (default: {DEFAULT_DIFF_DAYS})')
    parser.add_argument('--exclude-directories', nargs='*', default=[],
                       help='Directories to exclude')
    parser.add_argument('--config', help='Config file containing exclude_directories')
    parser.add_argument('--artifacts-output-dir', help='Directory for output artifacts')
    parser.add_argument('--later-branch', help='Later branch for comparison')
    parser.add_argument('--earlier-branch', help='Earlier branch for comparison')
    parser.add_argument('--later-commit', help='Later commit hash for comparison (highest priority)')
    parser.add_argument('--earlier-commit', help='Earlier commit hash for comparison (highest priority)')
    parser.add_argument('--out_files_modified', help='Output file path to write which files were modified')
    parser.add_argument('--out_functions_modified', help='Output file path to write which functions were modified')

    args = parser.parse_args()

    try:
        # Load exclude_directories from config if provided
        exclude_directories = args.exclude_directories
        if args.config:
            exclude_directories = _load_exclude_directories_from_config(args.config)

        # Create GitFileDiffGenerator instance
        generator = GitModifiedFilesListGenerator(
            repo_path=args.repo_path,
            max_count=args.max_count,
            default_days=args.default_days,
            exclude_directories=exclude_directories,
            artifacts_output_dir=args.artifacts_output_dir,
            later_branch=args.later_branch,
            earlier_branch=args.earlier_branch,
            later_commit=args.later_commit,
            earlier_commit=args.earlier_commit
        )

        # Generate the file list
        generator.generate(args.out_files_modified)

    except Exception as e:
        print(f"Error: {e}")
        return 1

    return 0


if __name__ == '__main__':
    exit(main())