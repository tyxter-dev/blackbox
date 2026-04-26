from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import uuid4

from agent_runtime.core.events import AgentEvent, EventTypes


@dataclass(slots=True)
class Trace:
    id: str = field(default_factory=lambda: f"trace_{uuid4().hex}")
    events: list[AgentEvent] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def record(self, event: AgentEvent) -> None:
        self.events.append(event)


@dataclass(slots=True, frozen=True)
class TraceSpan:
    """A compact tracing span derived from runtime events."""

    name: str
    kind: str
    span_id: str = field(default_factory=lambda: f"span_{uuid4().hex}")
    trace_id: str | None = None
    run_id: str | None = None
    provider: str | None = None
    model: str | None = None
    status: str = "ok"
    started_at: datetime | None = None
    ended_at: datetime | None = None
    duration_ms: float | None = None
    attributes: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "span_id": self.span_id,
            "trace_id": self.trace_id,
            "run_id": self.run_id,
            "name": self.name,
            "kind": self.kind,
            "provider": self.provider,
            "model": self.model,
            "status": self.status,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "ended_at": self.ended_at.isoformat() if self.ended_at else None,
            "duration_ms": self.duration_ms,
            "attributes": dict(self.attributes),
        }


def model_turn_span_from_events(
    events: list[AgentEvent],
    *,
    provider: str | None,
    model: str | None,
    metadata: dict[str, Any],
) -> TraceSpan | None:
    """Build a single model-turn span from canonical model events."""

    if not events:
        return None
    started = _first_event(events, EventTypes.MODEL_REQUEST_STARTED) or events[0]
    completed = _last_event(events, EventTypes.MODEL_COMPLETED) or events[-1]
    duration_ms = (
        (completed.timestamp - started.timestamp).total_seconds() * 1000
        if completed.timestamp and started.timestamp
        else None
    )
    attributes: dict[str, Any] = {}
    for key in ("usage", "cost", "mcp", "hosted_tools"):
        if key in metadata:
            attributes[key] = metadata[key]
    return TraceSpan(
        name="model.turn",
        kind="model",
        trace_id=started.run_id,
        run_id=started.run_id,
        provider=provider or completed.provider or started.provider,
        model=model,
        status="ok" if _last_event(events, EventTypes.MODEL_COMPLETED) else "unknown",
        started_at=started.timestamp,
        ended_at=completed.timestamp,
        duration_ms=duration_ms,
        attributes=attributes,
    )


def _first_event(events: list[AgentEvent], event_type: str) -> AgentEvent | None:
    return next((event for event in events if event.type == event_type), None)


def _last_event(events: list[AgentEvent], event_type: str) -> AgentEvent | None:
    return next((event for event in reversed(events) if event.type == event_type), None)
