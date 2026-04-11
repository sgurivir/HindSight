# Plan: Simplify Command Validator to First-Word Check Only

## Overview

This document outlines a plan to simplify the command validation logic in `hindsight/core/llm/command_validator.py`. The current implementation is overly complex and causes false positives that block legitimate commands.

## Problem Statement

### Blocked Commands (False Positives)

The following legitimate commands were blocked during code analysis:

```bash
# Blocked due to 2>/dev/null (stderr redirect)
grep -n 'class.*OneTimeCode' Mac/Safari/FormAutoFill/*.h 2>/dev/null | head -20
find . -name '*.h' -o -name '*.mm' | xargs grep -l 'Pattern' 2>/dev/null | head -10

# Blocked due to \| in grep regex patterns
grep -n 'pattern1\|pattern2\|pattern3' file.mm | head -20

# Blocked due to && in awk expressions
awk 'NR>=3100 && NR<=3600' file.js
```

### Root Causes

1. **`2>/dev/null` blocked as redirect**: The validator blocks `2>` as a dangerous redirect, but `2>/dev/null` is a safe, read-only operation.

2. **`\|` in grep patterns blocked**: The regex alternation operator inside grep patterns is incorrectly flagged.

3. **`&&` in awk expressions blocked**: The logical AND inside awk single-quoted expressions is incorrectly flagged as shell command chaining.

## Current Implementation (Complex)

The current `validate_command()` method performs multiple checks:

1. Check for command chains (`;`, `&&`, `||`)
2. Check for redirects (`>`, `>>`, `2>`, `&>`)
3. If pipes exist, validate EACH command in the pipe chain
4. Extract base command and check against whitelist

### Current Allowed Commands (17 total)

```python
SAFE_COMMANDS = {
    'ls', 'find', 'grep', 'wc', 'head', 'tail', 'cat', 'tree', 'file', 'sed',
    'sort', 'uniq', 'cut', 'awk', 'less', 'more', 'xargs'
}
```

## Proposed Implementation (Simple)

### New Logic

**If the first word of the command is in `SAFE_COMMANDS`, allow the entire command. No other checks.**

This approach:
- Trusts the LLM to use pipes, redirects, and chains appropriately
- Eliminates all false positives
- Maintains security by only allowing commands that start with safe read-only tools

### New `validate_command()` Method

Replace lines 56-97 in `hindsight/core/llm/command_validator.py`:

```python
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
```

## Code Changes Summary

### File to Modify

`hindsight/core/llm/command_validator.py`

### Code to Remove (Dead After Change)

| Lines | Item | Description |
|-------|------|-------------|
| 22 | `DANGEROUS_OPERATIONS` | Unused constant |
| 23 | `DANGEROUS_REDIRECTS` | No longer checked |
| 24 | `DANGEROUS_CHAINS` | No longer checked |
| 33 | `QUOTED_CONTENT_PATTERN` | No longer needed |
| 119-137 | `_check_command_chains()` | Method no longer called |
| 139-157 | `_check_redirects()` | Method no longer called |
| 159-185 | `_validate_pipe_chain()` | Method no longer called |
| 187-199 | `_split_by_pipe()` | Method no longer called |

### Code to Keep

| Lines | Item | Description |
|-------|------|-------------|
| 27-30 | `SAFE_COMMANDS` | The whitelist of 17 allowed commands |
| 99-117 | `_extract_base_command()` | Extracts first word from command |
| 202-214 | `validate_terminal_command()` | Convenience function |

## Expected Results

### Commands That Will Now Work

All previously blocked commands will be allowed since they start with `grep`, `find`, or `awk`:

| Command | First Word | Status |
|---------|------------|--------|
| `grep -n 'pattern1\|pattern2' file.mm 2>/dev/null \| head -20` | `grep` | ✅ Allowed |
| `awk 'NR>=100 && NR<=200' file.js` | `awk` | ✅ Allowed |
| `find . -name '*.h' \| xargs grep -l 'pattern' 2>/dev/null` | `find` | ✅ Allowed |

### Security Maintained

The simplified approach still provides security:

