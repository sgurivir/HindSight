"""Conversation state — instance-scoped, replaces Claude's class statics.

One `ConversationState` per LLM run (Stage 4a, Stage 4b, etc). Holds the
system prompt and the message list that gets re-sent each iteration.

Tool results are inserted as plain-text user messages with a `[TOOL_RESULT: id]`
header so the model can correlate them with its preceding tool requests. This
matches the legacy on-the-wire format exactly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass
class Message:
    """One conversation turn. Mirrors the AWS Bedrock message shape."""

    role: str
    content: Any

    def to_payload(self) -> Dict[str, Any]:
        return {"role": self.role, "content": self.content}


@dataclass
class ConversationState:
    """Mutable conversation history for one stage's iterative LLM run."""

    system_prompt: str = ""
    original_request: str = ""
    messages: List[Message] = field(default_factory=list)

    def set_system_prompt(self, system_prompt: str) -> None:
        self.system_prompt = system_prompt

    def set_original_request(self, request: str) -> None:
        self.original_request = request

    def add_user(self, content: str) -> None:
        self.messages.append(Message(role="user", content=content))

    def add_assistant(self, content: Any) -> None:
        self.messages.append(Message(role="assistant", content=content))

    def add_tool_result(self, tool_use_id: str, result: str) -> None:
        """Insert a tool result as a plain-text user message.

        Format matches the legacy `Claude._execute_json_tool_request` flow:
        `[TOOL_RESULT: {id}]\n{result}`. Models see this as the next user turn.
        """
        self.messages.append(
            Message(
                role="user",
                content=f"[TOOL_RESULT: {tool_use_id}]\n{result}",
            )
        )

    def as_payload(self) -> List[Dict[str, Any]]:
        """Snapshot the message list in the wire format used by the client."""
        return [m.to_payload() for m in self.messages]

    def last_assistant_text(self) -> str:
        """Return the most recent assistant message as plain text.

        Handles content-block lists by extracting their `text` parts; falls back
        to `str(content)` for unknown shapes. Used when the iteration loop bails
        out without finding valid JSON and we need something to return.
        """
        for message in reversed(self.messages):
            if message.role != "assistant":
                continue
            content = message.content
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                parts: list[str] = []
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        parts.append(block.get("text", ""))
                return "\n".join(parts)
            return str(content)
        return ""

    def clear(self) -> None:
        self.messages.clear()
        self.system_prompt = ""
        self.original_request = ""
