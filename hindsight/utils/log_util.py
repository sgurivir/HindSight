#!/usr/bin/env python3
# Created by Sridhar Gurivireddy
"""
Logging Utility Module
Provides centralized logging configuration for the entire application
"""

import os
import sys
import logging
import logging.handlers
from typing import Optional
from datetime import datetime
import tempfile

# Constants - Use temporary directory for default log file
def _get_default_log_file():
    """Get default log file path in temporary directory."""
    try:
        # Create a hindsight-specific temp directory
        system_temp = tempfile.gettempdir()

        # Create a unique hindsight directory based on current working directory
        cwd_hash = str(abs(hash(os.getcwd())))[:8]
        hindsight_temp_dir = os.path.join(system_temp, f"hindsight_{cwd_hash}")
        logs_dir = os.path.join(hindsight_temp_dir, "logs")

        # Create the directory if it doesn't exist
        os.makedirs(logs_dir, exist_ok=True)

        return os.path.join(logs_dir, f"hindsight_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")
    except Exception:
        # Fallback to current directory if temp directory creation fails
        return f"logs/hindsight_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

DEFAULT_LOG_FILE = _get_default_log_file()

def _get_artifacts_temp_subdir_path(repo_path: str, subdir: str, override_base_dir: str = None):
    """
    Local implementation to avoid circular import with file_util.
    Get path to a subdirectory within the artifacts temporary directory structure.
    """
    if override_base_dir:
        # Use the override base directory
        base_dir = override_base_dir
    else:
        # Use system temp directory with repo-specific hash
        system_temp = tempfile.gettempdir()
        repo_hash = str(abs(hash(os.path.abspath(repo_path))))[:8]
        base_dir = os.path.join(system_temp, f"artifacts_{repo_hash}")

    artifacts_dir = os.path.join(base_dir, subdir)
    os.makedirs(artifacts_dir, exist_ok=True)
    return artifacts_dir


