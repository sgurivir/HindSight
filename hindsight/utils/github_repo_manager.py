import os
import re
import shutil
from typing import Optional, Tuple
import git
import requests
from hindsight.utils.log_util import get_logger

logger = get_logger(__name__)


class GitHubRepoManager:
    """
    Utility class for managing GitHub repository cloning and cleanup operations.
    """

    @staticmethod
    def parse_github_url(github_url: str) -> Tuple[str, str]:
        """
        Parse a GitHub URL to extract the user/org and repository name.

        Supports formats:
        - https://github.com/user/repo
        - https://github.com/user/repo.git
        - git@github.com:user/repo.git
        - git@github.com:user/repo

        Args:
            github_url: The GitHub repository URL

        Returns:
            Tuple of (user_org, repo_name)

        Raises:
            ValueError: If the URL format is not recognized
        """
        # Remove trailing .git if present
        url = github_url.strip()
        if url.endswith('.git'):
            url = url[:-4]

        # Pattern for HTTPS URLs: https://github.com/user/repo
        https_pattern = r'https?://github\.com/([^/]+)/([^/]+?)(?:\.git)?$'
        # Pattern for SSH URLs: git@github.com:user/repo
        ssh_pattern = r'git@github\.com:([^/]+)/([^/]+?)(?:\.git)?$'

        match = re.match(https_pattern, url)
        if match:
            user_org = match.group(1)
            repo_name = match.group(2)
            logger.info(f"Parsed HTTPS GitHub URL - User/Org: {user_org}, Repo: {repo_name}")
            return user_org, repo_name

        match = re.match(ssh_pattern, url)
        if match:
            user_org = match.group(1)
            repo_name = match.group(2)
            logger.info(f"Parsed SSH GitHub URL - User/Org: {user_org}, Repo: {repo_name}")
            return user_org, repo_name

        raise ValueError(f"Invalid GitHub URL format: {github_url}. "
                        "Supported formats: https://github.com/user/repo or git@github.com:user/repo")

    @staticmethod
    def get_repository_name(github_url: str) -> str:
        """
        Extract repository name from GitHub URL in format "user/repo".

        Args:
            github_url: The GitHub repository URL

        Returns:
            Repository name in format "user/repo"

        Raises:
            ValueError: If the URL format is not recognized
        """
        user_org, repo_name = GitHubRepoManager.parse_github_url(github_url)
        return f"{user_org}/{repo_name}"

    @staticmethod
    def get_clone_path(user_org: str, repo_name: str) -> str:
        """
        Generate the /tmp directory path for the cloned repository.

        Args:
            user_org: The GitHub user or organization name
            repo_name: The repository name

        Returns:
            The full path to the clone directory: /tmp/{user_org}_{repo_name}
        """
        clone_dir_name = f"{user_org}_{repo_name}"
        clone_path = os.path.join("/tmp", clone_dir_name)
        logger.info(f"Generated clone path: {clone_path}")
        return clone_path

    @staticmethod
    def repository_exists(clone_path: str) -> bool:
        """
        Check if a repository already exists at the given path.

        Args:
            clone_path: The path to check

        Returns:
            True if the directory exists and contains a .git folder, False otherwise
        """
        exists = os.path.isdir(clone_path) and os.path.isdir(os.path.join(clone_path, ".git"))
        if exists:
            logger.info(f"Repository already exists at: {clone_path}")
        else:
            logger.info(f"Repository does not exist at: {clone_path}")
        return exists

    @staticmethod
    def check_repository_size(user_org: str, repo_name: str, max_size_kb: int,
                            github_token: Optional[str] = None) -> None:
        """
        Check the size of a GitHub repository before cloning.

        Args:
            user_org: The GitHub user or organization name
            repo_name: The repository name
            max_size_kb: Maximum allowed repository size in KB
            github_token: Optional GitHub personal access token for authentication

        Raises:
            Exception: If the repository size exceeds the maximum allowed size
            Exception: If unable to retrieve repository information from GitHub API
        """
        api_url = f"https://api.github.com/repos/{user_org}/{repo_name}"

        headers = {}
        if github_token:
            headers["Authorization"] = f"token {github_token}"

        try:
            logger.info(f"Checking repository size for {user_org}/{repo_name}...")
            response = requests.get(api_url, headers=headers, timeout=10)

            if response.status_code == 404:
                raise Exception(f"Repository not found: {user_org}/{repo_name}. "
                              "Please check the URL or ensure you have access to this repository.")

            if response.status_code == 403:
                raise Exception(f"Access forbidden to repository: {user_org}/{repo_name}. "
                              "You may need to provide a valid GitHub token for private repositories.")

            if response.status_code != 200:
                raise Exception(f"Failed to retrieve repository information from GitHub API. "
                              f"Status code: {response.status_code}")

            repo_data = response.json()
            repo_size_kb = repo_data.get("size", 0)  # GitHub API returns size in KB

            logger.info(f"Repository size: {repo_size_kb} KB, Max allowed: {max_size_kb} KB")

            if repo_size_kb > max_size_kb:
                raise Exception(
                    f"Repository size ({repo_size_kb} KB) exceeds maximum allowed size ({max_size_kb} KB). "
                    f"Please choose a smaller repository or contact your administrator to increase the limit."
                )

            logger.info(f"Repository size check passed: {repo_size_kb} KB <= {max_size_kb} KB")

        except requests.exceptions.RequestException as e:
            logger.error(f"Network error while checking repository size: {e}")
            raise Exception(f"Failed to check repository size due to network error: {e}")

    @staticmethod
    def clone_repository(github_url: str, clone_path: str, github_token: Optional[str] = None) -> str:
        """
        Clone a GitHub repository to the specified path.

        Args:
            github_url: The GitHub repository URL
            clone_path: The local path where the repository should be cloned
            github_token: Optional GitHub personal access token for private repositories

        Returns:
            The path to the cloned repository

        Raises:
            Exception: If cloning fails
        """
        try:
            # If token is provided and URL is HTTPS, inject the token
            clone_url = github_url
            if github_token and github_url.startswith('https://'):
                # GitHub App tokens and PATs both work with x-access-token format
                # Format: https://x-access-token:{token}@github.com/user/repo.git
                clone_url = github_url.replace('https://github.com/', f'https://x-access-token:{github_token}@github.com/')
                if not clone_url.endswith('.git'):
                    clone_url += '.git'
                logger.info(f"Using authenticated HTTPS URL for cloning (token provided)")
            else:
                if not clone_url.endswith('.git'):
                    clone_url += '.git'
                logger.info(f"Cloning repository from: {github_url}")

            # Ensure parent directory exists
            os.makedirs(os.path.dirname(clone_path), exist_ok=True)

            # Clone the repository
            logger.info(f"Cloning to: {clone_path}")
            git.Repo.clone_from(clone_url, clone_path)
            logger.info(f"Successfully cloned repository to: {clone_path}")

            return clone_path

        except git.GitCommandError as e:
            logger.error(f"Git command error while cloning repository: {e}")
            raise Exception(f"Failed to clone repository: {e}")
        except Exception as e:
            logger.error(f"Unexpected error while cloning repository: {e}")
            raise Exception(f"Failed to clone repository: {e}")

    @staticmethod
    def cleanup_repository(clone_path: str) -> None:
        """
        Remove a cloned repository from the filesystem.

        Args:
            clone_path: The path to the cloned repository to remove
        """
        try:
            if os.path.exists(clone_path):
                logger.info(f"Cleaning up cloned repository at: {clone_path}")
                shutil.rmtree(clone_path)
                logger.info(f"Successfully removed repository at: {clone_path}")
            else:
                logger.warning(f"Repository path does not exist, nothing to cleanup: {clone_path}")
        except Exception as e:
            logger.error(f"Error cleaning up repository at {clone_path}: {e}")
            # Don't raise - cleanup failures shouldn't stop the process

    @staticmethod
    def get_or_clone_repository(github_url: str, github_token: Optional[str] = None,
                               max_size_kb: Optional[int] = None) -> str:
        """
        Get the path to a repository, cloning it if it doesn't exist.

        This is a convenience method that combines parsing, size checking, and cloning.

        Args:
            github_url: The GitHub repository URL
            github_token: Optional GitHub personal access token for private repositories
            max_size_kb: Optional maximum repository size in KB. If provided, checks size before cloning.

        Returns:
            The path to the repository (either existing or newly cloned)

        Raises:
            ValueError: If the URL format is invalid
            Exception: If cloning fails or repository size exceeds limit
        """
        # Parse the URL
        user_org, repo_name = GitHubRepoManager.parse_github_url(github_url)

        # Generate the clone path
        clone_path = GitHubRepoManager.get_clone_path(user_org, repo_name)

        # Check if repository already exists
        if GitHubRepoManager.repository_exists(clone_path):
            logger.info(f"Using existing repository at: {clone_path}")
            return clone_path

        # Check repository size before cloning if max_size_kb is specified
        if max_size_kb is not None:
            GitHubRepoManager.check_repository_size(user_org, repo_name, max_size_kb, github_token)

        # Clone the repository
        logger.info(f"Repository not found, cloning from: {github_url}")
        return GitHubRepoManager.clone_repository(github_url, clone_path, github_token)

