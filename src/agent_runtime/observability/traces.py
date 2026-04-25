from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from agent_runtime.core.events import AgentEvent


@dataclass(slots=True)
class Trace:
    id: str = field(default_factory=lambda: f"trace_{uuid4().hex}")
    events: list[AgentEvent] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def record(self, event: AgentEvent) -> None:
        self.events.append(event)
