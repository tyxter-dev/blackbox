"""Tests for the JSONL and SQLite store backends.

Covers VALIDATION rows 5.3 / 5.4 / 5.5 against the new persistent
implementations and the round-trip through ``serialization.py``.
"""
from __future__ import annotations

import json
from pathlib import Path

from agent_runtime.core.events import AgentEvent, EventTypes
from agent_runtime.core.items import ItemTypes, RunItem
from agent_runtime.core.raw import RawEnvelope
from agent_runtime.core.serialization import event_from_dict, event_to_dict
from agent_runtime.core.state import ProviderState, RunState
from agent_runtime.core.stores import JSONLEventStore, SQLiteRunStore

# --- serialization round-trip ---------------------------------------------

def test_event_round_trip_preserves_known_typed_payloads() -> None:
    item = RunItem(
        type=ItemTypes.MESSAGE, provider="scripted", status="completed",
        data={"role": "assistant", "text": "hi"},
    )
    state = ProviderState(provider="scripted", previous_response_id="resp_1")
    event = AgentEvent(
        type=EventTypes.MODEL_COMPLETED,
        run_id="run_1",
        sequence=2,
        provider="scripted",
        item_id=item.id,
        data={"item": item, "provider_state": state, "delta": "hi"},
    )

    payload = event_to_dict(event)
    serialized = json.dumps(payload)  # must be JSON-safe end-to-end
    rehydrated = event_from_dict(json.loads(serialized))

    assert rehydrated.type == event.type
    assert rehydrated.run_id == "run_1"
    assert rehydrated.sequence == 2
    assert isinstance(rehydrated.data["item"], RunItem)
    assert rehydrated.data["item"].id == item.id
    assert isinstance(rehydrated.data["provider_state"], ProviderState)
    assert rehydrated.data["provider_state"].previous_response_id == "resp_1"


def test_event_drops_raw_by_default_keeps_when_requested() -> None:
    raw = RawEnvelope(provider="openai", payload={"id": "resp_1"}, sensitivity="public")
    event = AgentEvent(type=EventTypes.MODEL_TEXT_DELTA, raw=raw, run_id="r")

    dropped = event_from_dict(event_to_dict(event))
    assert dropped.raw is None

    kept = event_from_dict(event_to_dict(event, keep_raw=True))
    assert isinstance(kept.raw, RawEnvelope)
    assert kept.raw.payload == {"id": "resp_1"}


# --- JSONL event store ----------------------------------------------------

async def test_jsonl_event_store_round_trip(tmp_path: Path) -> None:
    store = JSONLEventStore(tmp_path / "events.jsonl")
    a = AgentEvent(type=EventTypes.MODEL_TEXT_DELTA, run_id="run_1", sequence=0,
                   data={"delta": "a"})
    b = AgentEvent(type=EventTypes.MODEL_TEXT_DELTA, run_id="run_1", sequence=1,
                   data={"delta": "b"})
    c = AgentEvent(type=EventTypes.MODEL_TEXT_DELTA, run_id="run_2", sequence=0,
                   data={"delta": "c"})
    for event in (a, b, c):
        await store.append(event)

    run_one = await store.list_events("run_1")
    assert [e.data["delta"] for e in run_one] == ["a", "b"]

    after = await store.list_events("run_1", after_sequence=0)
    assert [e.data["delta"] for e in after] == ["b"]

    limited = await store.list_events("run_1", limit=1)
    assert [e.data["delta"] for e in limited] == ["a"]


async def test_jsonl_event_store_persists_across_instances(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    first = JSONLEventStore(path)
    await first.append(
        AgentEvent(type=EventTypes.MODEL_TEXT_DELTA, run_id="run_x",
                   sequence=0, data={"delta": "hello"})
    )
    second = JSONLEventStore(path)
    events = await second.list_events("run_x")
    assert len(events) == 1
    assert events[0].data["delta"] == "hello"


async def test_jsonl_event_store_drops_events_without_run_id(tmp_path: Path) -> None:
    store = JSONLEventStore(tmp_path / "events.jsonl")
    await store.append(AgentEvent(type=EventTypes.MODEL_TEXT_DELTA, data={"delta": "x"}))
    assert (tmp_path / "events.jsonl").read_text() == ""


# --- SQLite run store -----------------------------------------------------

async def test_sqlite_run_store_round_trip(tmp_path: Path) -> None:
    store = SQLiteRunStore(tmp_path / "runs.sqlite")
    state = RunState(
        provider="scripted", model="test",
        provider_state=ProviderState(provider="scripted", previous_response_id="resp_42"),
        items=[
            RunItem(
                type=ItemTypes.FUNCTION_RESULT,
                provider="local",
                status="completed",
                data={"call_id": "call_1", "name": "lookup", "content": "ok"},
            )
        ],
        metadata={"validation_attempts": 1},
    )
    await store.save(state)

    loaded = await store.load(state.session_id)
    assert loaded is not None
    assert loaded.session_id == state.session_id
    assert loaded.provider == "scripted"
    assert loaded.provider_state is not None
    assert loaded.provider_state.previous_response_id == "resp_42"
    assert len(loaded.items) == 1
    assert loaded.items[0].data["call_id"] == "call_1"
    assert loaded.metadata == {"validation_attempts": 1}

    await store.close()


async def test_sqlite_run_store_persists_across_instances(tmp_path: Path) -> None:
    path = tmp_path / "runs.sqlite"
    first = SQLiteRunStore(path)
    state = RunState(
        provider="scripted", model="test",
        provider_state=ProviderState(provider="scripted", previous_response_id="resp_99"),
    )
    await first.save(state)
    await first.close()

    second = SQLiteRunStore(path)
    loaded = await second.load(state.session_id)
    assert loaded is not None
    assert loaded.provider_state is not None
    assert loaded.provider_state.previous_response_id == "resp_99"
    await second.close()


async def test_sqlite_run_store_load_missing_returns_none(tmp_path: Path) -> None:
    store = SQLiteRunStore(tmp_path / "runs.sqlite")
    assert (await store.load("does-not-exist")) is None
    await store.close()


async def test_sqlite_run_store_list_runs(tmp_path: Path) -> None:
    store = SQLiteRunStore(tmp_path / "runs.sqlite")
    s1 = RunState(provider="a", model="m1")
    s2 = RunState(provider="b", model="m2")
    await store.save(s1)
    await store.save(s2)

    runs = await store.list_runs()
    ids = {r.session_id for r in runs}
    assert ids == {s1.session_id, s2.session_id}
    await store.close()
