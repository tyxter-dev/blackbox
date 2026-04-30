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
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from agent_runtime.core.errors import SessionCursorError
from agent_runtime.core.events import AgentEvent
from agent_runtime.core.serialization import (
    agent_session_state_from_dict,
    agent_session_state_to_dict,
    event_from_dict,
    event_to_dict,
    run_state_from_dict,
    run_state_to_dict,
    session_event_cursor_from_dict,
    session_event_cursor_to_dict,
)
from agent_runtime.core.session_state import (
    AgentSessionState,
    SessionEventCursor,
    apply_session_event,
)
from agent_runtime.core.state import RunState


@runtime_checkable
class EventStore(Protocol):
    """Append-only log of events keyed by ``run_id``."""

    async def append(self, event: AgentEvent) -> None: ...

    async def append_many(self, events: Iterable[AgentEvent]) -> None:
        """Append a burst of events with one backend write where supported."""
        ...

    async def list_events(
        self,
        run_id: str,
        *,
        after_sequence: int | None = None,
        limit: int | None = None,
    ) -> list[AgentEvent]:
        """Return events for a run after an optional sequence cursor."""
        ...


@runtime_checkable
class RunStore(Protocol):
    """Storage for the durable :class:`RunState` of an in-flight or completed run."""

    async def save(self, state: RunState) -> None: ...

    async def load(self, run_id: str) -> RunState | None: ...


@runtime_checkable
class SessionStore(Protocol):
    """Durable lifecycle state and replay ledger for agent sessions."""

    async def save_session_state(self, state: AgentSessionState) -> None: ...

    async def load_session_state(self, session_id: str) -> AgentSessionState | None: ...

    async def delete_session_state(self, session_id: str) -> None: ...

    async def append_session_event(
        self,
        session_id: str,
        event: AgentEvent,
        *,
        provider_event_id: str | None = None,
        provider_cursor: str | None = None,
    ) -> SessionEventCursor: ...

    async def list_session_events(
        self,
        session_id: str,
        *,
        after_event_id: str | None = None,
        limit: int | None = None,
    ) -> list[AgentEvent]: ...

    async def get_event_cursor(
        self,
        session_id: str,
        event_id: str,
    ) -> SessionEventCursor | None: ...

    async def list_event_cursors(
        self,
        session_id: str,
        *,
        after_event_id: str | None = None,
        limit: int | None = None,
    ) -> list[SessionEventCursor]: ...

    async def find_session_by_approval(
        self,
        approval_id: str,
    ) -> AgentSessionState | None: ...


