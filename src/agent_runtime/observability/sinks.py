from __future__ import annotations

from typing import Protocol

from agent_runtime.core.events import AgentEvent


class EventSink(Protocol):
    async def emit(self, event: AgentEvent) -> None:
        ...


class MemoryEventSink:
    def __init__(self) -> None:
        self.events: list[AgentEvent] = []

    async def emit(self, event: AgentEvent) -> None:
        self.events.append(event)
