from __future__ import annotations

from agent_runtime import AgentRuntime
from agent_runtime.core.events import EventTypes
from agent_runtime.realtime.fake import FakeRealtimeProvider


async def test_manual_tool_mode_only_emits_requested() -> None:
    runtime = AgentRuntime()
    provider = FakeRealtimeProvider(tool_name="lookup")
    runtime.registry.register_realtime(provider)
    runtime.tools.register(lambda text: f"local:{text}", name="lookup")

    async with await runtime.realtime.connect(
        provider="fake-realtime:gpt-realtime",
        tools=["lookup"],
        tool_mode="manual",
    ) as session:
        await session.send_text("abc")
        events = []
        async for event in session.events():
            events.append(event)
            if event.type == EventTypes.TOOL_CALL_REQUESTED:
                break

    assert [event.type for event in events].count(EventTypes.TOOL_CALL_REQUESTED) == 1
    assert EventTypes.TOOL_CALL_STARTED not in {event.type for event in events}
    assert not any(command.type == "tool_result" for command in provider.sent_commands)


async def test_auto_tool_mode_executes_and_sends_result() -> None:
    runtime = AgentRuntime()
    provider = FakeRealtimeProvider(tool_name="lookup")
    runtime.registry.register_realtime(provider)
    runtime.tools.register(lambda text: f"local:{text}", name="lookup")

    async with await runtime.realtime.connect(
        provider="fake-realtime:gpt-realtime",
        tools=["lookup"],
        tool_mode="auto",
    ) as session:
        await session.send_text("abc")
        events = []
        async for event in session.events():
            events.append(event)
            if event.type == EventTypes.REALTIME_RESPONSE_COMPLETED:
                break

    types = [event.type for event in events]
    assert EventTypes.TOOL_CALL_REQUESTED in types
    assert EventTypes.TOOL_CALL_STARTED in types
    assert EventTypes.TOOL_CALL_COMPLETED in types
    assert any(command.type == "tool_result" for command in provider.sent_commands)
