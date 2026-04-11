import argparse
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import traceback
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional, Dict, Any, Set

# Import the supported extensions
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))
from hindsight.core.constants import DEFAULT_DIFF_DAYS
from hindsight.core.lang_util.all_supported_extensions import ALL_SUPPORTED_EXTENSIONS

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class GitModifiedFunctionListGenerator:
    """
    A class to generate a list of modified functions from a git repository
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
        self.repo_path = os.path.abspath(repo_path) if repo_path else ""
        self.max_count = max_count
        self.default_days = default_days
        self.exclude_directories = exclude_directories or []
        self.artifacts_output_dir = artifacts_output_dir
        self.later_branch = later_branch
        self.earlier_branch = earlier_branch
        self.later_commit = later_commit
        self.earlier_commit = earlier_commit

        # Validate repository path only if provided and not empty
        if self.repo_path and not os.path.exists(self.repo_path):
            raise ValueError(f"Repository path does not exist: {self.repo_path}")
        if self.repo_path and not os.path.exists(os.path.join(self.repo_path, '.git')):
            raise ValueError(f"Not a git repository: {self.repo_path}")


    def clone_or_update_repo(self, repo_url: str, target_dir: Path) -> None:
        """Clone repository or update if it already exists."""
        if target_dir.exists():
            logger.info(f"Repository already exists at {target_dir}, updating...")
            try:
                logger.info("Running: git fetch --all")
                subprocess.run(['git', 'fetch', '--all'], cwd=target_dir, check=True)

                # Get the default branch name
                logger.info("Getting default branch name...")
                try:
                    result = subprocess.run(['git', 'symbolic-ref', 'refs/remotes/origin/HEAD'],
                                          cwd=target_dir, check=True, capture_output=True, text=True)
                    default_branch = result.stdout.strip().split('/')[-1]
                    logger.info(f"Default branch: {default_branch}")

                    # Checkout to default branch
                    logger.info(f"Running: git checkout {default_branch}")
                    subprocess.run(['git', 'checkout', default_branch], cwd=target_dir, check=True)

                    # Reset to latest fetched state (use HEAD instead of origin/branch)
                    logger.info("Running: git reset --hard HEAD")
                    subprocess.run(['git', 'reset', '--hard', 'HEAD'], cwd=target_dir, check=True)

                except subprocess.CalledProcessError:
                    # Fallback: just reset current branch
                    logger.info("Fallback: Running git reset --hard HEAD")
                    subprocess.run(['git', 'reset', '--hard', 'HEAD'], cwd=target_dir, check=True)

                logger.info("Repository update completed")
            except subprocess.CalledProcessError as e:
                logger.error(f"Failed to update repository: {e}")
                raise
        else:
            logger.info(f"Cloning repository {repo_url} to {target_dir}...")
            logger.info(f"Running: git clone {repo_url} {target_dir}")
            try:
                # Stream output to console for clone operation to show progress
                subprocess.run(['git', 'clone', repo_url, str(target_dir)], check=True)
                logger.info("Repository clone completed")
            except subprocess.CalledProcessError as e:
                logger.error(f"Failed to clone repository: {e}")
                raise

    def checkout_commit_or_branch(self, repo_dir: Path, commit_or_branch: str) -> None:
        """Checkout to specific commit or branch."""
        logger.info(f"Checking out to {commit_or_branch}...")
        try:
            subprocess.run(['git', 'checkout', commit_or_branch], cwd=repo_dir, check=True, capture_output=True)
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to checkout {commit_or_branch}: {e}")
            raise

    def get_top_commit(self, repo_dir: Path) -> str:
        """Get the top commit hash from the repository."""
        try:
            result = subprocess.run(
                ['git', 'rev-parse', 'HEAD'],
                cwd=repo_dir,
                check=True,
                capture_output=True,
                text=True
            )
            commit_hash = result.stdout.strip()
            logger.info(f"Top commit: {commit_hash}")
            return commit_hash
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to get top commit: {e}")
            raise

    def get_commit_date(self, repo_dir: Path, commit_hash: str) -> datetime:
        """Get the date of a specific commit."""
        try:
            result = subprocess.run(
                ['git', 'show', '-s', '--format=%ci', commit_hash],
                cwd=repo_dir,
                check=True,
                capture_output=True,
                text=True
            )
            date_str = result.stdout.strip()
            # Parse the git date format (YYYY-MM-DD HH:MM:SS +ZZZZ)
            commit_date = datetime.strptime(date_str[:19], '%Y-%m-%d %H:%M:%S')
            logger.info(f"Commit {commit_hash} date: {commit_date}")
            return commit_date
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to get commit date for {commit_hash}: {e}")
            raise

    def find_commit_by_days_ago(self, repo_dir: Path, target_date: datetime) -> str:
        """Find the top commit that was made on or before the target date."""
        try:
            # Format target date for git log
            target_date_str = target_date.strftime('%Y-%m-%d %H:%M:%S')

            result = subprocess.run(
                ['git', 'log', '--until', target_date_str, '--format=%H', '-n', '1'],
                cwd=repo_dir,
                check=True,
                capture_output=True,
                text=True
            )
            commit_hash = result.stdout.strip()
            if not commit_hash:
                raise ValueError(f"No commits found before {target_date_str}")

            logger.info(f"Found commit {commit_hash} for date {target_date_str}")
            return commit_hash
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to find commit for date {target_date}: {e}")
            raise

    def generate_function_signatures_subprocess(self, repo_dir: Path, output_file: Path) -> Dict[str, Any]:
        """Generate function signatures using subprocess to avoid libclang reinitialization issues."""
        logger.info(f"Generating function signatures for {repo_dir} using subprocess...")

        # Create a unique temporary directory for this subprocess to avoid conflicts
        with tempfile.TemporaryDirectory(prefix="ast_temp_") as temp_dir:
            temp_output = Path(temp_dir) / "functions_temp.json"

            # Build the command to run ast_function_signature_util.py as a module
            repo_root_dir = Path(__file__).parent.parent  # Go up from scm_tools to repo root

            cmd = [
                "python3", "-m", "hindsight.core.lang_util.ast_function_signature_util",
                "--repo", str(repo_dir),
                "--output", str(temp_output)
            ]

            # Add ignore directories if specified
            if self.exclude_directories:
                cmd.extend(["--ignore"] + self.exclude_directories)

            try:
                logger.info(f"Starting AST function signature generation subprocess...")
                logger.info(f"Running command: {' '.join(cmd)}")
                logger.info(f"Using temporary output: {temp_output}")
                logger.info(f"Working directory: {repo_root_dir}")
                logger.info(f"This may take several minutes for large repositories...")

                result = subprocess.run(
                    cmd,
                    check=True,
                    capture_output=True,
                    text=True,
                    cwd=repo_root_dir
                )

                logger.info(f"Subprocess completed successfully")
                logger.info(f"Generated function signatures to temporary file {temp_output}")

                # Load the generated JSON from temp file
                with open(temp_output, 'r', encoding='utf-8') as f:
                    json_data = json.load(f)

                # Copy to final output location
                with open(output_file, 'w', encoding='utf-8') as f:
                    json.dump(json_data, f, indent=2, sort_keys=True)

                logger.info(f"Copied results to final output: {output_file}")
                return json_data

            except subprocess.CalledProcessError as e:
                logger.error(f"Subprocess failed with exit code {e.returncode}")
                logger.error(f"STDOUT: {e.stdout}")
                logger.error(f"STDERR: {e.stderr}")
                raise
            except Exception as e:
                logger.error(f"Failed to generate function signatures via subprocess: {e}")
                raise

    def diff_function_signatures(self, earlier_json: Dict[str, Any], later_json: Dict[str, Any]) -> Dict[str, Any]:
        """Compare two function signature JSONs and return functions with changed checksums."""
        logger.info("Comparing function signatures...")

        # Handle flattened structure (direct function mapping)
        earlier_functions = earlier_json if isinstance(earlier_json, dict) else {}
        later_functions = later_json if isinstance(later_json, dict) else {}

        changed_functions = {}

        # Only check functions that exist in the later version
        for func_name, later_locations in later_functions.items():
            changed_locations = []

            if func_name in earlier_functions:
                earlier_func_info = earlier_functions[func_name]
                later_func_info = later_locations

                # Get checksums from both versions
                earlier_checksum = earlier_func_info.get("checksum") if isinstance(earlier_func_info, dict) else None
                later_checksum = later_func_info.get("checksum") if isinstance(later_func_info, dict) else None

                # Include function if checksums are different (content changed)
                if earlier_checksum != later_checksum:
                    # Use the first location from later version's code array
                    code_locations = later_func_info.get("code", [])
                    if code_locations:
                        changed_functions[func_name] = code_locations[0]
            else:
                # Function is completely new in later version
                # Use the first location from later version's code array
                code_locations = later_locations.get("code", [])
                if code_locations:
                    changed_functions[func_name] = code_locations[0]

        result = {
            "functions_modified": changed_functions
        }

        logger.info(f"Found {len(changed_functions)} functions with changes")

        return result

    def process_git_changes(self, repo_url: str, output_file: str) -> None:
        """Main processing logic for git function changes."""
        if not self.artifacts_output_dir:
            raise ValueError("artifacts_output_dir is required")

        # Create artifacts directory and git_changes_generator subdirectory
        artifacts_dir = Path(self.artifacts_output_dir)
        git_changes_dir = artifacts_dir / "git_changes_generator"
        git_changes_dir.mkdir(parents=True, exist_ok=True)

        repo_dir = git_changes_dir / "repo"

        try:
            # Clone or update repository
            self.clone_or_update_repo(repo_url, repo_dir)

            # Determine earlier and later references
            if self.earlier_branch and self.later_branch:
                earlier_ref = self.earlier_branch
                later_ref = self.later_branch
                logger.info(f"Using branches: {earlier_ref} -> {later_ref}")
            elif self.earlier_commit and self.later_commit:
                earlier_ref = self.earlier_commit
                later_ref = self.later_commit
                logger.info(f"Using commits: {earlier_ref} -> {later_ref}")
            else:
                # Use default behavior with days
                logger.info(f"No branch/commit args provided, using default behavior with {self.default_days} days")

                # Get the top commit (later commit)
                later_ref = self.get_top_commit(repo_dir)

                # Get the date of the top commit
                later_commit_date = self.get_commit_date(repo_dir, later_ref)

                # Calculate the target date (X days ago)
                target_date = later_commit_date - timedelta(days=self.default_days)

                # Find the commit from X days ago
                earlier_ref = self.find_commit_by_days_ago(repo_dir, target_date)

                logger.info(f"Using default commits: {earlier_ref} -> {later_ref} ({self.default_days} days apart)")

            # Generate function signatures for earlier version
            self.checkout_commit_or_branch(repo_dir, earlier_ref)
            earlier_json_path = git_changes_dir / "earlier_functions_checksum.json"
            earlier_result = self.generate_function_signatures_subprocess(repo_dir, earlier_json_path)

            # Generate function signatures for later version
            self.checkout_commit_or_branch(repo_dir, later_ref)
            later_json_path = git_changes_dir / "later_functions_checksum.json"
            later_result = self.generate_function_signatures_subprocess(repo_dir, later_json_path)

            # Compare and generate diff
            changed_functions = self.diff_function_signatures(earlier_result, later_result)

            # Write output
            output_path = Path(output_file)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            with open(output_path, 'w', encoding='utf-8') as f:
                json.dump(changed_functions, f, indent=2, sort_keys=True)

            logger.info(f"Successfully generated changed functions JSON to {output_path}")

        except Exception as e:
            logger.error(f"Failed to process git changes: {e}")
            raise


def main():
    """Main function with argparse to handle command line arguments."""
    parser = argparse.ArgumentParser(description='Generate list of modified functions from git repository')
    parser.add_argument('repo_url', help='URL to git repository')
    parser.add_argument('--exclude-directories', nargs='*', default=[],
                       help='Directories to exclude')
    parser.add_argument('--include-directories', nargs='*', default=[],
                       help='Directories to include (not implemented yet)')
    parser.add_argument('--config', help='Config file containing exclude_directories')
    parser.add_argument('--artifacts-output-dir', required=True,
                       help='Directory for output artifacts (required)')
    parser.add_argument('--later-branch', help='Later branch for comparison')
    parser.add_argument('--earlier-branch', help='Earlier branch for comparison')
    parser.add_argument('--later-commit', help='Later commit hash for comparison (highest priority)')
    parser.add_argument('--earlier-commit', help='Earlier commit hash for comparison (highest priority)')
    parser.add_argument('--days', type=int, default=DEFAULT_DIFF_DAYS,
                       help='Number of days to look back for comparison (used when no branch/commit args provided)')
    parser.add_argument('--out_functions_modified',
                       help='Output file path to write which functions were modified',
                       default="/tmp/functions_modified.json")

    args = parser.parse_args()

    # Validate arguments - allow --days as default option
    has_branch_args = args.earlier_branch and args.later_branch
    has_commit_args = args.earlier_commit and args.later_commit
    has_days_arg = args.days is not None

    if not (has_branch_args or has_commit_args or has_days_arg):
        parser.error("Either both --earlier-branch and --later-branch must be provided, or both --earlier-commit and --later-commit must be provided, or --days must be specified for default behavior")

    # If only partial branch/commit args are provided, that's an error
    if (args.earlier_branch and not args.later_branch) or (not args.earlier_branch and args.later_branch):
        parser.error("Both --earlier-branch and --later-branch must be provided together")

    if (args.earlier_commit and not args.later_commit) or (not args.earlier_commit and args.later_commit):
        parser.error("Both --earlier-commit and --later-commit must be provided together")

    # Load exclude directories from config if provided
    exclude_directories = args.exclude_directories
    if args.config:
        try:
            with open(args.config, 'r', encoding='utf-8') as f:
                config = json.load(f)
            if 'exclude_directories' in config:
                exclude_directories = config['exclude_directories']
                logger.info(f"Using exclude_directories from config: {exclude_directories}")
        except Exception as e:
            logger.error(f"Failed to load config file {args.config}: {e}")
            return 1

    try:
        # Create GitModifiedFunctionListGenerator instance
        generator = GitModifiedFunctionListGenerator(
            repo_path="",  # Not used in this context
            exclude_directories=exclude_directories,
            artifacts_output_dir=args.artifacts_output_dir,
            default_days=args.days,
            later_branch=args.later_branch,
            earlier_branch=args.earlier_branch,
            later_commit=args.later_commit,
            earlier_commit=args.earlier_commit
        )

        # Process git changes
        generator.process_git_changes(args.repo_url, args.out_functions_modified)

        logger.info("Successfully completed git function changes analysis")
        return 0

    except Exception as e:
        logger.error(f"Failed to process git function changes: {e}")
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    exit(main())
