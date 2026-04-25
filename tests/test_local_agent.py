from __future__ import annotations

from agent_runtime import AgentRuntime, AgentSpec, EventTypes
from agent_runtime.agents.local import LocalAgentProvider
from agent_runtime.models.echo import EchoModelProvider


async def test_local_agent_streams_model_events_inside_session() -> None:
    runtime = AgentRuntime()
    runtime.registry.register_model(EchoModelProvider())
    runtime.registry.register_agent(LocalAgentProvider(runtime.models))

    agent = await runtime.agents.create_agent(
        provider="local",
        spec=AgentSpec(name="default", instructions="Echo tasks."),
    )
    session = await runtime.agents.create_session(
        provider="local",
        agent=agent,
        task="hello agent",
        model="echo/echo-mini",
    )

    events = [event async for event in runtime.agents.stream(session)]

    assert events[0].type == EventTypes.SESSION_STARTED
    assert any(event.type == EventTypes.MODEL_TEXT_DELTA for event in events)
    assert events[-1].type == EventTypes.SESSION_COMPLETED
    assert events[-1].session_id == session.id
