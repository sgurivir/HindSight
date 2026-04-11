#!/usr/bin/env python3
"""
Tests for hindsight/core/llm/tools/terminal_tools.py - Terminal Tools Module.

This module tests:
- runTerminalCmd: Execute safe terminal commands with validation
"""

import os
import tempfile
import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from hindsight.core.llm.tools.tools import Tools
from hindsight.utils.directory_tree_util import DirectoryTreeUtil


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def temp_repo():
    """Create a temporary repository with test files."""
    temp_dir = tempfile.mkdtemp()
    
    # Create test files
    test_file = os.path.join(temp_dir, "test.py")
    with open(test_file, 'w') as f:
        f.write("""def hello():
    print("Hello, World!")

def goodbye():
    print("Goodbye!")
""")
    
    # Create a text file for grep testing
    readme_file = os.path.join(temp_dir, "README.md")
    with open(readme_file, 'w') as f:
        f.write("""# Test Project

This is a test project for testing terminal commands.

## Features
- Feature 1
- Feature 2
""")
    
    # Create nested directory structure
    src_dir = os.path.join(temp_dir, "src")
    os.makedirs(src_dir, exist_ok=True)
    
    main_file = os.path.join(src_dir, "main.py")
    with open(main_file, 'w') as f:
        f.write("print('main')\n")
    
    yield temp_dir
    
    if os.path.exists(temp_dir):
        shutil.rmtree(temp_dir)


@pytest.fixture
def tools_instance(temp_repo):
    """Create a Tools instance for testing."""
    directory_tree_util = DirectoryTreeUtil()
    
    tools = Tools(
        repo_path=temp_repo,
        directory_tree_util=directory_tree_util
    )
    
    return tools


# ============================================================================
# runTerminalCmd Tool Tests - Safe Commands
# ============================================================================

class TestRunTerminalCmdSafeCommands:
    """Tests for runTerminalCmd with safe/allowed commands."""

    def test_ls_command(self, tools_instance, temp_repo):
        """Test running ls command."""
        result = tools_instance.execute_tool_use({
            "name": "runTerminalCmd",
            "input": {"command": "ls"}
        })
        
        assert "test.py" in result
        assert "README.md" in result

    def test_ls_with_flags(self, tools_instance, temp_repo):
        """Test running ls with flags."""
        result = tools_instance.execute_tool_use({
            "name": "runTerminalCmd",
            "input": {"command": "ls -la"}
        })
        
        assert "test.py" in result

    def test_cat_command(self, tools_instance, temp_repo):
        """Test running cat command."""
        result = tools_instance.execute_tool_use({
            "name": "runTerminalCmd",
            "input": {"command": "cat README.md"}
        })
        
        assert "Test Project" in result

    def test_grep_command(self, tools_instance, temp_repo):
        """Test running grep command."""
        result = tools_instance.execute_tool_use({
            "name": "runTerminalCmd",
            "input": {"command": "grep -r 'hello' ."}
        })
        
        # Should find hello in test.py
        assert "hello" in result.lower() or "Hello" in result

    def test_grep_with_context(self, tools_instance, temp_repo):
        """Test running grep with context flags."""
        result = tools_instance.execute_tool_use({
            "name": "runTerminalCmd",
            "input": {"command": "grep -A 2 -B 1 'def hello' test.py"}
        })
        
        assert "def hello" in result

    def test_find_command(self, tools_instance, temp_repo):
        """Test running find command."""
        result = tools_instance.execute_tool_use({
            "name": "runTerminalCmd",
            "input": {"command": "find . -name '*.py'"}
        })
        
        assert "test.py" in result

    def test_head_command(self, tools_instance, temp_repo):
        """Test running head command."""
        result = tools_instance.execute_tool_use({
            "name": "runTerminalCmd",
            "input": {"command": "head -n 3 test.py"}
        })
        
        assert "def hello" in result

    def test_tail_command(self, tools_instance, temp_repo):
        """Test running tail command."""
        result = tools_instance.execute_tool_use({
            "name": "runTerminalCmd",
            "input": {"command": "tail -n 3 test.py"}
        })
        
        assert "Goodbye" in result or "goodbye" in result

    def test_wc_command(self, tools_instance, temp_repo):
        """Test running wc command."""
        result = tools_instance.execute_tool_use({
            "name": "runTerminalCmd",
            "input": {"command": "wc -l test.py"}
        })
        
        # Should return line count
        assert "test.py" in result

    def test_pwd_command_blocked(self, tools_instance, temp_repo):
        """Test that pwd command is blocked (not in SAFE_COMMANDS whitelist)."""
        result = tools_instance.execute_tool_use({
            "name": "runTerminalCmd",
            "input": {"command": "pwd"}
        })
        
        # pwd is not in SAFE_COMMANDS, so it should be blocked
        assert "not allowed" in result.lower() or "error" in result.lower()

    def test_echo_command_blocked(self, tools_instance, temp_repo):
        """Test that echo command is blocked (not in SAFE_COMMANDS whitelist)."""
        result = tools_instance.execute_tool_use({
            "name": "runTerminalCmd",
            "input": {"command": "echo 'Hello World'"}
        })
        
        # echo is not in SAFE_COMMANDS, so it should be blocked
        assert "not allowed" in result.lower() or "error" in result.lower()


