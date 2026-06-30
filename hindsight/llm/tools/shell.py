"""Terminal command tool: runTerminalCmd.

Uses `asyncio.create_subprocess_shell` so the 30-second wait doesn't block
the event loop. Command safety validation runs synchronously up front
(it's pure string parsing) and reuses the existing `CommandValidator` class.

A side log at `{artifacts_dir}/terminal_commands.txt` is preserved
exactly as in the legacy `TerminalToolsMixin`.
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from hindsight.llm.command_validator import CommandValidator
from hindsight.utils.log_util import get_logger
from hindsight.utils.output_directory_provider import get_output_directory_provider

from .registry import ToolContext

logger = get_logger(__name__)

DEFAULT_TIMEOUT_SECONDS = 30


# CommandValidator caches a regex set internally; one shared instance per
# allowed-commands tuple is fine. Keyed by frozenset to allow different
# configurations if a future caller customizes the allow-list.
_VALIDATOR_CACHE: dict[frozenset[str], CommandValidator] = {}


def _validator_for(allowed: frozenset[str]) -> CommandValidator:
    cached = _VALIDATOR_CACHE.get(allowed)
    if cached is None:
        cached = CommandValidator(set(allowed))
        _VALIDATOR_CACHE[allowed] = cached
    return cached


async def run_terminal_cmd_tool(args: Dict[str, Any], ctx: ToolContext) -> str:
    """Validate, execute (or block), log, and return a terminal command's output."""
    command = args.get("command", "")
    reason = args.get("reason")

    logger.info(
        f"[TOOL] runTerminalCmd - command={command!r} | "
        f"[AI REASONING] {reason or 'No reason provided'}"
    )

    if not isinstance(command, str) or not command.strip():
        ctx.record("runTerminalCmd")
        return "Error: command parameter is required and must be a non-empty string"

    validator = _validator_for(ctx.allowed_commands)
    is_valid, error_message = validator.validate_command(command)
    if not is_valid:
        _log_terminal_command(ctx, command, blocked=True)
        logger.warning(f"[TOOL] runTerminalCmd - blocked: {error_message}")
        ctx.record("runTerminalCmd", item=command)
        return error_message

    # Legacy hack: when grep is passed a pattern starting with "-" (e.g.
    # searching for a method declaration), inject the bash `--` end-of-options
    # sentinel so grep doesn't interpret the pattern as a flag.
    original_command = command
    if command.startswith("grep") and ' "-' in command:
        command = command.replace(' "- ', ' -- "- ', 1)

    return await _run_subprocess(command, original_command, ctx)


async def _run_subprocess(command: str, original_command: str, ctx: ToolContext) -> str:
    """Spawn `command` via shell, capture output, enforce a hard timeout."""
    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=ctx.repo_path,
        )
    except Exception as exc:
        msg = f"Error executing command '{command}': {exc}"
        logger.error(f"[TOOL] runTerminalCmd failed at spawn: {exc}")
        ctx.record("runTerminalCmd", item=command)
        return msg

    try:
        stdout_b, stderr_b = await asyncio.wait_for(
            proc.communicate(), timeout=DEFAULT_TIMEOUT_SECONDS
        )
    except asyncio.TimeoutError:
        # Best-effort kill — preserves the "timed out" error message shape.
        try:
            proc.kill()
            await proc.wait()
        except Exception as exc:
            logger.debug(f"[TOOL] runTerminalCmd - kill after timeout failed: {exc}")
        ctx.record("runTerminalCmd", item=command)
        return f"Error: Command '{command}' timed out after {DEFAULT_TIMEOUT_SECONDS} seconds."

    stdout = stdout_b.decode("utf-8", errors="replace")
    stderr = stderr_b.decode("utf-8", errors="replace")
    output = stdout
    if stderr:
        output += f"\nStderr: {stderr}"

    if proc.returncode != 0:
        output += f"\nExit code: {proc.returncode}"
        _log_terminal_command(ctx, original_command, failed=True)
    else:
        _log_terminal_command(ctx, original_command, blocked=False, failed=False)

    final = f"Command: {command}\n{output}"
    logger.info(
        f"[TOOL] runTerminalCmd completed - command={command}, "
        f"exit={proc.returncode}, chars={len(final)}"
    )
    ctx.record("runTerminalCmd", chars=len(final), item=command)
    return final


def _log_terminal_command(
    ctx: ToolContext,
    command: str,
    *,
    blocked: bool = False,
    failed: bool = False,
) -> None:
    """Append a one-line audit entry to `{artifacts}/terminal_commands.txt`.

    Failures here are non-blocking (best effort). Same path layout as the
    legacy implementation.
    """
    try:
        if ctx.artifacts_dir:
            # artifacts_dir is typically {base}/{repo}/code_insights; we want the
            # parent so the log lands at `{base}/{repo}/terminal_commands.txt`.
            artifacts_parent = os.path.dirname(ctx.artifacts_dir)
        else:
            artifacts_parent = get_output_directory_provider().get_repo_artifacts_dir()

        Path(artifacts_parent).mkdir(parents=True, exist_ok=True)
        log_file = Path(artifacts_parent) / "terminal_commands.txt"

        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        if blocked:
            line = f"[{ts}] BLOCKED - {command}\n"
        elif failed:
            line = f"[{ts}] FAILED - {command}\n"
        else:
            line = f"[{ts}] {command}\n"
        with log_file.open("a", encoding="utf-8") as fh:
            fh.write(line)
    except Exception as exc:
        logger.warning(f"[TOOL] Failed to log terminal command: {exc}")
