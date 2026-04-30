from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from blackbox.core.items import RunItem


@dataclass(slots=True)
class ProviderState:
    """Provider-native continuation state.

    This is intentionally not a chat-message transcript. Adapters should preserve
    provider-native IDs, state tokens, native history objects, thought signatures,
    MCP approvals, hosted tool IDs, and any other information needed to resume.
    """

    provider: str
    conversation_id: str | None = None
    previous_response_id: str | None = None
    native_history: list[Any] = field(default_factory=list)
    reasoning_state: dict[str, Any] = field(default_factory=dict)
    tool_state: dict[str, Any] = field(default_factory=dict)
    continuation: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class RunState:
    """Runtime state across one model turn or agent session."""

    session_id: str = field(default_factory=lambda: f"sess_{uuid4().hex}")
    provider: str | None = None
    model: str | None = None
    provider_state: ProviderState | None = None
    items: list[RunItem] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def add_item(self, item: RunItem) -> None:
        self.items.append(item)
