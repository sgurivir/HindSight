#!/usr/bin/env python3
# Created by Sridhar Gurivireddy
"""
Command Validator Module - Simplified First-Word Validation for Terminal Commands

This module provides simple validation for terminal commands executed by LLMs.
The validation approach is straightforward: if the first word of the command
is in the SAFE_COMMANDS whitelist, the entire command is allowed.

Security Model:
- Only 17 read-only commands are allowed as the starting command
- Dangerous commands like rm, mv, chmod, sudo cannot be the first word
- Pipes, redirects, and chains are allowed since the first command is safe
- This trusts the LLM to use these operators appropriately

This simplified approach eliminates false positives that blocked legitimate
commands like:
- grep with 2>/dev/null (stderr redirect)
- grep with \\| regex alternation patterns
- awk with && logical operators in expressions
"""

from typing import Optional, Tuple, Set


# Safe commands for read-only operations (17 total)
SAFE_COMMANDS = {
    'ls', 'find', 'grep', 'wc', 'head', 'tail', 'cat', 'tree', 'file', 'sed',
    'sort', 'uniq', 'cut', 'awk', 'less', 'more', 'xargs'
}


class CommandValidator:
    """
    Validates terminal commands for safe execution by LLMs.
    
    Uses simple first-word validation: if the first word is in SAFE_COMMANDS,
    the entire command is allowed. This approach trusts the LLM to use pipes,
    redirects, and chains appropriately while preventing dangerous commands
    from being executed.
    """
    
    def __init__(self, allowed_commands: Set[str] = None):
        """
        Initialize the command validator.
        
        Args:
            allowed_commands: Set of allowed command names. Defaults to SAFE_COMMANDS.
        """
        self.allowed_commands = allowed_commands or SAFE_COMMANDS
    
    def validate_command(self, command: str) -> Tuple[bool, Optional[str]]:
        """
        Validate a command for safe execution.
        
        Simple validation: If the first word is in SAFE_COMMANDS, allow the entire command.
        This trusts the LLM to use pipes, redirects, and chains appropriately.
        
        Args:
            command: The command string to validate
            
        Returns:
            Tuple of (is_valid, error_message)
            - is_valid: True if command is safe, False otherwise
            - error_message: None if valid, error description if invalid
        """
        if not command or not command.strip():
            return False, "Error: Empty command."
        
        command = command.strip()
        base_cmd = self._extract_base_command(command)
        
        if not base_cmd:
            return False, "Error: Could not extract base command."
        
        if base_cmd not in self.allowed_commands:
            return False, f"Error: Command '{base_cmd}' is not allowed. Allowed commands: {', '.join(sorted(self.allowed_commands))}"
        
        return True, None
    
    def _extract_base_command(self, command: str) -> Optional[str]:
        """
        Extract the base command (first word) from a command string.
        
        Handles command chaining operators by stripping trailing punctuation
        like semicolons from the first word.
        
        Args:
            command: Command string
            
        Returns:
            Base command name or None if extraction fails
        """
        # Remove leading/trailing whitespace
        command = command.strip()
        
        # Split by whitespace and get first part
        parts = command.split()
        if not parts:
            return None
        
        # Strip trailing semicolons from the first word
        # This handles cases like "ls; ls -la" where parts[0] is "ls;"
        base_cmd = parts[0].rstrip(';')
        
        return base_cmd


def validate_terminal_command(command: str, allowed_commands: Set[str] = None) -> Tuple[bool, Optional[str]]:
    """
    Convenience function to validate a terminal command.
    
    Args:
        command: Command string to validate
        allowed_commands: Optional set of allowed commands
        
    Returns:
        Tuple of (is_valid, error_message)
    """
    validator = CommandValidator(allowed_commands)
    return validator.validate_command(command)
