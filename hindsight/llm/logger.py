"""Per-session conversation logger.

Writes markdown transcripts of every LLM call to
`{artifacts}/prompts_sent/{analyzer}/[{issue_n}/]stepN_{stage}.md`. This is
the same on-disk layout as the legacy logger that lived on `Claude` as class
statics.

The "currently active conversation" is a **handle returned from
`start_conversation()` and passed back into `record_turn()`/`finalize()`**,
so concurrent async workers sharing one `ConversationLogger` cannot trample
each other's in-flight transcripts. Only truly shared state (step counter,
issue directory) lives on the instance.

Errors that exceed token limits also get dumped to
`{artifacts}/results/errors/too_large_context_error_{ts}.txt`, preserving the
diagnostic file the old code produced.

Lifecycle:
  logger = ConversationLogger(artifacts_dir, analyzer="code_analysis")
  logger.clear_older_prompts()        # once per run
  logger.start_issue(issue_number=42) # before each function (optional)
  conv = logger.start_conversation("context_collection", "MyClass.method")
  logger.record_turn(conv, messages_sent, response_received)
  ...
  logger.finalize(conv, final_result=json_str)
  logger.end_issue()

Or use the context manager, which yields the handle and auto-finalizes on
exit if the caller didn't:
  with logger.conversation("context_collection", "...") as conv:
      logger.record_turn(conv, ...)
      logger.finalize(conv, final_result=...)
"""

from __future__ import annotations

import os
import shutil
import time
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Iterator, List, Optional

from hindsight.utils.file_util import ensure_directory_exists, write_file
from hindsight.utils.log_util import get_logger

logger = get_logger(__name__)


@dataclass
class _Conversation:
    """One conversation = one LLM run = one .md file when finalized.

    Instances are handed to callers by `start_conversation()` and passed
    back into `record_turn()`/`finalize()`. Each concurrent worker holds
    its own handle, so no cross-task state is shared here.
    """

    stage: str
    context_info: str
    started_at: str
    turns: List[Dict[str, Any]] = field(default_factory=list)
    finalized: bool = False


