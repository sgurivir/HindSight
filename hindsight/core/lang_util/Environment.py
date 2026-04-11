#!/usr/bin/env python3
# Author: Sridhar Gurivireddy

import logging
import os
import platform
import shutil
import subprocess
import threading
from pathlib import Path
from clang import cindex


class Environment:
    """
    Environment utility class for managing system-specific configurations.
    """

    @staticmethod
    def get_libclang_path_from_platform():
        """Return libclang.dylib path if installed via brew or other platform package managers."""
        # Check environment variable first
        env_path = os.getenv("LIBCLANG_PATH")
        if env_path and os.path.exists(env_path):
            return env_path

        # MACOS - try brew
        try:
            prefix = subprocess.check_output(
                ["brew", "--prefix", "llvm"], text=True, stderr=subprocess.DEVNULL
            ).strip()
            candidate = Path(prefix) / "lib" / "libclang.dylib"
            if candidate.exists():
                return str(candidate)
        except (subprocess.CalledProcessError, FileNotFoundError):
            pass  # brew not available or llvm not installed via brew

        # macOS fallback
        if platform.system() == "Darwin":
            return "/opt/homebrew/opt/llvm/lib/libclang.dylib"

        return None

    @staticmethod
    def set_clang_path_from_brew_or_pip():
        """
        Set CLang path from environment variable.
        Always uses LIBCLANG_PATH environment variable.
        """
        logger = logging.getLogger(__name__)

        # Always use environment variable - throw exception if not set
        env_path = os.getenv("LIBCLANG_PATH")
        if not env_path:
            error_msg = (
                "LIBCLANG_PATH environment variable is not set. "
                "Please set LIBCLANG_PATH to the path of your libclang library file."
            )
            logger.error(error_msg)
            raise RuntimeError(error_msg)

        if not os.path.exists(env_path):
            error_msg = f"LIBCLANG_PATH environment variable points to non-existent file: {env_path}"
            logger.error(error_msg)
            raise RuntimeError(error_msg)

        try:
            cindex.Config.set_library_file(env_path)
            logger.debug(f"Using libclang from environment variable: {env_path}")
        except Exception as e:
            error_msg = f"Failed to set libclang from environment path {env_path}: {e}"
            logger.error(error_msg)
            raise RuntimeError(error_msg)


    _libclang_initialized = False
    _libclang_lock = None

    @staticmethod
    def initialize_libclang():
        """Initialize libclang path before any Clang operations. Uses dispatch_once pattern."""


        # Initialize lock if not already done
        if Environment._libclang_lock is None:
            Environment._libclang_lock = threading.Lock()

        # Use double-checked locking pattern for thread safety
        if not Environment._libclang_initialized:
            with Environment._libclang_lock:
                if not Environment._libclang_initialized:
                    Environment.set_clang_path_from_brew_or_pip()
                    Environment._libclang_initialized = True