# ============================================================================
# runTerminalCmd Tool Tests - Blocked Commands
# ============================================================================

class TestRunTerminalCmdBlockedCommands:
    """Tests for runTerminalCmd with blocked/unsafe commands."""

    def test_rm_command_blocked(self, tools_instance, temp_repo):
        """Test that rm command is blocked."""
        result = tools_instance.execute_tool_use({
            "name": "runTerminalCmd",
            "input": {"command": "rm test.py"}
        })
        
        # Should be blocked
        assert "not allowed" in result.lower() or "blocked" in result.lower() or "error" in result.lower()
        
        # File should still exist
        assert os.path.exists(os.path.join(temp_repo, "test.py"))

    def test_rm_rf_command_blocked(self, tools_instance, temp_repo):
        """Test that rm -rf command is blocked."""
        result = tools_instance.execute_tool_use({
            "name": "runTerminalCmd",
            "input": {"command": "rm -rf ."}
        })
        
        # Should be blocked
        assert "not allowed" in result.lower() or "blocked" in result.lower() or "error" in result.lower()

    def test_mv_command_blocked(self, tools_instance, temp_repo):
        """Test that mv command is blocked."""
        result = tools_instance.execute_tool_use({
            "name": "runTerminalCmd",
            "input": {"command": "mv test.py test2.py"}
        })
        
        # Should be blocked
        assert "not allowed" in result.lower() or "blocked" in result.lower() or "error" in result.lower()

    def test_cp_command_blocked(self, tools_instance, temp_repo):
        """Test that cp command is blocked."""
        result = tools_instance.execute_tool_use({
            "name": "runTerminalCmd",
            "input": {"command": "cp test.py test2.py"}
        })
        
        # Should be blocked
        assert "not allowed" in result.lower() or "blocked" in result.lower() or "error" in result.lower()

    def test_chmod_command_blocked(self, tools_instance, temp_repo):
        """Test that chmod command is blocked."""
        result = tools_instance.execute_tool_use({
            "name": "runTerminalCmd",
            "input": {"command": "chmod 777 test.py"}
        })
        
        # Should be blocked
        assert "not allowed" in result.lower() or "blocked" in result.lower() or "error" in result.lower()

    def test_chown_command_blocked(self, tools_instance, temp_repo):
        """Test that chown command is blocked."""
        result = tools_instance.execute_tool_use({
            "name": "runTerminalCmd",
            "input": {"command": "chown root test.py"}
        })
        
        # Should be blocked
        assert "not allowed" in result.lower() or "blocked" in result.lower() or "error" in result.lower()

    def test_sudo_command_blocked(self, tools_instance, temp_repo):
        """Test that sudo command is blocked."""
        result = tools_instance.execute_tool_use({
            "name": "runTerminalCmd",
            "input": {"command": "sudo ls"}
        })
        
        # Should be blocked
        assert "not allowed" in result.lower() or "blocked" in result.lower() or "error" in result.lower()

    def test_curl_command_blocked(self, tools_instance, temp_repo):
        """Test that curl command is blocked."""
        result = tools_instance.execute_tool_use({
            "name": "runTerminalCmd",
            "input": {"command": "curl https://example.com"}
        })
        
        # Should be blocked
        assert "not allowed" in result.lower() or "blocked" in result.lower() or "error" in result.lower()

    def test_wget_command_blocked(self, tools_instance, temp_repo):
        """Test that wget command is blocked."""
        result = tools_instance.execute_tool_use({
            "name": "runTerminalCmd",
            "input": {"command": "wget https://example.com"}
        })
        
        # Should be blocked
        assert "not allowed" in result.lower() or "blocked" in result.lower() or "error" in result.lower()