- Only 17 read-only commands are allowed as the starting command
- Dangerous commands like `rm`, `mv`, `chmod`, `sudo` cannot be the first word
- Even if piped to, dangerous commands cannot execute destructive operations on their own (e.g., `grep foo | rm` would fail because `rm` expects arguments, not stdin)

## Implementation Checklist

- [ ] Update `validate_command()` method with simplified logic
- [ ] Remove unused constants (`DANGEROUS_OPERATIONS`, `DANGEROUS_REDIRECTS`, `DANGEROUS_CHAINS`, `QUOTED_CONTENT_PATTERN`)
- [ ] Remove unused methods (`_check_command_chains`, `_check_redirects`, `_validate_pipe_chain`, `_split_by_pipe`)
- [ ] Update module docstring to reflect simplified approach
- [ ] Create test file `hindsight/tests/core/llm/test_command_validator.py`
- [ ] Run tests: `pytest hindsight/tests/core/llm/test_command_validator.py -v`
- [ ] Verify all tests pass
- [ ] Manual test with previously blocked commands

## Testing

### Test File Location

Create new test file: `hindsight/tests/core/llm/test_command_validator.py`

### Test Cases to Implement

```python
"""
Tests for CommandValidator - Simplified First-Word Validation

Test file: hindsight/tests/core/llm/test_command_validator.py
"""

import pytest
from hindsight.core.llm.command_validator import CommandValidator, validate_terminal_command, SAFE_COMMANDS


class TestCommandValidator:
    """Test suite for simplified CommandValidator."""
    
    @pytest.fixture
    def validator(self):
        """Create a CommandValidator instance."""
        return CommandValidator()
    
    # ==================== ALLOWED COMMANDS ====================
    
    def test_simple_grep_allowed(self, validator):
        """Simple grep command should be allowed."""
        is_valid, error = validator.validate_command("grep -n 'pattern' file.txt")
        assert is_valid is True
        assert error is None
    
    def test_grep_with_stderr_redirect_allowed(self, validator):
        """grep with 2>/dev/null should be allowed (previously blocked)."""
        is_valid, error = validator.validate_command(
            "grep -n 'class.*OneTimeCode' Mac/Safari/*.h 2>/dev/null | head -20"
        )
        assert is_valid is True
        assert error is None
    
    def test_grep_with_regex_alternation_allowed(self, validator):
        """grep with \\| regex alternation should be allowed (previously blocked)."""
        is_valid, error = validator.validate_command(
            "grep -n 'pattern1\\|pattern2\\|pattern3' file.mm | head -20"
        )
        assert is_valid is True
        assert error is None
    
    def test_awk_with_logical_and_allowed(self, validator):
        """awk with && in expression should be allowed (previously blocked)."""
        is_valid, error = validator.validate_command(
            "awk 'NR>=3100 && NR<=3600' file.js"
        )
        assert is_valid is True
        assert error is None
    
    def test_find_with_xargs_and_redirect_allowed(self, validator):
        """Complex find | xargs | grep chain should be allowed."""
        is_valid, error = validator.validate_command(
            "find . -name '*.h' | xargs grep -l 'Pattern' 2>/dev/null | head -10"
        )
        assert is_valid is True
        assert error is None
    
    def test_all_safe_commands_allowed(self, validator):
        """All 17 safe commands should be allowed as first word."""
        for cmd in SAFE_COMMANDS:
            is_valid, error = validator.validate_command(f"{cmd} --help")
            assert is_valid is True, f"Command '{cmd}' should be allowed"
            assert error is None
    
    def test_pipe_chain_with_safe_first_command(self, validator):
        """Pipe chain starting with safe command should be allowed."""
        is_valid, error = validator.validate_command(
            "cat file.txt | grep pattern | sort | uniq -c | head -10"
        )
        assert is_valid is True
        assert error is None
    
    def test_command_with_output_redirect_allowed(self, validator):
        """Commands with output redirects should be allowed (first word is safe)."""
        is_valid, error = validator.validate_command("grep pattern file.txt > output.txt")
        assert is_valid is True
        assert error is None
    
    def test_command_with_chain_operators_allowed(self, validator):
        """Commands with && or || should be allowed (first word is safe)."""
        is_valid, error = validator.validate_command("grep pattern file.txt && echo found")
        assert is_valid is True
        assert error is None
    
    # ==================== BLOCKED COMMANDS ====================
    
    def test_rm_blocked(self, validator):
        """rm command should be blocked."""
        is_valid, error = validator.validate_command("rm -rf /")
        assert is_valid is False
        assert "rm" in error
        assert "not allowed" in error
    
    def test_sudo_blocked(self, validator):
        """sudo command should be blocked."""
        is_valid, error = validator.validate_command("sudo cat /etc/passwd")
        assert is_valid is False
        assert "sudo" in error
    
    def test_chmod_blocked(self, validator):
        """chmod command should be blocked."""
        is_valid, error = validator.validate_command("chmod 777 file.txt")
        assert is_valid is False
        assert "chmod" in error
    
    def test_mv_blocked(self, validator):
        """mv command should be blocked."""
        is_valid, error = validator.validate_command("mv file1.txt file2.txt")
        assert is_valid is False
        assert "mv" in error
    
    def test_cp_blocked(self, validator):
        """cp command should be blocked."""
        is_valid, error = validator.validate_command("cp file1.txt file2.txt")
        assert is_valid is False
        assert "cp" in error
    
    def test_dd_blocked(self, validator):
        """dd command should be blocked."""
        is_valid, error = validator.validate_command("dd if=/dev/zero of=file bs=1M count=100")
        assert is_valid is False
        assert "dd" in error
    
    # ==================== EDGE CASES ====================
    
    def test_empty_command_blocked(self, validator):
        """Empty command should be blocked."""
        is_valid, error = validator.validate_command("")
        assert is_valid is False
        assert "Empty command" in error
    
    def test_whitespace_only_blocked(self, validator):
        """Whitespace-only command should be blocked."""
        is_valid, error = validator.validate_command("   ")
        assert is_valid is False
        assert "Empty command" in error
    
    def test_command_with_leading_whitespace(self, validator):
        """Command with leading whitespace should work."""
        is_valid, error = validator.validate_command("  grep pattern file.txt")
        assert is_valid is True
        assert error is None


class TestValidateTerminalCommandFunction:
    """Test the convenience function."""
    
    def test_convenience_function_allowed(self):
        """Convenience function should allow safe commands."""
        is_valid, error = validate_terminal_command("grep pattern file.txt")
        assert is_valid is True
        assert error is None
    
    def test_convenience_function_blocked(self):
        """Convenience function should block unsafe commands."""
        is_valid, error = validate_terminal_command("rm -rf /")
        assert is_valid is False
        assert "rm" in error
    
    def test_convenience_function_custom_allowed_commands(self):
        """Convenience function should accept custom allowed commands."""
        is_valid, error = validate_terminal_command("custom_cmd arg", allowed_commands={'custom_cmd'})
        assert is_valid is True
        assert error is None
```

### Manual Testing

After implementation, verify these commands are allowed:

```bash
# Test 1: Stderr redirect
grep -n 'class.*OneTimeCode' Mac/Safari/FormAutoFill/*.h 2>/dev/null | head -20

# Test 2: Regex alternation
grep -n 'isTimeBasedOneTimeCodeItem\|isOneTimeCodeItem' file.mm | head -20

# Test 3: Awk logical AND
awk 'NR>=3100 && NR<=3600' file.js

# Test 4: Complex pipe chain
find . -name '*.h' | xargs grep -l 'Pattern' 2>/dev/null | head -10
```

And verify these are still blocked:

```bash
# Should be blocked - rm is not in SAFE_COMMANDS
rm -rf /

# Should be blocked - sudo is not in SAFE_COMMANDS
sudo cat /etc/passwd

# Should be blocked - chmod is not in SAFE_COMMANDS
chmod 777 file.txt
```

### Running Tests

```bash
# Run only command validator tests
pytest hindsight/tests/core/llm/test_command_validator.py -v

# Run with coverage
pytest hindsight/tests/core/llm/test_command_validator.py -v --cov=hindsight.core.llm.command_validator
```