@dataclass
class InMemoryEventStore:
    """Process-local event log. Useful for tests and simple deployments."""

    _events_by_run: dict[str, list[AgentEvent]] = field(default_factory=dict)

    async def append(self, event: AgentEvent) -> None:
        if event.run_id is None:
            return
        bucket = self._events_by_run.setdefault(event.run_id, [])
        bucket.append(event)

    async def append_many(self, events: Iterable[AgentEvent]) -> None:
        for event in events:
            await self.append(event)

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
class InMemorySessionStore:
    """Process-local session state and event ledger."""

    _states: dict[str, AgentSessionState] = field(default_factory=dict)
    _events_by_session: dict[str, list[AgentEvent]] = field(default_factory=dict)
    _cursors_by_session: dict[str, list[SessionEventCursor]] = field(default_factory=dict)
    _event_ids: dict[str, set[str]] = field(default_factory=dict)

    async def save_session_state(self, state: AgentSessionState) -> None:
        self._states[state.session.id] = state

    async def load_session_state(self, session_id: str) -> AgentSessionState | None:
        return self._states.get(session_id)

    async def delete_session_state(self, session_id: str) -> None:
        self._states.pop(session_id, None)
        self._events_by_session.pop(session_id, None)
        self._cursors_by_session.pop(session_id, None)
        self._event_ids.pop(session_id, None)

    async def append_session_event(
        self,
        session_id: str,
        event: AgentEvent,
        *,
        provider_event_id: str | None = None,
        provider_cursor: str | None = None,
    ) -> SessionEventCursor:
        event_ids = self._event_ids.setdefault(session_id, set())
        if event.id in event_ids:
            cursor = await self.get_event_cursor(session_id, event.id)
            assert cursor is not None
            return cursor
        cursors = self._cursors_by_session.setdefault(session_id, [])
        cursor = SessionEventCursor(
            session_id=session_id,
            event_id=event.id,
            session_sequence=len(cursors),
            run_id=event.run_id,
            run_sequence=event.sequence,
            provider_event_id=provider_event_id,
            provider_cursor=provider_cursor,
            created_at=event.timestamp.isoformat(),
        )
        self._events_by_session.setdefault(session_id, []).append(event)
        cursors.append(cursor)
        event_ids.add(event.id)
        state = self._states.get(session_id)
        if state is not None:
            apply_session_event(state, event, cursor)
        return cursor

    async def list_session_events(
        self,
        session_id: str,
        *,
        after_event_id: str | None = None,
        limit: int | None = None,
    ) -> list[AgentEvent]:
        events = self._events_by_session.get(session_id, [])
        start = _after_event_index(
            self._cursors_by_session.get(session_id, []),
            session_id=session_id,
            after_event_id=after_event_id,
        )
        result = list(events[start:])
        if limit is not None:
            result = result[:limit]
        return result

    async def get_event_cursor(
        self,
        session_id: str,
        event_id: str,
    ) -> SessionEventCursor | None:
        for cursor in self._cursors_by_session.get(session_id, []):
            if cursor.event_id == event_id:
                return cursor
        return None

    async def list_event_cursors(
        self,
        session_id: str,
        *,
        after_event_id: str | None = None,
        limit: int | None = None,
    ) -> list[SessionEventCursor]:
        cursors = self._cursors_by_session.get(session_id, [])
        start = _after_event_index(
            cursors,
            session_id=session_id,
            after_event_id=after_event_id,
        )
        result = list(cursors[start:])
        if limit is not None:
            result = result[:limit]
        return result

    async def find_session_by_approval(
        self,
        approval_id: str,
    ) -> AgentSessionState | None:
        for state in self._states.values():
            if approval_id in state.pending_approvals:
                return state
        return None


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
        await self.append_many([event])

    async def append_many(self, events: Iterable[AgentEvent]) -> None:
        lines = [
            json.dumps(event_to_dict(event, keep_raw=self._keep_raw))
            for event in events
            if event.run_id is not None
        ]
        if not lines:
            return
        async with self._lock:
            await asyncio.to_thread(self._append_lines, lines)

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

    def _append_lines(self, lines: list[str]) -> None:
        with self._path.open("a", encoding="utf-8") as fh:
            for line in lines:
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