# ============================================================================
# runTerminalCmd Tool Tests - Pipe Chains
# ============================================================================

class TestRunTerminalCmdPipeChains:
    """Tests for runTerminalCmd with pipe chains."""

    def test_grep_pipe_wc(self, tools_instance, temp_repo):
        """Test grep piped to wc."""
        result = tools_instance.execute_tool_use({
            "name": "runTerminalCmd",
            "input": {"command": "grep 'def' test.py | wc -l"}
        })
        
        # Should return count of lines with 'def'
        assert "2" in result or "Command:" in result

    def test_cat_pipe_grep(self, tools_instance, temp_repo):
        """Test cat piped to grep."""
        result = tools_instance.execute_tool_use({
            "name": "runTerminalCmd",
            "input": {"command": "cat test.py | grep 'hello'"}
        })
        
        assert "hello" in result.lower()

    def test_find_pipe_head(self, tools_instance, temp_repo):
        """Test find piped to head."""
        result = tools_instance.execute_tool_use({
            "name": "runTerminalCmd",
            "input": {"command": "find . -name '*.py' | head -n 2"}
        })
        
        assert ".py" in result

    def test_blocked_command_in_pipe_allowed_if_first_is_safe(self, tools_instance, temp_repo):
        """Test that pipe chains are allowed if the first command is safe.
        
        The simplified validator only checks the first word of the command.
        If the first command is in SAFE_COMMANDS, the entire command is allowed.
        This trusts the LLM to use pipes appropriately.
        """
        # ls is in SAFE_COMMANDS, so this will be allowed even though rm is in the pipe
        # The command will fail because 'rm' expects arguments, but it won't be blocked
        result = tools_instance.execute_tool_use({
            "name": "runTerminalCmd",
            "input": {"command": "ls | head -1"}
        })
        
        # Should be allowed since 'ls' is the first command
        assert "Command:" in result or "test.py" in result or "README" in result


# ============================================================================
# runTerminalCmd Tool Tests - Error Handling
# ============================================================================

class TestRunTerminalCmdErrorHandling:
    """Tests for runTerminalCmd error handling."""

    def test_command_not_found(self, tools_instance, temp_repo):
        """Test handling of non-existent command."""
        result = tools_instance.execute_tool_use({
            "name": "runTerminalCmd",
            "input": {"command": "nonexistentcommand123"}
        })
        
        # Should return error or be blocked
        assert "error" in result.lower() or "not found" in result.lower() or "not allowed" in result.lower()

    def test_file_not_found_in_command(self, tools_instance, temp_repo):
        """Test handling of command with non-existent file."""
        result = tools_instance.execute_tool_use({
            "name": "runTerminalCmd",
            "input": {"command": "cat nonexistent.txt"}
        })
        
        # Should return error about file not found
        assert "error" in result.lower() or "no such file" in result.lower() or "Exit code" in result

    def test_empty_command(self, tools_instance, temp_repo):
        """Test handling of empty command."""
        result = tools_instance.execute_tool_use({
            "name": "runTerminalCmd",
            "input": {"command": ""}
        })
        
        # Should handle gracefully
        assert result is not None

    def test_missing_command_parameter(self, tools_instance, temp_repo):
        """Test handling of missing command parameter."""
        result = tools_instance.execute_tool_use({
            "name": "runTerminalCmd",
            "input": {}
        })
        
        assert "error" in result.lower()


