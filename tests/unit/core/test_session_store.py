from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest

from blackbox.core.errors import SessionCursorError
from blackbox.core.events import AgentEvent, EventTypes
from blackbox.core.session_state import AgentSessionState
from blackbox.core.sessions import AgentSession
from blackbox.core.stores import (
    InMemorySessionStore,
    JSONLSessionStore,
    SessionStore,
    SQLiteSessionStore,
)

StoreFactory = Callable[[Path], Awaitable[SessionStore]]


async def _memory_store(_: Path) -> SessionStore:
    return InMemorySessionStore()


async def _jsonl_store(path: Path) -> SessionStore:
    return JSONLSessionStore(path / "sessions")


async def _sqlite_store(path: Path) -> SessionStore:
    return SQLiteSessionStore(path / "sessions.sqlite")


@pytest.mark.parametrize("factory", [_memory_store, _jsonl_store, _sqlite_store])
async def test_session_store_saves_state_and_lists_events(
    tmp_path: Path,
    factory: StoreFactory,
) -> None:
    store = await factory(tmp_path)
    session = AgentSession(provider="local", task="go", id="sess_1")
    state = AgentSessionState(
        schema_version=1,
        session=session,
        session_ref=session.ref,
        provider="local",
        status="created",
    )
    await store.save_session_state(state)

    a = AgentEvent(
        id="evt_a",
        type=EventTypes.SESSION_STARTED,
        run_id="run_1",
        sequence=0,
        session_id=session.id,
        provider="local",
    )
    b = AgentEvent(
        id="evt_b",
        type=EventTypes.SESSION_COMPLETED,
        run_id="run_1",
        sequence=1,
        session_id=session.id,
        provider="local",
    )

    cursor_a = await store.append_session_event(
        session.id,
        a,
        provider_event_id="provider_a",
        provider_cursor="cursor_a",
    )
    cursor_b = await store.append_session_event(
        session.id,
        b,
        provider_event_id="provider_b",
        provider_cursor="cursor_b",
    )
    duplicate = await store.append_session_event(
        session.id,
        b,
        provider_event_id="provider_b",
        provider_cursor="cursor_b",
    )

    assert cursor_a.session_sequence == 0
    assert cursor_b.session_sequence == 1
    assert duplicate.session_sequence == 1
    assert duplicate.event_id == "evt_b"

    events = await store.list_session_events(session.id)
    assert [event.id for event in events] == ["evt_a", "evt_b"]
    after = await store.list_session_events(session.id, after_event_id="evt_a")
    assert [event.id for event in after] == ["evt_b"]
    limited = await store.list_session_events(session.id, limit=1)
    assert [event.id for event in limited] == ["evt_a"]

    cursor = await store.get_event_cursor(session.id, "evt_a")
    assert cursor is not None
    assert cursor.provider_cursor == "cursor_a"
    cursors = await store.list_event_cursors(session.id, after_event_id="evt_a")
    assert [cursor.event_id for cursor in cursors] == ["evt_b"]

    loaded = await store.load_session_state(session.id)
    assert loaded is not None
    assert loaded.status == "completed"
    assert loaded.last_event_id == "evt_b"
    assert loaded.last_provider_cursor == "cursor_b"

    close = getattr(store, "close", None)
    if callable(close):
        await close()


@pytest.mark.parametrize("factory", [_memory_store, _jsonl_store, _sqlite_store])
async def test_session_store_unknown_cursor_raises(
    tmp_path: Path,
    factory: StoreFactory,
) -> None:
    store = await factory(tmp_path)

    with pytest.raises(SessionCursorError):
        await store.list_session_events("sess_missing", after_event_id="evt_missing")

    close = getattr(store, "close", None)
    if callable(close):
        await close()


async def test_sqlite_session_store_persists_across_instances(tmp_path: Path) -> None:
    path = tmp_path / "sessions.sqlite"
    first = SQLiteSessionStore(path)
    session = AgentSession(
        provider="claude-code",
        task="go",
        metadata={"provider_session_id": "provider_sess_1"},
        id="sess_1",
    )
    await first.save_session_state(
        AgentSessionState(
            schema_version=1,
            session=session,
            session_ref=session.ref,
            provider=session.provider,
            status=session.status,
        )
    )
    await first.append_session_event(
        session.id,
        AgentEvent(
            id="evt_1",
            type=EventTypes.CLOUD_AGENT_LOG,
            run_id="run_1",
            sequence=0,
            session_id=session.id,
            provider="claude-code",
            data={"message": "working"},
        ),
        provider_event_id="provider_evt_1",
        provider_cursor="provider_cursor_1",
    )
    await first.close()

    second = SQLiteSessionStore(path)
    loaded = await second.load_session_state(session.id)
    assert loaded is not None
    assert loaded.session.metadata["provider_session_id"] == "provider_sess_1"
    events = await second.list_session_events(session.id)
    assert [event.id for event in events] == ["evt_1"]
    cursor = await second.get_event_cursor(session.id, "evt_1")
    assert cursor is not None
    assert cursor.provider_cursor == "provider_cursor_1"
    await second.close()


async def test_jsonl_session_store_persists_across_instances(tmp_path: Path) -> None:
    path = tmp_path / "sessions"
    first = JSONLSessionStore(path)
    session = AgentSession(provider="local", task="go", id="sess_1")
    await first.save_session_state(
        AgentSessionState(
            schema_version=1,
            session=session,
            session_ref=session.ref,
            provider=session.provider,
            status=session.status,
        )
    )
    await first.append_session_event(
        session.id,
        AgentEvent(
            id="evt_1",
            type=EventTypes.SESSION_STARTED,
            session_id=session.id,
            provider="local",
        ),
    )

    second = JSONLSessionStore(path)
    loaded = await second.load_session_state(session.id)
    assert loaded is not None
    assert loaded.session.id == "sess_1"
    events = await second.list_session_events(session.id)
    assert [event.id for event in events] == ["evt_1"]
