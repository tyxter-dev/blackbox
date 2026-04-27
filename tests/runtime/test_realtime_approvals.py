from __future__ import annotations

import asyncio

from agent_runtime import AgentRuntime
from agent_runtime.core.approvals import ApprovalDecision
from agent_runtime.core.events import EventTypes
from agent_runtime.realtime.fake import FakeRealtimeProvider


async def test_realtime_auto_tool_approval_can_be_denied() -> None:
    runtime = AgentRuntime()
    provider = FakeRealtimeProvider(tool_name="lookup")
    runtime.registry.register_realtime(provider)
    runtime.tools.register(lambda text: f"local:{text}", name="lookup")

    async with await runtime.realtime.connect(
        provider="fake-realtime:gpt-realtime",
        tools=["lookup"],
        tool_mode="auto",
        approval_policy=lambda name, args: True,
    ) as session:
        await session.send_text("abc")
        events = []

        async def collect() -> None:
            async for event in session.events():
                events.append(event)
                if event.type == EventTypes.APPROVAL_REQUESTED:
                    await runtime.approve(
                        event.data["approval_id"],
                        ApprovalDecision.deny("no"),
                    )
                if event.type == EventTypes.TOOL_CALL_FAILED:
                    break

        await asyncio.wait_for(collect(), timeout=2)

    assert EventTypes.APPROVAL_REQUESTED in [event.type for event in events]
    assert EventTypes.APPROVAL_DENIED in [event.type for event in events]
    failed = next(event for event in events if event.type == EventTypes.TOOL_CALL_FAILED)
    assert failed.data["error"] == "denied_by_approval"
