#!/usr/bin/env python3
# Created by Sridhar Gurivireddy
"""
Terminal Tools Module - Terminal command execution tools.

This module provides tools for:
- runTerminalCmd: Execute safe terminal commands with validation
"""

import os
import subprocess
import time
from datetime import datetime
from typing import Optional

from ....utils.output_directory_provider import get_output_directory_provider
from ....utils.log_util import get_logger
from .base import ToolsBase


logger = get_logger(__name__)


class TerminalToolsMixin:
    """
    Mixin class providing terminal command tool implementations.
    
    This mixin should be used with ToolsBase to provide terminal-related tools:
    - execute_terminal_cmd_tool
    """

    def _validate_command_safety(self: ToolsBase, command: str) -> Optional[str]:
        """
        Validate command safety using enhanced CommandValidator.
        
        Checks for:
        - Allowed commands (whitelist)
        - Pipe chains (validates each command in chain)
        - Command chains (;, &&, ||)
        - Output redirects (>, >>, 2>, &>)

        Args:
            command: Command to validate

        Returns:
            str: Error message if unsafe, None if safe
        """
        # Use enhanced command validator
        is_valid, error_message = self.command_validator.validate_command(command)
        
        if not is_valid:
            logger.warning(f"[TOOL] runTerminalCmd - Command validation failed: {error_message}")
            return error_message
        
        return None  # Command is safe

    def _log_terminal_command(self: ToolsBase, command: str, blocked: bool = False, failed: bool = False) -> None:
        """
        Log terminal command to {artifacts_dir}/{repo_name}/terminal_commands.txt
        
        Args:
            command: The command being executed or blocked
            blocked: True if command was blocked by validation, False if executed
            failed: True if command returned non-zero exit code, False otherwise
        """
        try:
            # Determine artifacts directory
            if self.artifacts_dir:
                # artifacts_dir is typically {base}/opencv/code_insights
                # We want to log to {base}/opencv/terminal_commands.txt
                # So go up one level from artifacts_dir
                artifacts_path = os.path.dirname(self.artifacts_dir)
            else:
                output_provider = get_output_directory_provider()
                artifacts_path = output_provider.get_repo_artifacts_dir()
            
            # Create directory if needed
            os.makedirs(artifacts_path, exist_ok=True)
            
            # Log file path: {artifacts_dir}/{repo_name}/terminal_commands.txt
            log_file = os.path.join(artifacts_path, "terminal_commands.txt")
            
            # Format: [timestamp] command, [timestamp] BLOCKED - command, or [timestamp] FAILED - command
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
            if blocked:
                log_entry = f"[{timestamp}] BLOCKED - {command}\n"
            elif failed:
                log_entry = f"[{timestamp}] FAILED - {command}\n"
            else:
                log_entry = f"[{timestamp}] {command}\n"
            
            # Append to file
            with open(log_file, 'a', encoding='utf-8') as f:
                f.write(log_entry)
            
            status = 'BLOCKED' if blocked else ('FAILED' if failed else 'EXECUTED')
            logger.debug(f"[TOOL] Logged command to {log_file}: {status}")
            
        except Exception as e:
            # Non-blocking: log error but don't fail command execution
            logger.warning(f"[TOOL] Failed to log command to file: {e}")

    def execute_terminal_cmd_tool(self: ToolsBase, command: str, reason: str = None) -> str:
        """
        Execute terminal command tool safely with comprehensive logging.

        Args:
            command: Command to execute
            reason: Reason why this tool is being used (optional for backward compatibility)

        Returns:
            str: Command output or error message
        """
        start_time = time.time()
        self.tool_usage_stats['runTerminalCmd']['count'] += 1

        logger.info(f"[TOOL] runTerminalCmd called #{self.tool_usage_stats['runTerminalCmd']['count']} - Command: {command}")
        logger.info(f"[AI REASONING] {reason if reason else 'No reason provided'}")

        try:
            # Validate command safety
            validation_error = self._validate_command_safety(command)
            if validation_error:
                # Log blocked command
                self._log_terminal_command(command, blocked=True)
                return validation_error

            # Hack - allow following command
            # grep -A 10 -B 2 "- (void)setSourceTime:" daemon/TMDaemonCore.m
            original_command = command  # Save original for logging
            if command.startswith("grep") and ' "-' in command:
                command = command.replace(' "- ', ' -- "- ', 1)

            # Execute command with timeout
            logger.debug(f"[TOOL] runTerminalCmd - Executing in directory: {self.repo_path}")
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=30,
                cwd=self.repo_path
            )

            output = result.stdout
            if result.stderr:
                output += f"\nStderr: {result.stderr}"

            if result.returncode != 0:
                output += f"\nExit code: {result.returncode}"
                # Log failed command (non-zero return code)
                self._log_terminal_command(original_command, failed=True)
            else:
                # Log successful command
                self._log_terminal_command(original_command, blocked=False)

            final_result = f"Command: {command}\n{output}"

            # Update statistics
            self.tool_usage_stats['runTerminalCmd']['total_chars'] += len(final_result)
            self.tool_usage_stats['runTerminalCmd']['commands_executed'].append(command)

            execution_time = time.time() - start_time
            logger.info(f"[TOOL] runTerminalCmd completed - Command: {command}, "
                       f"Exit code: {result.returncode}, Output: {len(final_result)} chars, "
                       f"Time: {execution_time:.2f}s")

            return final_result

        except subprocess.TimeoutExpired:
            execution_time = time.time() - start_time
            error_msg = f"Error: Command '{command}' timed out after 30 seconds."
            logger.error(f"[TOOL] runTerminalCmd timeout - Command: {command}, Time: {execution_time:.2f}s")
            return error_msg
        except Exception as e:
            execution_time = time.time() - start_time
            error_msg = f"Error executing command '{command}': {str(e)}"
            logger.error(f"[TOOL] runTerminalCmd failed - Command: {command}, Error: {str(e)}, Time: {execution_time:.2f}s")
            return error_msg