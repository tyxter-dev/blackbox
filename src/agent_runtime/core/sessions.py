from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal
from uuid import uuid4

SessionStatus = Literal["created", "running", "waiting", "completed", "failed", "cancelled"]


@dataclass(slots=True, frozen=True)
class AgentRef:
    provider: str
    id: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class SessionRef:
    provider: str
    id: str
    agent_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class AgentSession:
    provider: str
    task: str
    agent_id: str | None = None
    model: str | None = None
    status: SessionStatus = "created"
    metadata: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: f"sess_{uuid4().hex}")

    @property
    def ref(self) -> SessionRef:
        return SessionRef(provider=self.provider, id=self.id, agent_id=self.agent_id, metadata=self.metadata)