class JSONLSessionStore:
    """Directory-backed JSON Lines session store.

    Layout:
      sessions/<session_id>/state.json
      sessions/<session_id>/events.jsonl
    """

    def __init__(self, path: str | Path, *, keep_raw: bool = False) -> None:
        self._path = Path(path)
        self._keep_raw = keep_raw
        self._lock = asyncio.Lock()
        self._path.mkdir(parents=True, exist_ok=True)

    @property
    def path(self) -> Path:
        return self._path

    async def save_session_state(self, state: AgentSessionState) -> None:
        async with self._lock:
            await asyncio.to_thread(self._write_state_sync, state)

    async def load_session_state(self, session_id: str) -> AgentSessionState | None:
        async with self._lock:
            payload = await asyncio.to_thread(self._read_state_sync, session_id)
        if payload is None:
            return None
        return agent_session_state_from_dict(payload)

    async def delete_session_state(self, session_id: str) -> None:
        async with self._lock:
            await asyncio.to_thread(self._delete_session_sync, session_id)

    async def append_session_event(
        self,
        session_id: str,
        event: AgentEvent,
        *,
        provider_event_id: str | None = None,
        provider_cursor: str | None = None,
    ) -> SessionEventCursor:
        async with self._lock:
            return await asyncio.to_thread(
                self._append_session_event_sync,
                session_id,
                event,
                provider_event_id,
                provider_cursor,
            )

    async def list_session_events(
        self,
        session_id: str,
        *,
        after_event_id: str | None = None,
        limit: int | None = None,
    ) -> list[AgentEvent]:
        async with self._lock:
            records = await asyncio.to_thread(self._read_event_records_sync, session_id)
        cursors = [_cursor_from_record(record) for record in records]
        start = _after_event_index(cursors, session_id=session_id, after_event_id=after_event_id)
        events = [
            event_from_dict(record["event"])
            for record in records[start:]
            if isinstance(record.get("event"), dict)
        ]
        if limit is not None:
            events = events[:limit]
        return events

    async def get_event_cursor(
        self,
        session_id: str,
        event_id: str,
    ) -> SessionEventCursor | None:
        async with self._lock:
            records = await asyncio.to_thread(self._read_event_records_sync, session_id)
        for record in records:
            cursor = _cursor_from_record(record)
            if cursor.event_id == event_id:
                return cursor
        return None

    async def list_event_cursors(
        self,
        session_id: str,
        *,
        after_event_id: str | None = None,
        limit: int | None = None,
    ) -> list[SessionEventCursor]:
        async with self._lock:
            records = await asyncio.to_thread(self._read_event_records_sync, session_id)
        cursors = [_cursor_from_record(record) for record in records]
        start = _after_event_index(cursors, session_id=session_id, after_event_id=after_event_id)
        result = cursors[start:]
        if limit is not None:
            result = result[:limit]
        return result

    async def find_session_by_approval(
        self,
        approval_id: str,
    ) -> AgentSessionState | None:
        async with self._lock:
            payloads = await asyncio.to_thread(self._read_all_state_payloads_sync)
        for payload in payloads:
            state = agent_session_state_from_dict(payload)
            if approval_id in state.pending_approvals:
                return state
        return None

    def _session_dir(self, session_id: str) -> Path:
        return self._path / session_id

    def _state_path(self, session_id: str) -> Path:
        return self._session_dir(session_id) / "state.json"

    def _events_path(self, session_id: str) -> Path:
        return self._session_dir(session_id) / "events.jsonl"

    def _write_state_sync(self, state: AgentSessionState) -> None:
        session_dir = self._session_dir(state.session.id)
        session_dir.mkdir(parents=True, exist_ok=True)
        payload = agent_session_state_to_dict(state)
        self._state_path(state.session.id).write_text(
            json.dumps(payload, indent=2),
            encoding="utf-8",
        )

    def _read_state_sync(self, session_id: str) -> dict[str, Any] | None:
        path = self._state_path(session_id)
        if not path.exists():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else None

    def _read_all_state_payloads_sync(self) -> list[dict[str, Any]]:
        payloads: list[dict[str, Any]] = []
        for state_path in self._path.glob("*/state.json"):
            try:
                payload = json.loads(state_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                payloads.append(payload)
        return payloads

    def _delete_session_sync(self, session_id: str) -> None:
        import shutil

        path = self._session_dir(session_id)
        if path.exists():
            shutil.rmtree(path)

    def _append_session_event_sync(
        self,
        session_id: str,
        event: AgentEvent,
        provider_event_id: str | None,
        provider_cursor: str | None,
    ) -> SessionEventCursor:
        records = self._read_event_records_sync(session_id)
        for record in records:
            cursor = _cursor_from_record(record)
            if cursor.event_id == event.id:
                return cursor

        cursor = SessionEventCursor(
            session_id=session_id,
            event_id=event.id,
            session_sequence=len(records),
            run_id=event.run_id,
            run_sequence=event.sequence,
            provider_event_id=provider_event_id,
            provider_cursor=provider_cursor,
            created_at=event.timestamp.isoformat(),
        )
        record = {
            "session_id": session_id,
            "session_sequence": cursor.session_sequence,
            "event_id": event.id,
            "run_id": event.run_id,
            "run_sequence": event.sequence,
            "provider": event.provider,
            "event_type": event.type,
            "provider_event_id": provider_event_id,
            "provider_cursor": provider_cursor,
            "created_at": cursor.created_at,
            "cursor": session_event_cursor_to_dict(cursor),
            "event": event_to_dict(event, keep_raw=self._keep_raw),
        }
        session_dir = self._session_dir(session_id)
        session_dir.mkdir(parents=True, exist_ok=True)
        with self._events_path(session_id).open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record))
            fh.write("\n")

        state_payload = self._read_state_sync(session_id)
        if state_payload is not None:
            state = agent_session_state_from_dict(state_payload)
            apply_session_event(state, event, cursor)
            self._write_state_sync(state)
        return cursor

    def _read_event_records_sync(self, session_id: str) -> list[dict[str, Any]]:
        path = self._events_path(session_id)
        if not path.exists():
            return []
        records: list[dict[str, Any]] = []
        with path.open("r", encoding="utf-8") as fh:
            for raw in fh:
                stripped = raw.strip()
                if not stripped:
                    continue
                try:
                    payload = json.loads(stripped)
                except json.JSONDecodeError:
                    continue
                if isinstance(payload, dict):
                    records.append(payload)
        return records


_SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    session_id TEXT PRIMARY KEY,
    provider TEXT,
    model TEXT,
    payload TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


_SQLITE_SESSION_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    agent_id TEXT,
    model TEXT,
    status TEXT NOT NULL,
    task TEXT,
    trace_id TEXT,
    current_run_id TEXT,
    last_event_id TEXT,
    last_session_sequence INTEGER NOT NULL DEFAULT -1,
    last_provider_event_id TEXT,
    last_provider_cursor TEXT,
    payload TEXT NOT NULL,
    schema_version INTEGER NOT NULL,
    created_at TEXT,
    updated_at TEXT,
    completed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_sessions_provider_status
ON sessions(provider, status);

CREATE TABLE IF NOT EXISTS session_events (
    session_id TEXT NOT NULL,
    session_sequence INTEGER NOT NULL,
    event_id TEXT NOT NULL,
    run_id TEXT,
    run_sequence INTEGER,
    provider TEXT,
    event_type TEXT NOT NULL,
    provider_event_id TEXT,
    provider_cursor TEXT,
    payload TEXT NOT NULL,
    cursor_payload TEXT NOT NULL,
    created_at TEXT,
    PRIMARY KEY(session_id, session_sequence),
    UNIQUE(session_id, event_id)
);

CREATE INDEX IF NOT EXISTS idx_session_events_event_id
ON session_events(session_id, event_id);

CREATE TABLE IF NOT EXISTS session_invocations (
    invocation_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    status TEXT NOT NULL,
    idempotency_key TEXT,
    provider_invocation_id TEXT,
    payload TEXT NOT NULL,
    created_at TEXT,
    updated_at TEXT,
    UNIQUE(session_id, idempotency_key)
);

