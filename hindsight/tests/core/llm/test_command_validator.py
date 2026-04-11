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


class TestSafeCommandsConstant:
    """Test the SAFE_COMMANDS constant."""
    
    def test_safe_commands_count(self):
        """SAFE_COMMANDS should contain exactly 17 commands."""
        assert len(SAFE_COMMANDS) == 17
    
    def test_safe_commands_contains_expected(self):
        """SAFE_COMMANDS should contain all expected commands."""
        expected = {
            'ls', 'find', 'grep', 'wc', 'head', 'tail', 'cat', 'tree', 'file', 'sed',
            'sort', 'uniq', 'cut', 'awk', 'less', 'more', 'xargs'
        }
        assert SAFE_COMMANDS == expected
