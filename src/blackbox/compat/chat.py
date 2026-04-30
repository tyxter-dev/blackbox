from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

ChatRole = Literal["system", "user", "assistant", "tool"]


@dataclass(slots=True, frozen=True)
class ChatMessage:
    """Compatibility projection for chat-shaped integrations."""

    role: ChatRole
    content: str
    name: str | None = None
    tool_call_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        message: dict[str, Any] = {"role": self.role, "content": self.content}
        if self.name is not None:
            message["name"] = self.name
        if self.tool_call_id is not None:
            message["tool_call_id"] = self.tool_call_id
        if self.metadata:
            message["metadata"] = dict(self.metadata)
        return message


def messages_to_input(messages: list[ChatMessage | dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert chat-shaped messages into provider input payloads."""

    converted: list[dict[str, Any]] = []
    for message in messages:
        if isinstance(message, ChatMessage):
            converted.append(message.to_dict())
        else:
            converted.append(dict(message))
    return converted


def message_from_assistant_text(text: str) -> ChatMessage:
    return ChatMessage(role="assistant", content=text)