CREATE TABLE IF NOT EXISTS session_approvals (
    approval_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    status TEXT NOT NULL,
    provider_approval_id TEXT,
    idempotency_key TEXT,
    payload TEXT NOT NULL,
    created_at TEXT,
    updated_at TEXT
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


class SQLiteSessionStore:
    """SQLite-backed :class:`SessionStore` implementation."""

    def __init__(self, path: str | Path, *, keep_raw: bool = False) -> None:
        self._path = Path(path)
        self._keep_raw = keep_raw
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()
        self._connection = sqlite3.connect(
            str(self._path),
            check_same_thread=False,
        )
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.executescript(_SQLITE_SESSION_SCHEMA)
        self._connection.commit()

    @property
    def path(self) -> Path:
        return self._path

    async def save_session_state(self, state: AgentSessionState) -> None:
        async with self._lock:
            await asyncio.to_thread(self._save_state_sync, state)

    async def load_session_state(self, session_id: str) -> AgentSessionState | None:
        async with self._lock:
            payload = await asyncio.to_thread(self._load_state_payload_sync, session_id)
        if payload is None:
            return None
        return agent_session_state_from_dict(json.loads(payload))

    async def delete_session_state(self, session_id: str) -> None:
        async with self._lock:
            await asyncio.to_thread(self._delete_state_sync, session_id)

    async def append_session_event(
        self,
        session_id: str,
        event: AgentEvent,
        *,
        provider_event_id: str | None = None,
        provider_cursor: str | None = None,
    ) -> SessionEventCursor:
        async with self._lock:
            return await asyncio.to_thread(
                self._append_event_sync,
                session_id,
                event,
                provider_event_id,
                provider_cursor,
            )

    async def list_session_events(
        self,
        session_id: str,
        *,
        after_event_id: str | None = None,
        limit: int | None = None,
    ) -> list[AgentEvent]:
        async with self._lock:
            rows = await asyncio.to_thread(
                self._list_event_rows_sync,
                session_id,
                after_event_id,
                limit,
            )
        return [event_from_dict(json.loads(row[0])) for row in rows]

    async def get_event_cursor(
        self,
        session_id: str,
        event_id: str,
    ) -> SessionEventCursor | None:
        async with self._lock:
            payload = await asyncio.to_thread(
                self._cursor_payload_sync,
                session_id,
                event_id,
            )
        if payload is None:
            return None
        return session_event_cursor_from_dict(json.loads(payload))

    async def list_event_cursors(
        self,
        session_id: str,
        *,
        after_event_id: str | None = None,
        limit: int | None = None,
    ) -> list[SessionEventCursor]:
        async with self._lock:
            rows = await asyncio.to_thread(
                self._list_cursor_rows_sync,
                session_id,
                after_event_id,
                limit,
            )
        return [session_event_cursor_from_dict(json.loads(row[0])) for row in rows]

    async def find_session_by_approval(
        self,
        approval_id: str,
    ) -> AgentSessionState | None:
        async with self._lock:
            session_id = await asyncio.to_thread(
                self._session_id_for_approval_sync,
                approval_id,
            )
            if session_id is None:
                return None
            payload = await asyncio.to_thread(self._load_state_payload_sync, session_id)
        if payload is None:
            return None
        return agent_session_state_from_dict(json.loads(payload))

    async def close(self) -> None:
        async with self._lock:
            await asyncio.to_thread(self._connection.close)

    def _save_state_sync(self, state: AgentSessionState) -> None:
        payload = json.dumps(agent_session_state_to_dict(state))
        now = datetime.now(UTC).isoformat()
        self._connection.execute(
            "INSERT OR REPLACE INTO sessions("
            "session_id, provider, agent_id, model, status, task, trace_id, "
            "current_run_id, last_event_id, last_session_sequence, "
            "last_provider_event_id, last_provider_cursor, payload, schema_version, "
            "created_at, updated_at, completed_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                state.session.id,
                state.provider,
                state.session.agent_id,
                state.model,
                state.status,
                state.task,
                state.trace_id,
                state.current_run_id,
                state.last_event_id,
                state.last_session_sequence,
                state.last_provider_event_id,
                state.last_provider_cursor,
                payload,
                state.schema_version,
                state.created_at,
                state.updated_at or now,
                state.completed_at,
            ),
        )
        self._connection.execute(
            "DELETE FROM session_invocations WHERE session_id = ?",
            (state.session.id,),
        )
        for invocation in state.invocations:
            self._connection.execute(
                "INSERT OR REPLACE INTO session_invocations("
                "invocation_id, session_id, provider, status, idempotency_key, "
                "provider_invocation_id, payload, created_at, updated_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    invocation.id,
                    invocation.session_id,
                    invocation.provider,
                    invocation.status,
                    invocation.idempotency_key,
                    invocation.provider_invocation_id,
                    json.dumps(session_invocation_state_to_payload(invocation)),
                    invocation.started_at,
                    now,
                ),
            )
        self._connection.execute(
            "DELETE FROM session_approvals WHERE session_id = ?",
            (state.session.id,),
        )
        for approval in state.pending_approvals.values():
            self._connection.execute(
                "INSERT OR REPLACE INTO session_approvals("
                "approval_id, session_id, status, provider_approval_id, "
                "idempotency_key, payload, created_at, updated_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    approval.approval_id,
                    approval.session_id,
                    approval.status,
                    approval.provider_approval_id,
                    approval.idempotency_key,
                    json.dumps(pending_approval_state_to_payload(approval)),
                    approval.requested_event_id,
                    now,
                ),
            )
        self._connection.commit()

    def _load_state_payload_sync(self, session_id: str) -> str | None:
        cursor = self._connection.execute(
            "SELECT payload FROM sessions WHERE session_id = ?",
            (session_id,),
        )
        row = cursor.fetchone()
        return row[0] if row else None

    def _delete_state_sync(self, session_id: str) -> None:
        self._connection.execute("DELETE FROM session_events WHERE session_id = ?", (session_id,))
        self._connection.execute(
            "DELETE FROM session_invocations WHERE session_id = ?",
            (session_id,),
        )
        self._connection.execute(
            "DELETE FROM session_approvals WHERE session_id = ?",
            (session_id,),
        )
        self._connection.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))
        self._connection.commit()

    def _append_event_sync(
        self,
        session_id: str,
        event: AgentEvent,
        provider_event_id: str | None,
        provider_cursor: str | None,
    ) -> SessionEventCursor:
        existing = self._cursor_payload_sync(session_id, event.id)
        if existing is not None:
            return session_event_cursor_from_dict(json.loads(existing))

        row = self._connection.execute(
            "SELECT COALESCE(MAX(session_sequence), -1) FROM session_events WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        sequence = int(row[0]) + 1 if row is not None else 0
        cursor = SessionEventCursor(
            session_id=session_id,
            event_id=event.id,
            session_sequence=sequence,
            run_id=event.run_id,
            run_sequence=event.sequence,
            provider_event_id=provider_event_id,
            provider_cursor=provider_cursor,
            created_at=event.timestamp.isoformat(),
        )
        event_payload = json.dumps(event_to_dict(event, keep_raw=self._keep_raw))
        cursor_payload = json.dumps(session_event_cursor_to_dict(cursor))
        self._connection.execute(
            "INSERT OR IGNORE INTO session_events("
            "session_id, session_sequence, event_id, run_id, run_sequence, provider, "
            "event_type, provider_event_id, provider_cursor, payload, cursor_payload, created_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                session_id,
                cursor.session_sequence,
                event.id,
                event.run_id,
                event.sequence,
                event.provider,
                event.type,
                provider_event_id,
                provider_cursor,
                event_payload,
                cursor_payload,
                cursor.created_at,
            ),
        )
        existing_after_insert = self._cursor_payload_sync(session_id, event.id)
        if existing_after_insert is not None and existing_after_insert != cursor_payload:
            self._connection.commit()
            return session_event_cursor_from_dict(json.loads(existing_after_insert))

        state_payload = self._load_state_payload_sync(session_id)
        if state_payload is not None:
            state = agent_session_state_from_dict(json.loads(state_payload))
            apply_session_event(state, event, cursor)
            self._save_state_sync(state)
        else:
            self._connection.commit()
        return cursor

    def _list_event_rows_sync(
        self,
        session_id: str,
        after_event_id: str | None,
        limit: int | None,
    ) -> list[tuple[str]]:
        after_sequence = self._after_sequence_sync(session_id, after_event_id)
        query = (
            "SELECT payload FROM session_events "
            "WHERE session_id = ? AND session_sequence > ? "
            "ORDER BY session_sequence"
        )
        params: tuple[Any, ...]
        params = (session_id, after_sequence)
        if limit is not None:
            query += " LIMIT ?"
            params = (session_id, after_sequence, limit)
        cursor = self._connection.execute(query, params)
        return list(cursor.fetchall())

    def _list_cursor_rows_sync(
        self,
        session_id: str,
        after_event_id: str | None,
        limit: int | None,
    ) -> list[tuple[str]]:
        after_sequence = self._after_sequence_sync(session_id, after_event_id)
        query = (
            "SELECT cursor_payload FROM session_events "
            "WHERE session_id = ? AND session_sequence > ? "
            "ORDER BY session_sequence"
        )
        params: tuple[Any, ...]
        params = (session_id, after_sequence)
        if limit is not None:
            query += " LIMIT ?"
            params = (session_id, after_sequence, limit)
        cursor = self._connection.execute(query, params)
        return list(cursor.fetchall())

    def _cursor_payload_sync(self, session_id: str, event_id: str) -> str | None:
        cursor = self._connection.execute(
            "SELECT cursor_payload FROM session_events WHERE session_id = ? AND event_id = ?",
            (session_id, event_id),
        )
        row = cursor.fetchone()
        return row[0] if row else None

    def _session_id_for_approval_sync(self, approval_id: str) -> str | None:
        cursor = self._connection.execute(
            "SELECT session_id FROM session_approvals WHERE approval_id = ?",
            (approval_id,),
        )
        row = cursor.fetchone()
        return row[0] if row else None

    def _after_sequence_sync(self, session_id: str, after_event_id: str | None) -> int:
        if after_event_id is None:
            return -1
        row = self._connection.execute(
            "SELECT session_sequence FROM session_events WHERE session_id = ? AND event_id = ?",
            (session_id, after_event_id),
        ).fetchone()
        if row is None:
            raise SessionCursorError(
                f"Unknown session event cursor {after_event_id!r} for session {session_id!r}.",
                session_id=session_id,
                operation="list_session_events",
                safe_to_retry=False,
            )
        return int(row[0])


