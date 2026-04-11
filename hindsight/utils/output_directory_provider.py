#!/usr/bin/env python3
"""
OutputDirectoryProvider - Singleton for managing output directory configuration
"""

from typing import Optional
from threading import Lock

from .artifacts import get_repo_artifacts_dir


class OutputDirectoryProvider:
    """
    Singleton class that provides centralized access to output directory configuration.
    This eliminates the need to pass custom_base_dir parameters throughout the codebase.
    """

    _instance: Optional['OutputDirectoryProvider'] = None
    _lock = Lock()

    def __new__(cls) -> 'OutputDirectoryProvider':
        """Ensure only one instance exists (singleton pattern)."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        """Initialize the provider if not already initialized."""
        if not getattr(self, '_initialized', False):
            self._repo_path: Optional[str] = None
            self._custom_base_dir: Optional[str] = None
            self._initialized = True

    def configure(self, repo_path: str, custom_base_dir: Optional[str] = None) -> None:
        """
        Configure the output directory provider with repository path and optional custom base directory.

        Args:
            repo_path: Path to the repository
            custom_base_dir: Optional custom base directory from outputDirectory config
        """
        with self._lock:
            self._repo_path = repo_path
            self._custom_base_dir = custom_base_dir

    def get_repo_artifacts_dir(self, repo_path: Optional[str] = None) -> str:
        """
        Get the repository artifacts directory, using configured custom base directory if available.

        Args:
            repo_path: Optional repository path (uses configured path if not provided)

        Returns:
            str: Path to the repository artifacts directory

        Raises:
            RuntimeError: If provider is not configured and no repo_path is provided
        """
        effective_repo_path = repo_path or self._repo_path
        if not effective_repo_path:
            raise RuntimeError("OutputDirectoryProvider not configured and no repo_path provided")

        return get_repo_artifacts_dir(effective_repo_path, self._custom_base_dir)

    def get_custom_base_dir(self) -> Optional[str]:
        """
        Get the configured custom base directory.

        Returns:
            Optional[str]: Custom base directory or None if not configured
        """
        return self._custom_base_dir

    def is_configured(self) -> bool:
        """
        Check if the provider has been configured.

        Returns:
            bool: True if configured, False otherwise
        """
        return self._repo_path is not None

    def reset(self) -> None:
        """Reset the provider configuration (mainly for testing)."""
        with self._lock:
            self._repo_path = None
            self._custom_base_dir = None

    @classmethod
    def get_instance(cls) -> 'OutputDirectoryProvider':
        """
        Get the singleton instance.

        Returns:
            OutputDirectoryProvider: The singleton instance
        """
        return cls()


# Convenience function for easy access
def get_output_directory_provider() -> OutputDirectoryProvider:
    """
    Get the OutputDirectoryProvider singleton instance.

    Returns:
        OutputDirectoryProvider: The singleton instance
    """
    return OutputDirectoryProvider.get_instance()