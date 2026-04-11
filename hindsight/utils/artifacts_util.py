#!/usr/bin/env python3
# Created by Sridhar Gurivireddy
"""
Artifacts Utility Module

Provides utilities for managing artifacts directories:
- Platform-specific temp directory access
- Repository folder name extraction
- Artifacts directory path management
- Subdirectory creation and management
"""

import os
import tempfile
from typing import Optional

from .artifacts import get_repo_artifacts_dir
from .log_util import get_logger


logger = get_logger(__name__)


def get_platform_temp_dir() -> str:
    """
    Get the platform-specific temporary directory.

    Returns:
        str: Platform-specific temporary directory path
    """
    return tempfile.gettempdir()


def get_repository_folder_name(repo_path: str) -> str:
    """
    Get the repository folder name from the repository path.

    Args:
        repo_path (str): Path to the repository

    Returns:
        str: Repository folder name
    """
    return os.path.basename(os.path.abspath(repo_path))


def get_artifacts_temp_dir(repo_path: str, override_base_dir: str = None) -> str:
    """
    Get the artifacts directory structure:
    If override_base_dir is provided: <override_base_dir>/<repo_folder_name>
    Otherwise: Try to use OutputDirectoryProvider singleton, fallback to ~/hindsight_artifacts/<repo_folder_name>

    Args:
        repo_path (str): Path to the repository
        override_base_dir (str, optional): Override base directory instead of ~/hindsight_artifacts

    Returns:
        str: Path to the artifacts directory for this repository
    """
    if override_base_dir:
        base_dir = override_base_dir
        repo_folder_name = get_repository_folder_name(repo_path)
        artifacts_dir = os.path.join(base_dir, repo_folder_name)
    else:
        # Try to use OutputDirectoryProvider singleton first
        try:
            from .output_directory_provider import get_output_directory_provider
            output_provider = get_output_directory_provider()
            if output_provider.is_configured():
                artifacts_dir = output_provider.get_repo_artifacts_dir()
            else:
                # Fallback to default if singleton not configured
                artifacts_dir = get_repo_artifacts_dir(repo_path)
        except (RuntimeError, ImportError):
            # Fallback to default if singleton not available
            artifacts_dir = get_repo_artifacts_dir(repo_path)

    # Only create directory if it doesn't exist (don't create empty directories unnecessarily)
    if not os.path.exists(artifacts_dir):
        os.makedirs(artifacts_dir, exist_ok=True)

    return artifacts_dir


def get_artifacts_temp_file_path(repo_path: str, filename: str, override_base_dir: str = None) -> str:
    """
    Get the full path for a file in the artifacts directory.

    Args:
        repo_path (str): Path to the repository
        filename (str): Name of the file
        override_base_dir (str, optional): Override base directory instead of platform temp

    Returns:
        str: Full path to the file in the artifacts directory
    """
    artifacts_dir = get_artifacts_temp_dir(repo_path, override_base_dir)
    return os.path.join(artifacts_dir, filename)


def get_artifacts_temp_subdir_path(repo_path: str, subdir: str, override_base_dir: str = None) -> str:
    """
    Get the full path for a subdirectory in the artifacts directory.

    Args:
        repo_path (str): Path to the repository
        subdir (str): Name of the subdirectory
        override_base_dir (str, optional): Override base directory instead of platform temp

    Returns:
        str: Full path to the subdirectory in the artifacts directory
    """
    artifacts_dir = get_artifacts_temp_dir(repo_path, override_base_dir)
    subdir_path = os.path.join(artifacts_dir, subdir)

    # Ensure the subdirectory exists
    os.makedirs(subdir_path, exist_ok=True)

    return subdir_path


def ensure_artifacts_dir_exists(repo_path: str, override_base_dir: str = None) -> str:
    """
    Ensure the artifacts directory exists and return its path.
    
    Args:
        repo_path (str): Path to the repository
        override_base_dir (str, optional): Override base directory
        
    Returns:
        str: Path to the artifacts directory
    """
    artifacts_dir = get_artifacts_temp_dir(repo_path, override_base_dir)
    os.makedirs(artifacts_dir, exist_ok=True)
    return artifacts_dir


def get_code_insights_dir(repo_path: str, override_base_dir: str = None) -> str:
    """
    Get the code_insights subdirectory path within the artifacts directory.
    
    Args:
        repo_path (str): Path to the repository
        override_base_dir (str, optional): Override base directory
        
    Returns:
        str: Path to the code_insights directory
    """
    return get_artifacts_temp_subdir_path(repo_path, "code_insights", override_base_dir)


def get_trace_insights_dir(repo_path: str, override_base_dir: str = None) -> str:
    """
    Get the trace_insights subdirectory path within the artifacts directory.
    
    Args:
        repo_path (str): Path to the repository
        override_base_dir (str, optional): Override base directory
        
    Returns:
        str: Path to the trace_insights directory
    """
    return get_artifacts_temp_subdir_path(repo_path, "trace_insights", override_base_dir)


def get_diff_insights_dir(repo_path: str, override_base_dir: str = None) -> str:
    """
    Get the diff_insights subdirectory path within the artifacts directory.
    
    Args:
        repo_path (str): Path to the repository
        override_base_dir (str, optional): Override base directory
        
    Returns:
        str: Path to the diff_insights directory
    """
    return get_artifacts_temp_subdir_path(repo_path, "diff_insights", override_base_dir)