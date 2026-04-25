from __future__ import annotations

from agent_runtime import AgentRuntime, EventTypes
from agent_runtime.models.echo import EchoModelProvider


async def test_echo_model_runtime_run() -> None:
    runtime = AgentRuntime()
    runtime.registry.register_model(EchoModelProvider())

    result = await runtime.models.run(provider="echo/echo-mini", input="hello")

    assert result.text == "hello"
    assert any(event.type == EventTypes.MODEL_TEXT_DELTA for event in result.events)


async def test_echo_model_runtime_stream() -> None:
    runtime = AgentRuntime()
    runtime.registry.register_model(EchoModelProvider())

    events = [event async for event in runtime.models.stream(provider="echo/echo-mini", input="hi")]

    assert events[0].type == EventTypes.MODEL_REQUEST_STARTED
    assert events[-1].type == EventTypes.MODEL_COMPLETED
