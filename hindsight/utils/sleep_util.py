#!/usr/bin/env python3
"""
Sleep prevention utilities for macOS
Provides context managers and functions to prevent Mac from sleeping during long-running operations
"""

import sys
import signal
import subprocess
from typing import Optional
from contextlib import contextmanager
from .log_util import get_logger

logger = get_logger(__name__)


class SleepPrevention:
    """Class to manage sleep prevention on macOS using caffeinate."""

    def __init__(self, prevent_display_sleep: bool = True, prevent_system_sleep: bool = True, prevent_idle_sleep: bool = True):
        """
        Initialize sleep prevention settings.

        Args:
            prevent_display_sleep: Prevent display from sleeping (-d flag)
            prevent_system_sleep: Prevent system from sleeping (-s flag)
            prevent_idle_sleep: Prevent system from idle sleeping (-i flag)
        """
        self.prevent_display_sleep = prevent_display_sleep
        self.prevent_system_sleep = prevent_system_sleep
        self.prevent_idle_sleep = prevent_idle_sleep
        self.caffeinate_process: Optional[subprocess.Popen] = None

    def _build_caffeinate_args(self) -> list:
        """Build caffeinate command arguments based on settings."""
        args = ['caffeinate']

        flags = ''
        if self.prevent_idle_sleep:
            flags += 'i'
        if self.prevent_display_sleep:
            flags += 'd'
        if self.prevent_system_sleep:
            flags += 's'

        if flags:
            args.append(f'-{flags}')

        return args

    def start(self) -> bool:
        """
        Start sleep prevention.

        Returns:
            bool: True if successfully started, False otherwise
        """
        if self.caffeinate_process is not None:
            logger.warning("Sleep prevention is already active")
            return True

        try:
            args = self._build_caffeinate_args()
            logger.info(f"Starting sleep prevention with command: {' '.join(args)}")

            # Start caffeinate process
            self.caffeinate_process = subprocess.Popen(
                args,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )

            logger.info(f"Sleep prevention started (PID: {self.caffeinate_process.pid})")
            return True

        except Exception as e:
            logger.error(f"Failed to start sleep prevention: {e}")
            self.caffeinate_process = None
            return False

    def stop(self) -> bool:
        """
        Stop sleep prevention.

        Returns:
            bool: True if successfully stopped, False otherwise
        """
        if self.caffeinate_process is None:
            logger.debug("Sleep prevention is not active")
            return True

        try:
            logger.info(f"Stopping sleep prevention (PID: {self.caffeinate_process.pid})")

            # Terminate the caffeinate process
            self.caffeinate_process.terminate()

            # Wait for process to terminate (with timeout)
            try:
                self.caffeinate_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                logger.warning("Caffeinate process didn't terminate gracefully, killing it")
                self.caffeinate_process.kill()
                self.caffeinate_process.wait()

            self.caffeinate_process = None
            logger.info("Sleep prevention stopped")
            return True

        except Exception as e:
            logger.error(f"Failed to stop sleep prevention: {e}")
            return False

    def is_active(self) -> bool:
        """
        Check if sleep prevention is currently active.

        Returns:
            bool: True if active, False otherwise
        """
        if self.caffeinate_process is None:
            return False

        # Check if process is still running
        poll_result = self.caffeinate_process.poll()
        if poll_result is not None:
            # Process has terminated
            logger.warning(f"Caffeinate process terminated unexpectedly (exit code: {poll_result})")
            self.caffeinate_process = None
            return False

        return True


@contextmanager
def prevent_sleep(prevent_display_sleep: bool = True, prevent_system_sleep: bool = True, prevent_idle_sleep: bool = True):
    """
    Context manager to prevent Mac from sleeping during code execution.

    Args:
        prevent_display_sleep: Prevent display from sleeping
        prevent_system_sleep: Prevent system from sleeping
        prevent_idle_sleep: Prevent system from idle sleeping

    Usage:
        with prevent_sleep():
            # Your long-running code here
            time.sleep(3600)  # Sleep for 1 hour without Mac going to sleep
    """
    sleep_prevention = SleepPrevention(
        prevent_display_sleep=prevent_display_sleep,
        prevent_system_sleep=prevent_system_sleep,
        prevent_idle_sleep=prevent_idle_sleep
    )

    try:
        success = sleep_prevention.start()
        if not success:
            logger.warning("Failed to start sleep prevention, continuing without it")

        yield sleep_prevention

    finally:
        sleep_prevention.stop()


def run_with_sleep_prevention(func, *args, **kwargs):
    """
    Run a function with sleep prevention enabled.

    Args:
        func: Function to run
        *args: Arguments to pass to the function
        **kwargs: Keyword arguments to pass to the function

    Returns:
        The return value of the function
    """
    with prevent_sleep():
        return func(*args, **kwargs)


# Signal handler for graceful shutdown
def _signal_handler(signum, _, sleep_prevention_instance):
    """Handle signals to ensure sleep prevention is stopped."""
    logger.info(f"Received signal {signum}, stopping sleep prevention...")
    if sleep_prevention_instance:
        sleep_prevention_instance.stop()
    sys.exit(0)


def setup_signal_handlers(sleep_prevention_instance):
    """Set up signal handlers to ensure clean shutdown of sleep prevention."""
    def handler(signum, frame):
        _signal_handler(signum, frame, sleep_prevention_instance)

    signal.signal(signal.SIGINT, handler)   # Ctrl+C
    signal.signal(signal.SIGTERM, handler)  # Termination signal


if __name__ == "__main__":
    # Example usage
    print("Testing sleep prevention...")

    with prevent_sleep() as sp:
        print("Sleep prevention active. Your Mac should not sleep for the next 10 seconds.")
        print(f"Sleep prevention status: {sp.is_active()}")
        time.sleep(10)
        print("Test completed!")

    print("Sleep prevention disabled.")