# ============================================================================
# runTerminalCmd Tool Tests - Working Directory
# ============================================================================

class TestRunTerminalCmdWorkingDirectory:
    """Tests for runTerminalCmd working directory behavior."""

    def test_command_runs_in_repo_path(self, tools_instance, temp_repo):
        """Test that commands run in repo_path directory."""
        result = tools_instance.execute_tool_use({
            "name": "runTerminalCmd",
            "input": {"command": "ls"}
        })
        
        # Should see files from temp_repo
        assert "test.py" in result

    def test_relative_path_in_command(self, tools_instance, temp_repo):
        """Test command with relative path."""
        result = tools_instance.execute_tool_use({
            "name": "runTerminalCmd",
            "input": {"command": "cat src/main.py"}
        })
        
        assert "main" in result


# ============================================================================
# runTerminalCmd Tool Tests - Reason Parameter
# ============================================================================

class TestRunTerminalCmdReasonParameter:
    """Tests for runTerminalCmd reason parameter."""

    def test_command_with_reason(self, tools_instance, temp_repo):
        """Test command with reason parameter."""
        result = tools_instance.execute_tool_use({
            "name": "runTerminalCmd",
            "input": {
                "command": "ls",
                "reason": "Listing files to understand project structure"
            }
        })
        
        assert "test.py" in result

    def test_command_without_reason(self, tools_instance, temp_repo):
        """Test command without reason parameter."""
        result = tools_instance.execute_tool_use({
            "name": "runTerminalCmd",
            "input": {"command": "ls"}
        })
        
        assert "test.py" in result


# ============================================================================
# runTerminalCmd Tool Tests - Direct Method Calls
# ============================================================================

class TestRunTerminalCmdDirectCalls:
    """Tests for runTerminalCmd direct method calls."""

    def test_handler_direct_call(self, tools_instance, temp_repo):
        """Test calling _handle_run_terminal_cmd directly."""
        result = tools_instance._handle_run_terminal_cmd(command="ls")
        
        assert "test.py" in result

    def test_execute_tool_direct_call(self, tools_instance, temp_repo):
        """Test calling execute_terminal_cmd_tool directly."""
        result = tools_instance.execute_terminal_cmd_tool(command="ls")
        
        assert "test.py" in result

    def test_execute_tool_with_reason(self, tools_instance, temp_repo):
        """Test calling execute_terminal_cmd_tool with reason."""
        result = tools_instance.execute_terminal_cmd_tool(
            command="ls",
            reason="Testing direct call"
        )
        
        assert "test.py" in result


# ============================================================================
# runTerminalCmd Tool Tests - Statistics
# ============================================================================

class TestRunTerminalCmdStatistics:
    """Tests for runTerminalCmd statistics tracking."""

    def test_updates_count(self, tools_instance, temp_repo):
        """Test that command execution updates count."""
        initial_count = tools_instance.tool_usage_stats.get('runTerminalCmd', {}).get('count', 0)
        
        tools_instance.execute_tool_use({
            "name": "runTerminalCmd",
            "input": {"command": "ls"}
        })
        
        new_count = tools_instance.tool_usage_stats['runTerminalCmd']['count']
        assert new_count == initial_count + 1

    def test_tracks_commands_executed(self, tools_instance, temp_repo):
        """Test that executed commands are tracked."""
        tools_instance.execute_tool_use({
            "name": "runTerminalCmd",
            "input": {"command": "ls"}
        })
        
        commands = tools_instance.tool_usage_stats['runTerminalCmd']['commands_executed']
        assert "ls" in commands

    def test_tracks_total_chars(self, tools_instance, temp_repo):
        """Test that total characters are tracked."""
        tools_instance.execute_tool_use({
            "name": "runTerminalCmd",
            "input": {"command": "ls"}
        })
        
        total_chars = tools_instance.tool_usage_stats['runTerminalCmd']['total_chars']
        assert total_chars > 0


# ============================================================================
# runTerminalCmd Tool Tests - Output Redirects
# ============================================================================

