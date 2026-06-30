"""Tool registry — async dispatch for the JSON-embedded tool protocol.

`ToolContext` owns the shared state every tool needs (repo path, file content
provider, ignore-dirs, artifacts dir, etc). One context per `AnalysisSession`.

`ToolRegistry` is a thin dispatcher: register an async function under a tool
name, then call `await registry.execute(call, allowed=...)`.

The legacy `Tools` class used inheritance + mixins + `_allowed_tools` instance
state. The new design pushes the allowed-set into the per-call API so a single
registry instance can serve Stage-A (full toolset) and Stage-B (restricted)
calls without cloning the executor.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, FrozenSet, List, Optional, Set, Tuple

from hindsight.utils.log_util import get_logger
from hindsight.utils.output_directory_provider import get_output_directory_provider

from ..tool_protocol import ToolCall
from .schemas import (
    get_tool_names,
    normalize_parameters,
    validate_tool_parameters,
)

logger = get_logger(__name__)


# Constants matching the legacy `base.py`.
MAX_FILE_SIZE_BYTES = 1024 * 1024  # 1 MB hard limit
MAX_FILE_CHARACTERS = 80_000  # used by getImplementation pruning
ALLOWED_TERMINAL_COMMANDS: FrozenSet[str] = frozenset(
    {
        "ls", "find", "grep", "wc", "head", "tail", "cat", "tree", "file", "sed",
        "awk", "sort", "uniq", "cut", "xargs", "diff", "strings",
    }
)


ToolHandler = Callable[[Dict[str, Any], "ToolContext"], Awaitable[str]]


@dataclass
class ToolStats:
    """Per-tool execution counters for the end-of-run summary."""

    count: int = 0
    total_chars: int = 0
    items_accessed: List[str] = field(default_factory=list)


@dataclass
class ToolContext:
    """Shared state for all tool invocations in one session.

    Owns no async resources — safe to share across event loops and across
    concurrent tool dispatches. Mutable state is confined to `stats` (a dict
    of `ToolStats`) and is updated under the GIL only, so it is safe under
    asyncio concurrency without an explicit lock.
    """

    repo_path: str
    file_content_provider: Any = None
    artifacts_dir: Optional[str] = None
    directory_tree_util: Any = None
    ignore_dirs: Set[str] = field(default_factory=set)
    allowed_commands: FrozenSet[str] = ALLOWED_TERMINAL_COMMANDS
    stats: Dict[str, ToolStats] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Path resolution — matches legacy `ToolsBase._resolve_file_path`
    # ------------------------------------------------------------------

    def resolve_file_path(self, file_path: Any) -> Tuple[Optional[Path], str]:
        """Resolve a tool-supplied path against (1) absolute, (2) repo-relative,
        (3) the file_content_provider's index.

        Returns (resolved_path, original_string_for_error_messages).
        """
        if not isinstance(file_path, str):
            return None, str(file_path)
        file_path = file_path.strip()
        if not file_path:
            return None, file_path

        p = Path(file_path)
        if p.is_absolute() and p.exists():
            return p.resolve(), file_path

        repo_relative = Path(self.repo_path).resolve() / file_path
        if repo_relative.exists():
            return repo_relative.resolve(), file_path

        if self.file_content_provider is not None and hasattr(self.file_content_provider, "resolve_file_path"):
            resolved = self.file_content_provider.resolve_file_path(file_path)
            if resolved:
                return Path(resolved), file_path

        return None, file_path

    def get_artifacts_dir(self) -> str:
        """Return the configured artifacts dir, or fall back to the singleton."""
        if self.artifacts_dir:
            return self.artifacts_dir
        return get_output_directory_provider().get_repo_artifacts_dir()

    # ------------------------------------------------------------------
    # Stats accounting — used by tools to report usage
    # ------------------------------------------------------------------

    def record(self, tool_name: str, *, chars: int = 0, item: Optional[str] = None) -> None:
        slot = self.stats.setdefault(tool_name, ToolStats())
        slot.count += 1
        slot.total_chars += chars
        if item:
            slot.items_accessed.append(item)

    # ------------------------------------------------------------------
    # Failure logging — preserves the legacy tool_failures/failures.txt path
    # ------------------------------------------------------------------

    def log_tool_failure(self, tool_name: str, command: str, error_msg: str) -> None:
        """Append a `[ts] [tool] cmd \\n Error: msg` line to
        `{artifacts_dir}/tool_failures/failures.txt`.

        Non-blocking: any failure to log is swallowed.
        """
        try:
            artifacts_parent = (
                str(Path(self.artifacts_dir).parent) if self.artifacts_dir else self.get_artifacts_dir()
            )
            failures_dir = Path(artifacts_parent) / "tool_failures"
            failures_dir.mkdir(parents=True, exist_ok=True)
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
            with (failures_dir / "failures.txt").open("a", encoding="utf-8") as fh:
                fh.write(f"[{ts}] [{tool_name}] {command}\nError: {error_msg}\n\n")
        except Exception as exc:
            logger.warning(f"[TOOL] Failed to log tool failure: {exc}")


class ToolRegistry:
    """Async dispatcher for the JSON-embedded tool protocol.

    Usage::

        registry = ToolRegistry(ctx)
        registry.register("readFile", read_file_tool)
        result = await registry.execute(call, allowed=frozenset({"readFile"}))

    The `allowed` parameter on `execute` is a per-call filter: when a tool is
    not in the set, execution is rejected with a string error (so the model
    sees the error in its next turn rather than the run crashing).
    """

    def __init__(self, ctx: ToolContext):
        self.ctx = ctx
        self._handlers: Dict[str, ToolHandler] = {}

    def register(self, tool_name: str, handler: ToolHandler) -> None:
        """Register an async tool handler. Re-registering replaces the existing one."""
        self._handlers[tool_name] = handler
        logger.debug(f"Registered tool: {tool_name}")

    def registered(self) -> List[str]:
        return list(self._handlers)

    async def execute(self, call: ToolCall, *, allowed: FrozenSet[str]) -> str:
        """Dispatch a `ToolCall` to its registered handler.

        Validates the call's params (using the canonical schemas) before
        handing them to the handler, so individual tools don't repeat the
        check. Tool-internal failures (raised exceptions) become string
        results — they never propagate.
        """
        name = call.name
        logger.info(f"[REGISTRY] Executing tool '{name}'")

        if name not in allowed:
            error = (
                f"Error: Tool '{name}' is not available in this context. "
                f"Allowed tools: {', '.join(sorted(allowed))}"
            )
            logger.warning(f"[REGISTRY] {error}")
            return error

        if name == "getImplementation" and "query" in call.args:
            # Specific error preserved from the legacy dispatcher — LLMs sometimes
            # try `{"tool": "getImplementation", "query": "..."}` instead of `name`.
            return (
                "Error: getImplementation requires 'name' parameter, not 'query'. "
                f'Use: {{"tool": "getImplementation", "name": "{call.args.get("query", "")}"}}'
            )

        if name not in self._handlers:
            available = ", ".join(get_tool_names())
            return f"Error: Unknown tool '{name}'. Available tools: {available}"

        ok, err = validate_tool_parameters(name, call.args)
        if not ok:
            return f"Error: {err}"

        normalized = normalize_parameters(name, call.args)
        handler = self._handlers[name]

        try:
            return await handler(normalized, self.ctx)
        except TypeError as exc:
            return f"Error: Parameter mismatch for tool '{name}': {exc}"
        except Exception as exc:  # noqa: BLE001 — feed back to LLM
            logger.error(f"[REGISTRY] Tool '{name}' raised: {exc}")
            return f"Error executing tool '{name}': {exc}"

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------

    def log_usage_summary(self) -> None:
        """Log the same per-tool usage summary the legacy `Tools` class produced."""
        logger.info("=== TOOL USAGE SUMMARY ===")
        total_calls = sum(s.count for s in self.ctx.stats.values())
        total_chars = sum(s.total_chars for s in self.ctx.stats.values())
        logger.info(f"Total tool calls: {total_calls}")
        logger.info(f"Total characters returned: {total_chars}")
        for name, stats in self.ctx.stats.items():
            if stats.count > 0:
                logger.info(f"[{name}] Calls: {stats.count}, Chars: {stats.total_chars}")
                if stats.items_accessed:
                    logger.info(f"[{name}] Items: {', '.join(stats.items_accessed)}")
        logger.info("=== END TOOL USAGE SUMMARY ===")
