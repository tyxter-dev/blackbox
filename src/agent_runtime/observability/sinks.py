"""Event sinks.

Sinks consume :class:`AgentEvent` records as they flow through the runtime.
The default :class:`MemoryEventSink` collects everything for tests and
debug. :class:`RedactingEventSink` wraps an inner sink and rewrites
:class:`RawEnvelope` payloads tagged as sensitive before forwarding the
event, so downstream sinks (logs, JSONL, OTLP) never see the raw payload.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Protocol

from agent_runtime.core.events import AgentEvent
from agent_runtime.core.raw import RawEnvelope

RedactionPolicy = Callable[[RawEnvelope], bool]


def default_redaction_policy(envelope: RawEnvelope) -> bool:
    """Redact when the envelope is sensitive or storage is forbidden."""
    return envelope.sensitivity in {"sensitive", "secret"} or not envelope.storage_allowed


class EventSink(Protocol):
    async def emit(self, event: AgentEvent) -> None: ...


class MemoryEventSink:
    """Collects every event in-memory. Useful for tests and replay."""

    def __init__(self) -> None:
        self.events: list[AgentEvent] = []

    async def emit(self, event: AgentEvent) -> None:
        self.events.append(event)


@dataclass(slots=True)
class RedactingEventSink:
    """Wraps an inner sink and replaces sensitive raw payloads with a marker.

    Events whose ``raw`` is not a :class:`RawEnvelope` pass through unchanged
    (this preserves the v0.1 behavior where adapters stash raw SDK objects on
    ``event.raw`` directly). Adapters that want their payloads to be
    redactable must wrap them in ``RawEnvelope`` and tag the sensitivity.
    """

    inner: EventSink
    policy: RedactionPolicy = field(default=default_redaction_policy)
    marker: str = "<redacted>"

    async def emit(self, event: AgentEvent) -> None:
        raw = event.raw
        if isinstance(raw, RawEnvelope) and self.policy(raw):
            await self.inner.emit(replace(event, raw=raw.redacted(self.marker)))
            return
        await self.inner.emit(event)
