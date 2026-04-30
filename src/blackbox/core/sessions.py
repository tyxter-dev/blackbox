from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal
from uuid import uuid4

SessionStatus = Literal["created", "running", "waiting", "completed", "failed", "cancelled"]


@dataclass(slots=True, frozen=True)
class AgentRef:
    """Stable reference to an agent managed by a provider."""

    provider: str
    id: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class SessionRef:
    """Stable reference to a provider-backed agent session."""

    provider: str
    id: str
    agent_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class InvocationRef:
    """Reference to a specific user-triggered invocation inside a session.

    A single session may host many invocations (each ``send_message`` call,
    each follow-up task). Cloud-agent providers that issue their own
    invocation/job IDs should return one of these so the application can
    correlate downstream events and artifacts.
    """

    provider: str
    session_id: str
    id: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class AgentSession:
    """Mutable session record for a task being handled by an agent provider."""

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
