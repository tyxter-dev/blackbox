from __future__ import annotations

from agent_runtime import AgentResult, AgentRuntime, EventTypes
from agent_runtime.core.events import AgentEvent
from agent_runtime.core.stores import (
    EventStore,
    EventStoreBatcher,
    InMemoryEventStore,
    InMemoryRunStore,
)
from tests.fixtures.scripted_model import ScriptedModelProvider, text_only_turn


async def test_in_memory_event_store_round_trip() -> None:
    store = InMemoryEventStore()
    a = AgentEvent(type=EventTypes.MODEL_TEXT_DELTA, run_id="run_1", sequence=0, data={"delta": "a"})
    b = AgentEvent(type=EventTypes.MODEL_TEXT_DELTA, run_id="run_1", sequence=1, data={"delta": "b"})
    c = AgentEvent(type=EventTypes.MODEL_TEXT_DELTA, run_id="run_2", sequence=0, data={"delta": "c"})

    for evt in (a, b, c):
        await store.append(evt)

    run_one = await store.list_events("run_1")
    assert [e.data["delta"] for e in run_one] == ["a", "b"]

    after = await store.list_events("run_1", after_sequence=0)
    assert [e.data["delta"] for e in after] == ["b"]


async def test_in_memory_event_store_append_many_batches_without_append_calls() -> None:
    class NoPerEventAppendStore(InMemoryEventStore):
        async def append(self, event: AgentEvent) -> None:
            raise AssertionError("append_many should not delegate per event")

    store = NoPerEventAppendStore()
    await store.append_many(
        [
            AgentEvent(type=EventTypes.MODEL_TEXT_DELTA, run_id="run_1", sequence=0),
            AgentEvent(type=EventTypes.MODEL_TEXT_DELTA, run_id="run_1", sequence=1),
            AgentEvent(type=EventTypes.MODEL_TEXT_DELTA, sequence=2),
        ]
    )

    stored = await store.list_events("run_1")
    assert [event.sequence for event in stored] == [0, 1]


async def test_event_store_batcher_flushes_on_batch_size_and_close() -> None:
    class CountingStore:
        def __init__(self) -> None:
            self.batches: list[list[AgentEvent]] = []

        async def append(self, event: AgentEvent) -> None:
            self.batches.append([event])

        async def append_many(self, events: list[AgentEvent]) -> None:
            self.batches.append(list(events))

        async def list_events(
            self,
            run_id: str,
            *,
            after_sequence: int | None = None,
            limit: int | None = None,
        ) -> list[AgentEvent]:
            return []

    store = CountingStore()
    batcher = EventStoreBatcher(store, max_batch_size=2)

    await batcher.append(AgentEvent(type=EventTypes.MODEL_TEXT_DELTA, run_id="run_1"))
    assert store.batches == []

    await batcher.append(AgentEvent(type=EventTypes.MODEL_TEXT_DELTA, run_id="run_1"))
    assert [len(batch) for batch in store.batches] == [2]

    await batcher.append(AgentEvent(type=EventTypes.MODEL_TEXT_DELTA, run_id="run_1"))
    await batcher.flush()

    assert [len(batch) for batch in store.batches] == [2, 1]


async def test_runtime_appends_events_to_store_during_run() -> None:
    runtime = AgentRuntime()
    scripted = ScriptedModelProvider()
    runtime.registry.register_model(scripted)
    scripted.queue(text_only_turn("ok"))

    result: AgentResult[str] = await runtime.run(provider="scripted/test", input="hi")
    run_id = result.events[0].run_id
    assert run_id is not None

    assert isinstance(runtime.event_store, EventStore)
    stored = await runtime.event_store.list_events(run_id)
    assert len(stored) == len(result.events)
    assert all(e.run_id == run_id for e in stored)
    sequences = [e.sequence for e in stored]
    assert sequences == sorted([s for s in sequences if s is not None])


async def test_in_memory_run_store_round_trip() -> None:
    from agent_runtime.core.state import RunState

    store = InMemoryRunStore()
    state = RunState(provider="scripted", model="test")
    await store.save(state)
    loaded = await store.load(state.session_id)
    assert loaded is state
    assert (await store.load("missing")) is None
