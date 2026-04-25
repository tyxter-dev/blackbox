"""Persistence interfaces for runs and events.

The Protocols defined here let the runtime separate execution from storage.
In addition to the in-memory implementations, v0.1 ships JSONL and SQLite
backends so apps can persist event logs and resume runs from disk; Postgres
and other adapters land in later milestones.
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from agent_runtime.core.events import AgentEvent
from agent_runtime.core.serialization import (
    event_from_dict,
    event_to_dict,
    run_state_from_dict,
    run_state_to_dict,
)
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


class JSONLEventStore:
    """Append-only JSON Lines event store.

    Each event is written as one JSON object per line. ``raw`` payloads are
    dropped by default so the file stays portable across processes; pass
    ``keep_raw=True`` to opt in (the serializer wraps unknown payloads in
    ``repr`` so the file is always JSON-decodable, but lossy).

    Concurrency: file appends and reads are serialized through an
    ``asyncio.Lock``; concurrent writers across processes are not safe (use a
    real database for that).
    """

    def __init__(self, path: str | Path, *, keep_raw: bool = False) -> None:
        self._path = Path(path)
        self._keep_raw = keep_raw
        self._lock = asyncio.Lock()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.touch(exist_ok=True)

    @property
    def path(self) -> Path:
        return self._path

    async def append(self, event: AgentEvent) -> None:
        if event.run_id is None:
            return
        line = json.dumps(event_to_dict(event, keep_raw=self._keep_raw))
        async with self._lock:
            await asyncio.to_thread(self._append_line, line)

    async def list_events(
        self,
        run_id: str,
        *,
        after_sequence: int | None = None,
        limit: int | None = None,
    ) -> list[AgentEvent]:
        async with self._lock:
            payloads = await asyncio.to_thread(self._read_lines)
        events: list[AgentEvent] = []
        for payload in payloads:
            if payload.get("run_id") != run_id:
                continue
            seq = payload.get("sequence")
            if (
                after_sequence is not None
                and isinstance(seq, int)
                and seq <= after_sequence
            ):
                continue
            events.append(event_from_dict(payload))
            if limit is not None and len(events) >= limit:
                break
        return events

    def _append_line(self, line: str) -> None:
        with self._path.open("a", encoding="utf-8") as fh:
            fh.write(line)
            fh.write("\n")

    def _read_lines(self) -> list[dict[str, Any]]:
        if not self._path.exists():
            return []
        out: list[dict[str, Any]] = []
        with self._path.open("r", encoding="utf-8") as fh:
            for raw in fh:
                stripped = raw.strip()
                if not stripped:
                    continue
                try:
                    payload = json.loads(stripped)
                except json.JSONDecodeError:
                    continue
                if isinstance(payload, dict):
                    out.append(payload)
        return out


_SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    session_id TEXT PRIMARY KEY,
    provider TEXT,
    model TEXT,
    payload TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


class SQLiteRunStore:
    """SQLite-backed :class:`RunStore`.

    Opens (or creates) a single-table database. Each ``RunState`` is stored
    as a JSON blob keyed by ``session_id``; ``provider`` and ``model`` are
    surfaced as columns for cheap filtering.
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()
        self._connection = sqlite3.connect(
            str(self._path), check_same_thread=False
        )
        self._connection.execute(_SQLITE_SCHEMA)
        self._connection.commit()

    @property
    def path(self) -> Path:
        return self._path

    async def save(self, state: RunState) -> None:
        payload = json.dumps(run_state_to_dict(state))
        async with self._lock:
            await asyncio.to_thread(self._save_sync, state, payload)

    async def load(self, run_id: str) -> RunState | None:
        async with self._lock:
            payload = await asyncio.to_thread(self._load_sync, run_id)
        if payload is None:
            return None
        return run_state_from_dict(json.loads(payload))

    async def list_runs(self) -> list[RunState]:
        async with self._lock:
            rows = await asyncio.to_thread(self._list_sync)
        return [run_state_from_dict(json.loads(row)) for row in rows]

    async def close(self) -> None:
        async with self._lock:
            await asyncio.to_thread(self._connection.close)

    def _save_sync(self, state: RunState, payload: str) -> None:
        from datetime import UTC, datetime

        self._connection.execute(
            "INSERT OR REPLACE INTO runs(session_id, provider, model, payload, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                state.session_id,
                state.provider,
                state.model,
                payload,
                datetime.now(UTC).isoformat(),
            ),
        )
        self._connection.commit()

    def _load_sync(self, run_id: str) -> str | None:
        cursor = self._connection.execute(
            "SELECT payload FROM runs WHERE session_id = ?", (run_id,)
        )
        row = cursor.fetchone()
        return row[0] if row else None

    def _list_sync(self) -> list[str]:
        cursor = self._connection.execute(
            "SELECT payload FROM runs ORDER BY updated_at"
        )
        return [row[0] for row in cursor.fetchall()]