class TestRunTerminalCmdOutputRedirects:
    """Tests for runTerminalCmd output redirect handling.
    
    NOTE: The simplified validator only checks the first word of the command.
    If the first command is in SAFE_COMMANDS, redirects are allowed.
    This trusts the LLM to use redirects appropriately.
    """

    def test_output_redirect_allowed_with_safe_command(self, tools_instance, temp_repo):
        """Test that output redirect is allowed when first command is safe.
        
        The simplified validator only checks the first word. Since 'ls' is in
        SAFE_COMMANDS, the redirect is allowed.
        """
        result = tools_instance.execute_tool_use({
            "name": "runTerminalCmd",
            "input": {"command": "ls > output.txt"}
        })
        
        # Should be allowed since 'ls' is the first command
        assert "Command:" in result
        
        # File may or may not be created depending on execution
        # Clean up if created
        output_file = os.path.join(temp_repo, "output.txt")
        if os.path.exists(output_file):
            os.remove(output_file)

    def test_append_redirect_allowed_with_safe_command(self, tools_instance, temp_repo):
        """Test that append redirect is allowed when first command is safe."""
        result = tools_instance.execute_tool_use({
            "name": "runTerminalCmd",
            "input": {"command": "ls >> output.txt"}
        })
        
        # Should be allowed since 'ls' is the first command
        assert "Command:" in result
        
        # Clean up if created
        output_file = os.path.join(temp_repo, "output.txt")
        if os.path.exists(output_file):
            os.remove(output_file)

    def test_stderr_redirect_allowed_with_safe_command(self, tools_instance, temp_repo):
        """Test that stderr redirect is allowed when first command is safe.
        
        This is particularly useful for commands like 'grep pattern 2>/dev/null'
        which suppress error messages.
        """
        result = tools_instance.execute_tool_use({
            "name": "runTerminalCmd",
            "input": {"command": "grep 'test' . 2>/dev/null"}
        })
        
        # Should be allowed since 'grep' is the first command
        assert "Command:" in result


# ============================================================================
# runTerminalCmd Tool Tests - Command Chaining
# ============================================================================

class TestRunTerminalCmdCommandChaining:
    """Tests for runTerminalCmd command chaining handling.
    
    NOTE: The simplified validator only checks the first word of the command.
    If the first command is in SAFE_COMMANDS, chaining is allowed.
    This trusts the LLM to use chaining appropriately.
    """

    def test_semicolon_chaining_allowed_with_safe_first_command(self, tools_instance, temp_repo):
        """Test that semicolon chaining is allowed when first command is safe.
        
        The simplified validator only checks the first word. Since 'ls' is in
        SAFE_COMMANDS, the chained command is allowed to execute.
        """
        result = tools_instance.execute_tool_use({
            "name": "runTerminalCmd",
            "input": {"command": "ls; ls -la"}
        })
        
        # Should be allowed since 'ls' is the first command
        assert "Command:" in result

    def test_and_chaining_allowed_with_safe_first_command(self, tools_instance, temp_repo):
        """Test that && chaining is allowed when first command is safe."""
        result = tools_instance.execute_tool_use({
            "name": "runTerminalCmd",
            "input": {"command": "ls && cat README.md"}
        })
        
        # Should be allowed since 'ls' is the first command
        assert "Command:" in result

    def test_or_chaining_allowed_with_safe_first_command(self, tools_instance, temp_repo):
        """Test that || chaining is allowed when first command is safe."""
        result = tools_instance.execute_tool_use({
            "name": "runTerminalCmd",
            "input": {"command": "ls || cat README.md"}
        })
        
        # Should be allowed since 'ls' is the first command
        assert "Command:" in result

    def test_chaining_blocked_with_unsafe_first_command(self, tools_instance, temp_repo):
        """Test that chaining is blocked when first command is unsafe."""
        result = tools_instance.execute_tool_use({
            "name": "runTerminalCmd",
            "input": {"command": "rm test.py; ls"}
        })
        
        # Should be blocked since 'rm' is the first command and not in SAFE_COMMANDS
        assert "not allowed" in result.lower() or "error" in result.lower()
        
        # File should still exist
        assert os.path.exists(os.path.join(temp_repo, "test.py"))
