"""Persistence interfaces for runs and events.

The Protocols defined here let the runtime separate execution from storage.
v0.1 ships only the in-memory implementations; JSONL/SQLite/Postgres land in
later milestones.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from agent_runtime.core.events import AgentEvent
from agent_runtime.core.state import RunState


@runtime_checkable
class EventStore(Protocol):
    """Append-only log of events keyed by ``run_id``."""

    async def append(self, event: AgentEvent) -> None: ...

    async def list_events(
        self,
        run_id: str,
        *,
        after_sequence: int | None = None,
        limit: int | None = None,
    ) -> list[AgentEvent]: ...


@runtime_checkable
class RunStore(Protocol):
    """Storage for the durable :class:`RunState` of an in-flight or completed run."""

    async def save(self, state: RunState) -> None: ...

    async def load(self, run_id: str) -> RunState | None: ...


@dataclass
class InMemoryEventStore:
    """Process-local event log. Useful for tests and simple deployments."""

    _events_by_run: dict[str, list[AgentEvent]] = field(default_factory=dict)

    async def append(self, event: AgentEvent) -> None:
        if event.run_id is None:
            return
        bucket = self._events_by_run.setdefault(event.run_id, [])
        bucket.append(event)

    async def list_events(
        self,
        run_id: str,
        *,
        after_sequence: int | None = None,
        limit: int | None = None,
    ) -> list[AgentEvent]:
        events: Iterable[AgentEvent] = self._events_by_run.get(run_id, [])
        if after_sequence is not None:
            events = (
                e for e in events
                if e.sequence is not None and e.sequence > after_sequence
            )
        result = list(events)
        if limit is not None:
            result = result[:limit]
        return result

    def clear(self) -> None:
        self._events_by_run.clear()


@dataclass
class InMemoryRunStore:
    """Process-local run state store."""

    _runs: dict[str, RunState] = field(default_factory=dict)

    async def save(self, state: RunState) -> None:
        self._runs[state.session_id] = state

    async def load(self, run_id: str) -> RunState | None:
        return self._runs.get(run_id)

    def all_runs(self) -> list[RunState]:
        return list(self._runs.values())


@dataclass
class _StoreSink:
    """Internal helper that fans events out into an EventStore."""

    store: EventStore
    extra: dict[str, Any] = field(default_factory=dict)

    async def emit(self, event: AgentEvent) -> None:
        await self.store.append(event)