class LogUtil:
    """Centralized logging configuration and management"""

    _configured = False
    _loggers = {}
    _is_worker_process = False  # Flag to indicate if this is a worker process
    _shared_log_file = None  # Shared log file path for worker processes

    @staticmethod
    def mark_as_worker_process():
        """
        Mark the current process as a worker process.
        This should be called in worker process initializers to prevent
        duplicate log file creation messages and other main-process-only behaviors.
        """
        LogUtil._is_worker_process = True

    @staticmethod
    def is_worker_process() -> bool:
        """Check if the current process is a worker process."""
        return LogUtil._is_worker_process

    @staticmethod
    def set_shared_log_file(log_file_path: str):
        """
        Set the shared log file path for worker processes.
        This should be called in worker process initializers to ensure
        all workers log to the same file as the main process.
        
        Args:
            log_file_path: Path to the log file that workers should use
        """
        LogUtil._shared_log_file = log_file_path

    @staticmethod
    def get_shared_log_file() -> Optional[str]:
        """
        Get the shared log file path for worker processes.
        
        Returns:
            The shared log file path, or None if not set
        """
        return LogUtil._shared_log_file

    @staticmethod
    def get_current_log_file() -> Optional[str]:
        """
        Get the current log file path being used by the root logger.
        
        Returns:
            The current log file path, or None if no file handler is configured
        """
        root_logger = logging.getLogger()
        for handler in root_logger.handlers:
            if isinstance(handler, (logging.FileHandler, logging.handlers.RotatingFileHandler)):
                return handler.baseFilename
        return None

    @staticmethod
    def setup_logging(
        log_file: Optional[str] = None,
        log_level: str = "INFO",
        console_level: str = "INFO",
        file_level: str = "DEBUG",
        log_format: Optional[str] = None,
        max_bytes: int = 10 * 1024 * 1024,  # 10MB
        backup_count: int = 5,
        create_dirs: bool = True
    ) -> logging.Logger:
        """
        Setup centralized logging configuration.

        Args:
            log_file (str, optional): Path to log file. If None, only console logging
            log_level (str): Overall log level
            console_level (str): Console handler log level
            file_level (str): File handler log level
            log_format (str, optional): Custom log format
            max_bytes (int): Maximum log file size before rotation
            backup_count (int): Number of backup files to keep
            create_dirs (bool): Whether to create log directory if it doesn't exist

        Returns:
            logging.Logger: Configured root logger
        """
        if LogUtil._configured:
            return logging.getLogger()

        # Clear any existing handlers
        root_logger = logging.getLogger()
        for handler in root_logger.handlers[:]:
            root_logger.removeHandler(handler)

        # Set default format if not provided
        if log_format is None:
            log_format = '%(asctime)s - %(name)s - %(levelname)s - %(filename)s:%(lineno)d - %(message)s'

        formatter = logging.Formatter(log_format)

        # Create console handler
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(getattr(logging, console_level.upper()))
        console_handler.setFormatter(formatter)
        root_logger.addHandler(console_handler)

        # Create file handler if log_file is specified
        if log_file:
            if create_dirs:
                log_dir = os.path.dirname(log_file)
                if log_dir:
                    os.makedirs(log_dir, exist_ok=True)

            # Use rotating file handler to prevent huge log files
            file_handler = logging.handlers.RotatingFileHandler(
                log_file,
                maxBytes=max_bytes,
                backupCount=backup_count,
                encoding='utf-8'
            )
            file_handler.setLevel(getattr(logging, file_level.upper()))
            file_handler.setFormatter(formatter)
            root_logger.addHandler(file_handler)
            
            # Log where the log file will be saved (only for main process, not workers)
            if not LogUtil._is_worker_process:
                print(f"Logs will be persisted to file: {log_file}")

        # Set root logger level to the most permissive level needed
        # This ensures all messages reach the handlers, which then filter appropriately
        min_level = min(
            getattr(logging, log_level.upper()),
            getattr(logging, console_level.upper()),
            getattr(logging, file_level.upper()) if log_file else getattr(logging, console_level.upper())
        )
        root_logger.setLevel(min_level)

        LogUtil._configured = True
        return root_logger

    @staticmethod
    def get_logger(name: str) -> logging.Logger:
        """
        Get a logger with the specified name.

        Args:
            name (str): Logger name (usually __name__)

        Returns:
            logging.Logger: Logger instance
        """
        if name not in LogUtil._loggers:
            LogUtil._loggers[name] = logging.getLogger(name)
        return LogUtil._loggers[name]



# Convenience functions for common logging operations
def setup_default_logging(log_file: str = None, repo_path: str = None, override_base_dir: str = None) -> logging.Logger:
    """
    Setup default logging configuration.

    Args:
        log_file: Path to log file. If None, uses temporary directory
        repo_path: Repository path for temporary directory structure
        override_base_dir: Override base directory for logs

    Returns:
        logging.Logger: Configured root logger
    """
    # For worker processes, use the shared log file if available
    if LogUtil.is_worker_process():
        shared_log = LogUtil.get_shared_log_file()
        if shared_log:
            log_file = shared_log
    
    if log_file is None:
        try:
            if repo_path:
                # Use repo-specific temporary directory
                temp_logs_dir = _get_artifacts_temp_subdir_path(repo_path, "logs", override_base_dir)
            else:
                # Use current working directory as fallback
                temp_logs_dir = _get_artifacts_temp_subdir_path(os.getcwd(), "logs", override_base_dir)
            log_file = os.path.join(temp_logs_dir, f"artifacts_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")
        except Exception:
            # Fallback to default if temp directory creation fails
            log_file = DEFAULT_LOG_FILE

    return LogUtil.setup_logging(
        log_file=log_file,
        log_level="INFO",
        console_level="INFO",
        file_level="DEBUG"
    )


def get_logger(name: str) -> logging.Logger:
    """
    Get a logger instance.

    Args:
        name: Logger name (usually __name__)

    Returns:
        logging.Logger: Logger instance
    """
    return LogUtil.get_logger(name)