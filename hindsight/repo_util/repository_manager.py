#!/usr/bin/env python3
"""
Repository Manager for handling different version control systems.
Supports Git and Perforce repositories for analyzing file changes between commits.
"""

import argparse
import json
import os
import subprocess
import sys
from abc import ABC, abstractmethod
from typing import List, Optional


class RepositoryVendor(ABC):
    """Abstract interface for repository vendor implementations."""

    @abstractmethod
    def is_repo_of_type(self, directory: str) -> bool:
        """
        Check if the given directory is a repository of this vendor type.

        Args:
            directory: Path to the directory to check

        Returns:
            True if directory is a repository of this type, False otherwise
        """
        pass

    @abstractmethod
    def files_changed(self, repo_path: str, commit_id1: str, commit_id2: str) -> List[str]:
        """
        Get list of files that changed between two commits.

        Args:
            repo_path: Path to the repository
            commit_id1: First commit ID
            commit_id2: Second commit ID

        Returns:
            List of file paths that changed between the commits
        """
        pass


class RepositoryVendorGit(RepositoryVendor):
    """Git repository vendor implementation."""

    def is_repo_of_type(self, directory: str) -> bool:
        """Check if directory is a Git repository."""
        git_dir = os.path.join(directory, '.git')
        return os.path.exists(git_dir)

    def files_changed(self, repo_path: str, commit_id1: str, commit_id2: str) -> List[str]:
        """Get files changed between two Git commits."""
        try:
            # Use git diff to get list of changed files
            cmd = ['git', 'diff', '--name-only', commit_id1, commit_id2]
            result = subprocess.run(
                cmd,
                cwd=repo_path,
                capture_output=True,
                text=True,
                check=True
            )

            # Filter out empty lines and return list of file paths
            files = [line.strip() for line in result.stdout.split('\n') if line.strip()]
            return files

        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"Git command failed: {e.stderr}")
        except Exception as e:
            raise RuntimeError(f"Error getting Git file changes: {str(e)}")


class RepositoryVendorPerforce(RepositoryVendor):
    """Perforce repository vendor implementation."""

    def is_repo_of_type(self, directory: str) -> bool:
        """Check if directory is a Perforce workspace."""
        # Check for P4CONFIG file or if p4 info works in this directory
        p4config_file = os.path.join(directory, '.p4config')
        if os.path.exists(p4config_file):
            return True

        try:
            # Try running p4 info to see if this is a valid Perforce workspace
            result = subprocess.run(
                ['p4', 'info'],
                cwd=directory,
                capture_output=True,
                text=True,
                timeout=10
            )
            return result.returncode == 0
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return False

    def files_changed(self, repo_path: str, commit_id1: str, commit_id2: str) -> List[str]:
        """Get files changed between two Perforce changelists."""
        try:
            # Use p4 filelog to get files changed between changelists
            # Note: In Perforce, commit_id1 and commit_id2 are changelist numbers
            cmd = ['p4', 'files', f'//...@{commit_id1},@{commit_id2}']
            result = subprocess.run(
                cmd,
                cwd=repo_path,
                capture_output=True,
                text=True,
                check=True
            )

            # Parse Perforce output to extract file paths
            files = []
            for line in result.stdout.split('\n'):
                if line.strip() and ' - ' in line:
                    # Perforce output format: "//depot/path/file.ext#revision - action"
                    file_path = line.split(' - ')[0].split('#')[0]
                    # Convert depot path to local path (simplified)
                    if file_path.startswith('//'):
                        # Remove depot prefix and convert to relative path
                        local_path = '/'.join(file_path.split('/')[3:])
                        if local_path:
                            files.append(local_path)

            return files

        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"Perforce command failed: {e.stderr}")
        except Exception as e:
            raise RuntimeError(f"Error getting Perforce file changes: {str(e)}")


class RepositoryManager:
    """Manager class for handling repository operations across different vendors."""

    def __init__(self, path_to_repo: str, commit_id_1: str, commit_id_2: str,
                 repository_vendor: Optional[RepositoryVendor] = None):
        """
        Initialize RepositoryManager.

        Args:
            path_to_repo: Path to the repository
            commit_id_1: First commit ID
            commit_id_2: Second commit ID
            repository_vendor: Repository vendor implementation (defaults to Git if None)
        """
        self.path_to_repo = path_to_repo
        self.commit_id_1 = commit_id_1
        self.commit_id_2 = commit_id_2

        if repository_vendor is None:
            self.repository_vendor = RepositoryVendorGit()
        else:
            self.repository_vendor = repository_vendor

    def _detect_repository_vendor(self) -> RepositoryVendor:
        """Auto-detect the repository vendor type."""
        vendors = [RepositoryVendorGit(), RepositoryVendorPerforce()]

        for vendor in vendors:
            if vendor.is_repo_of_type(self.path_to_repo):
                return vendor

        raise RuntimeError(f"Could not detect repository type for: {self.path_to_repo}")

    def get_files_changed(self) -> List[str]:
        """
        Get list of files that changed between the two commits.

        Returns:
            List of file paths that changed between commit_id_1 and commit_id_2
        """
        return self.repository_vendor.files_changed(
            self.path_to_repo,
            self.commit_id_1,
            self.commit_id_2
        )


def main():
    """Main function to run RepositoryManager from command line."""
    parser = argparse.ArgumentParser(
        description='Get files changed between two commits in a repository'
    )
    parser.add_argument(
        'repo',
        help='Path to the repository'
    )
    parser.add_argument(
        'commit_id_1',
        help='First commit ID or changelist number'
    )
    parser.add_argument(
        'commit_id_2',
        help='Second commit ID or changelist number'
    )
    parser.add_argument(
        '--vendor',
        choices=['git', 'perforce'],
        help='Repository vendor (defaults to git if not specified)'
    )
    parser.add_argument(
        '-j', '--json',
        help='Output JSON file path to write the list of changed files'
    )

    args = parser.parse_args()

    try:
        # Create vendor instance - default to Git if not specified
        vendor = RepositoryVendorGit()  # Default to Git
        if args.vendor == 'git':
            vendor = RepositoryVendorGit()
        elif args.vendor == 'perforce':
            vendor = RepositoryVendorPerforce()

        # Create RepositoryManager instance
        repo_manager = RepositoryManager(
            args.repo,
            args.commit_id_1,
            args.commit_id_2,
            vendor
        )

        # Get changed files
        changed_files = repo_manager.get_files_changed()

        # Output to JSON file or console
        if args.json:
            try:
                # Create JSON output
                json_output = {
                    "description": (f"Files changed between {args.commit_id_1} "
                                   f"and {args.commit_id_2}"),
                    "files": changed_files
                }

                with open(args.json, 'w', encoding='utf-8') as f:
                    json.dump(json_output, f, indent=2, ensure_ascii=False)
                print(f"JSON output written to: {args.json}")
            except IOError as e:
                print(f"Error writing to file {args.json}: {str(e)}", file=sys.stderr)
                sys.exit(1)
        else:
            # Console output
            if changed_files:
                print(f"Files changed between {args.commit_id_1} and {args.commit_id_2}:")
                for file_path in changed_files:
                    print(f"  {file_path}")
            else:
                print(f"No files changed between {args.commit_id_1} and {args.commit_id_2}")

    except Exception as e:
        print(f"Error: {str(e)}", file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()