def _after_event_index(
    cursors: list[SessionEventCursor],
    *,
    session_id: str,
    after_event_id: str | None,
) -> int:
    if after_event_id is None:
        return 0
    for index, cursor in enumerate(cursors):
        if cursor.event_id == after_event_id:
            return index + 1
    raise SessionCursorError(
        f"Unknown session event cursor {after_event_id!r} for session {session_id!r}.",
        session_id=session_id,
        operation="list_session_events",
        safe_to_retry=False,
    )


def _cursor_from_record(record: dict[str, Any]) -> SessionEventCursor:
    raw_cursor = record.get("cursor")
    if isinstance(raw_cursor, dict):
        return session_event_cursor_from_dict(raw_cursor)
    return SessionEventCursor(
        session_id=str(record.get("session_id") or ""),
        event_id=str(record.get("event_id") or ""),
        session_sequence=int(record.get("session_sequence", -1)),
        run_id=record.get("run_id"),
        run_sequence=record.get("run_sequence"),
        provider_event_id=record.get("provider_event_id"),
        provider_cursor=record.get("provider_cursor"),
        created_at=record.get("created_at"),
    )


def session_invocation_state_to_payload(value: Any) -> dict[str, Any]:
    from agent_runtime.core.serialization import session_invocation_to_dict

    return session_invocation_to_dict(value)


def pending_approval_state_to_payload(value: Any) -> dict[str, Any]:
    from agent_runtime.core.serialization import pending_approval_to_dict

    return pending_approval_to_dict(value)
