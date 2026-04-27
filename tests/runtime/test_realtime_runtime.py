from __future__ import annotations

from agent_runtime import AgentRuntime
from agent_runtime.core.events import EventTypes
from agent_runtime.realtime.fake import FakeRealtimeProvider


async def test_fake_realtime_provider_connects_and_streams() -> None:
    runtime = AgentRuntime()
    runtime.registry.register_realtime(FakeRealtimeProvider())

    async with await runtime.realtime.connect(provider="fake-realtime:gpt-realtime") as session:
        await session.send_text("hello")
        events = []
        async for event in session.events():
            events.append(event)
            if event.type == EventTypes.REALTIME_RESPONSE_COMPLETED:
                break

    assert [event.sequence for event in events] == list(range(len(events)))
    assert events[0].run_id is not None
    assert events[0].provider == "fake-realtime"
    assert events[0].session_id == session.ref.id
    assert any(event.type == EventTypes.REALTIME_SESSION_CONNECTED for event in events)
    assert any(event.type == EventTypes.REALTIME_OUTPUT_TEXT_DELTA for event in events)
    stored = await runtime.event_store.list_events(events[0].run_id)
    assert [event.id for event in stored] == [event.id for event in events]


async def test_realtime_send_helpers_emit_input_events() -> None:
    runtime = AgentRuntime()
    runtime.registry.register_realtime(FakeRealtimeProvider())

    async with await runtime.realtime.connect(provider="fake-realtime:gpt-realtime") as session:
        await session.send_audio(b"abc")
        await session.commit_audio()
        await session.cancel_response("resp_1")
        await session.truncate_output(item_id="item_1", audio_end_ms=100)
        seen = []
        async for event in session.events():
            seen.append(event.type)
            if EventTypes.REALTIME_OUTPUT_TRUNCATED in seen:
                break

    assert EventTypes.REALTIME_INPUT_AUDIO_DELTA in seen
    assert EventTypes.REALTIME_INPUT_AUDIO_COMMITTED in seen
    assert EventTypes.REALTIME_RESPONSE_CANCELLED in seen
    assert EventTypes.REALTIME_OUTPUT_TRUNCATED in seen
