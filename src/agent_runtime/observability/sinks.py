"""Event sinks.

Sinks consume :class:`AgentEvent` records as they flow through the runtime.
The default :class:`MemoryEventSink` collects everything for tests and
debug. :class:`RedactingEventSink` wraps an inner sink and rewrites
:class:`RawEnvelope` payloads tagged as sensitive before forwarding the
event, so downstream sinks (logs, JSONL, OTLP) never see the raw payload.
"""
from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Protocol

from agent_runtime.core.events import AgentEvent
from agent_runtime.core.raw import RawEnvelope
from agent_runtime.core.serialization import event_to_dict

RedactionPolicy = Callable[[RawEnvelope], bool]


def default_redaction_policy(envelope: RawEnvelope) -> bool:
    """Redact when the envelope is sensitive or storage is forbidden."""
    return envelope.sensitivity in {"sensitive", "secret"} or not envelope.storage_allowed


class EventSink(Protocol):
    """Protocol for asynchronous consumers of runtime events."""

    async def emit(self, event: AgentEvent) -> None: ...


class MemoryEventSink:
    """Collects every event in-memory. Useful for tests and replay."""

    def __init__(self) -> None:
        self.events: list[AgentEvent] = []

    async def emit(self, event: AgentEvent) -> None:
        self.events.append(event)


@dataclass(slots=True)
class CompositeEventSink:
    """Fan one event out to multiple sinks in order."""

    sinks: list[EventSink] = field(default_factory=list)

    async def emit(self, event: AgentEvent) -> None:
        for sink in self.sinks:
            await sink.emit(event)


class JSONLEventSink:
    """Append redaction-safe runtime events to a JSON Lines file."""

    def __init__(
        self,
        path: str | Path,
        *,
        keep_raw: bool = False,
        extra: dict[str, object] | None = None,
    ) -> None:
        self._path = Path(path)
        self._keep_raw = keep_raw
        self._extra = dict(extra or {})
        self._lock = asyncio.Lock()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.touch(exist_ok=True)

    @property
    def path(self) -> Path:
        return self._path

    async def emit(self, event: AgentEvent) -> None:
        payload = event_to_dict(event, keep_raw=self._keep_raw)
        if self._extra:
            payload["observability"] = dict(self._extra)
        line = json.dumps(payload, default=str, sort_keys=True)
        async with self._lock:
            await asyncio.to_thread(self._append_line, line)

    def _append_line(self, line: str) -> None:
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(line)
            handle.write("\n")


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