class ConversationLogger:
    """Per-session markdown logger for LLM conversations.

    Safe for concurrent use: every call is scoped to a `_Conversation`
    handle owned by the caller, so two workers sharing one logger cannot
    trample each other's transcripts. The instance lock only guards the
    shared step counter and issue-directory fields.
    """

    def __init__(self, artifacts_dir: str, analyzer: str):
        """Construct a logger rooted at `{artifacts_dir}/prompts_sent/{analyzer}/`.

        Errors land in `{artifacts_dir}/results/errors/`. Both directories are
        created on demand.
        """
        self._artifacts_dir = artifacts_dir
        self._analyzer = analyzer
        self._prompts_dir = os.path.join(artifacts_dir, "prompts_sent", analyzer)
        self._errors_dir = os.path.join(artifacts_dir, "results", "errors")
        self._lock = threading.Lock()

        self._counter = 0
        self._current_issue_number: Optional[int] = None
        self._current_issue_dir: Optional[str] = None

        ensure_directory_exists(self._prompts_dir)
        ensure_directory_exists(self._errors_dir)
        logger.debug(f"ConversationLogger setup at: {self._prompts_dir}")

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def clear_older_prompts(self) -> None:
        """Wipe `prompts_sent/{analyzer}/` and reset the step counter.

        Call once at the start of each analysis run (matches old
        `Claude.clear_older_prompts`).
        """
        with self._lock:
            self._counter = 0
            self._current_issue_number = None
            self._current_issue_dir = None
            if os.path.exists(self._prompts_dir):
                shutil.rmtree(self._prompts_dir)
                ensure_directory_exists(self._prompts_dir)
                logger.info(f"Cleared and recreated prompts directory: {self._prompts_dir}")

    def start_issue(self, issue_number: int) -> None:
        """Begin logging into `prompts_sent/{analyzer}/{issue_number}/`.

        Resets the per-issue step counter so each function's transcripts are
        numbered from 1.
        """
        with self._lock:
            self._current_issue_number = issue_number
            self._current_issue_dir = os.path.join(self._prompts_dir, str(issue_number))
            ensure_directory_exists(self._current_issue_dir)
            self._counter = 0
            logger.info(f"Started issue logging in: {self._current_issue_dir}")

    def end_issue(self) -> None:
        """Stop logging into the issue-specific subdirectory."""
        with self._lock:
            if self._current_issue_number is not None:
                logger.info(f"Ended issue logging for issue {self._current_issue_number}")
            self._current_issue_number = None
            self._current_issue_dir = None

    # ------------------------------------------------------------------
    # Conversation tracking
    # ------------------------------------------------------------------

    @contextmanager
    def conversation(self, stage: str, context_info: str = "") -> Iterator[_Conversation]:
        """Context manager around one LLM run.

        Yields the conversation handle. Exiting the block without an
        explicit `finalize(handle, ...)` still flushes whatever turns were
        recorded so failures leave a transcript on disk.
        """
        conv = self.start_conversation(stage, context_info)
        try:
            yield conv
        finally:
            if not conv.finalized:
                self.finalize(conv, final_result=None)

    def start_conversation(self, stage: str, context_info: str = "") -> _Conversation:
        """Begin a new conversation and return its handle.

        Callers pass the returned handle back into `record_turn()` and
        `finalize()`. Nothing about the active conversation is stored on
        the logger itself, so concurrent workers cannot trample each other.
        """
        return _Conversation(
            stage=stage,
            context_info=context_info,
            started_at=time.strftime("%Y-%m-%d %H:%M:%S"),
        )

    def record_turn(
        self,
        conv: _Conversation,
        messages: List[Dict[str, Any]],
        response: Optional[Dict[str, Any]],
    ) -> None:
        """Record one request/response pair on `conv`."""
        if conv.finalized:
            logger.warning(
                f"record_turn called on already-finalized conversation ({conv.stage}); ignoring"
            )
            return
        conv.turns.append(
            {
                "messages": [dict(m) for m in messages],
                "response": dict(response) if response else {"error": "No response"},
            }
        )

    def finalize(
        self,
        conv: _Conversation,
        final_result: Optional[str] = None,
    ) -> Optional[str]:
        """Flush `conv` to a markdown file.

        Returns the file path on success. Idempotent: a second call on the
        same handle is a no-op.
        """
        if conv.finalized:
            return None
        with self._lock:
            self._counter += 1
            step_n = self._counter
            target_dir = self._current_issue_dir or self._prompts_dir
        stage_safe = conv.stage.replace(" ", "_").replace("/", "_").replace("\\", "_")
        filename = f"step{step_n}_{stage_safe}.md"
        path = os.path.join(target_dir, filename)
        content = self._render(conv, final_result, step_n)
        conv.finalized = True
        if write_file(path, content):
            logger.info(f"Logged conversation: {filename}")
            return path
        logger.warning(f"Failed to log conversation: {filename}")
        return None

    # ------------------------------------------------------------------
    # Error dump (token-limit overflow diagnostic)
    # ------------------------------------------------------------------

    def dump_token_limit_error(
        self,
        messages: List[Dict[str, Any]],
        *,
        total_content_length: int,
        estimated_tokens: int,
        max_input_tokens: int,
    ) -> Optional[str]:
        """Write a diagnostic file when the input exceeds the model's budget.

        Preserves the legacy `too_large_context_error_{timestamp}.txt` format
        so existing tooling that scans `results/errors/` keeps working.
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
        filename = f"too_large_context_error_{timestamp}.txt"
        path = os.path.join(self._errors_dir, filename)

        lines: list[str] = []
        lines.append("=== TOKEN LIMIT ERROR CONTEXT ===")
        lines.append(f"Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append(f"Total content: {total_content_length:,} characters")
        lines.append(f"Estimated tokens: {estimated_tokens:,}")
        lines.append(f"Max input tokens: {max_input_tokens:,}")
        lines.append(f"Number of messages: {len(messages)}")
        lines.append("")
        lines.append("=== MESSAGE BREAKDOWN ===")
        for i, message in enumerate(messages, 1):
            role = message.get("role", "unknown")
            content = message.get("content", "")
            content_len = len(content) if isinstance(content, str) else len(str(content))
            est_msg_tokens = content_len // 3
            lines.append(f"\nMessage {i} ({role.upper()}):")
            lines.append(f"  Length: {content_len:,} characters")
            lines.append(f"  Estimated tokens: {est_msg_tokens:,}")
            if "cache_control" in message:
                lines.append(f"  Cache Control: {message['cache_control']}")
            text = content if isinstance(content, str) else str(content)
            if content_len > 1000:
                lines.append("  Content preview (first 500 chars):")
                lines.append(text[:500])
                lines.append("  ...")
                lines.append("  Content preview (last 500 chars):")
                lines.append(text[-500:])
            else:
                lines.append("  Full content:")
                lines.append(text)
            lines.append("-" * 80)
        lines.append("\n=== END CONTEXT ===\n")

        if write_file(path, "\n".join(lines)):
            logger.info(f"Token limit error context dumped to: {filename}")
            return path
        logger.error(f"Failed to dump token limit error context to: {filename}")
        return None

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    @staticmethod
    def _render(conv: _Conversation, final_result: Optional[str], step_n: int) -> str:
        parts: list[str] = []
        parts.append(f"# CONVERSATION {step_n}\n")
        parts.append(f"**Analysis Type:** {conv.stage}")
        parts.append(f"**Context:** {conv.context_info or 'N/A'}")
        parts.append(f"**Start Time:** {conv.started_at}")
        parts.append("---\n")

        for i, turn in enumerate(conv.turns, 1):
            parts.append(f"## Turn {i}\n")
            for j, message in enumerate(turn["messages"], 1):
                role = message.get("role", "unknown")
                content = message.get("content", "")
                formatted = ConversationLogger._format_content(content)
                parts.append(f"### Message {j} ({role.upper()})")
                cache_ctl = message.get("cache_control")
                if cache_ctl:
                    parts.append(f"**Cache Control:** {cache_ctl}\n")
                parts.append(f"```\n{formatted}\n```\n")

            response = turn["response"]
            if response and not response.get("error"):
                response_text = ConversationLogger._format_response(response)
                parts.append("### ASSISTANT RESPONSE")
                parts.append(f"```\n{response_text}\n```\n")

                usage = response.get("usage", {}) if isinstance(response, dict) else {}
                input_tokens = usage.get("input_tokens", 0) or usage.get("prompt_tokens", 0)
                output_tokens = usage.get("output_tokens", 0) or usage.get("completion_tokens", 0)
                if input_tokens or output_tokens:
                    parts.append(f"**Token Usage:** Input: {input_tokens:,}, Output: {output_tokens:,}\n")
            else:
                parts.append("### ERROR RESPONSE")
                parts.append(f"```\n{response}\n```\n")

            parts.append("---\n")

        if final_result is not None:
            parts.append("## FINAL ANALYSIS RESULT\n")
            parts.append(f"```json\n{final_result}\n```\n")

        parts.append(f"**End Time:** {time.strftime('%Y-%m-%d %H:%M:%S')}")
        parts.append("=" * 80)
        return "\n".join(parts) + "\n"

    @staticmethod
    def _format_content(content: Any) -> str:
        if isinstance(content, str):
            return content.replace("\\n", "\n")
        if isinstance(content, list):
            return ConversationLogger._format_blocks(content)
        return str(content)

    @staticmethod
    def _format_blocks(blocks: List[Any]) -> str:
        out: list[str] = []
        for block in blocks:
            if not isinstance(block, dict):
                out.append(str(block))
                continue
            btype = block.get("type", "unknown")
            if btype == "text":
                out.append(block.get("text", ""))
            elif btype == "tool_use":
                out.append(f"[TOOL_USE: {block.get('name')} (id: {block.get('id', 'unknown')})]")
                out.append(f"Input: {block.get('input', {})}")
            elif btype == "tool_result":
                out.append(f"[TOOL_RESULT: (id: {block.get('tool_use_id', 'unknown')})]")
                out.append(f"Result: {block.get('content', '')}")
            else:
                out.append(f"[{btype.upper()}: {block}]")
        return "\n".join(out)

    @staticmethod
    def _format_response(response: Dict[str, Any]) -> str:
        choices = response.get("choices") if isinstance(response, dict) else None
        if choices:
            return choices[0].get("message", {}).get("content", "")
        content = response.get("content") if isinstance(response, dict) else None
        if isinstance(content, list):
            return ConversationLogger._format_blocks(content)
        return str(response)
