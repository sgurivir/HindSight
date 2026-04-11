#!/usr/bin/env python3
# Created by Sridhar Gurivireddy
"""
Utility functions for managing Hindsight artifacts directory paths
"""

import os
from ..core.constants import REPO_IQ_ARTIFACTS_DIR


def get_artifacts_dir():
    """
    Returns the full expanded path to the artifacts directory.

    Returns:
        str: Full path to ~/REPO_IQ_ARTIFACTS_DIR directory
    """
    return os.path.expanduser(f"~/{REPO_IQ_ARTIFACTS_DIR}")


def get_repo_artifacts_dir(repo_path, custom_base_dir=None):
    """
    Returns the full expanded path to a specific repository's artifacts directory.

    Args:
        repo_path (str): Path to the repository
        custom_base_dir (str, optional): Custom base directory to use instead of REPO_IQ_ARTIFACTS_DIR

    Returns:
        str: Full path to hindsight_artifacts/<repo_name> directory
    """
    repo_name = os.path.basename(repo_path.rstrip('/'))

    if custom_base_dir:
        # Use custom base directory
        base_dir = os.path.expanduser(custom_base_dir)
        return os.path.join(base_dir, repo_name)
    else:
        # Use default REPO_IQ_ARTIFACTS_DIR
        return os.path.join(get_artifacts_dir(), repo_name)