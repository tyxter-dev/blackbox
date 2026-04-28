"""Local agent driving a tool-call loop with the echo provider.

The example echoes a fake tool-call request and routes it through ToolRuntime.
Run::

    python examples/local_agent_with_tool.py
"""
from __future__ import annotations

# ruff: noqa: E402
import asyncio
from collections.abc import AsyncIterator, Iterable

from _bootstrap import bootstrap

bootstrap()

from agent_runtime import AgentRuntime, AgentSpec, EventTypes
from agent_runtime.core.events import AgentEvent
from agent_runtime.core.items import RunItem
from agent_runtime.core.state import ProviderState
from agent_runtime.providers.agent_adapters.local import LocalAgentProvider
from agent_runtime.providers.base import TurnRequest
from agent_runtime.tools import ToolRegistry, ToolResult, ToolRuntime


class _ScriptedModel:
    """Toy model: first turn requests a tool, second turn produces a final answer."""

    provider_id = "scripted"

    def __init__(self) -> None:
        self._calls = 0

    def capabilities(self, model: str | None = None):
        from agent_runtime.core.capabilities import ModelCapabilities
        return ModelCapabilities(supports_streaming_events=True, supports_function_tools=True)

    async def stream_turn(self, request: TurnRequest) -> AsyncIterator[AgentEvent]:
        self._calls += 1
        if self._calls == 1:
            for ev in _tool_call_events():
                yield ev
        else:
            for ev in _final_text_events(request.input):
                yield ev


def _tool_call_events() -> Iterable[AgentEvent]:
    yield AgentEvent(type=EventTypes.MODEL_REQUEST_STARTED, provider="scripted")
    yield AgentEvent(
        type=EventTypes.TOOL_CALL_REQUESTED,
        provider="scripted",
        item_id="call_1",
        data={"call_id": "call_1", "name": "add_one", "arguments": {"x": 41}},
    )
    yield AgentEvent(
        type=EventTypes.MODEL_COMPLETED,
        provider="scripted",
        data={"provider_state": ProviderState(provider="scripted")},
    )


def _final_text_events(input_payload):
    last = input_payload[-1] if isinstance(input_payload, list) else None
    summary = last.data["content"] if isinstance(last, RunItem) else "(no result)"
    yield AgentEvent(type=EventTypes.MODEL_REQUEST_STARTED, provider="scripted")
    yield AgentEvent(
        type=EventTypes.MODEL_TEXT_DELTA,
        provider="scripted",
        data={"delta": f"final answer: {summary}"},
    )
    yield AgentEvent(
        type=EventTypes.MODEL_COMPLETED,
        provider="scripted",
        data={"provider_state": ProviderState(provider="scripted")},
    )


async def main() -> None:
    registry = ToolRegistry()
    registry.register(
        lambda x: ToolResult(content=f"sum={x + 1}"),
        name="add_one",
        description="Return x + 1.",
    )
    tools = ToolRuntime(registry)

    runtime = AgentRuntime()
    runtime.registry.register_model(_ScriptedModel())
    runtime.registry.register_agent(LocalAgentProvider(runtime.models, tools=tools))

    await runtime.agents.create_agent(provider="local", spec=AgentSpec(name="default"))
    session = await runtime.agents.create_session(
        provider="local", agent="default", task="add one to 41", model="scripted/test",
    )

    async for event in runtime.agents.stream(session):
        if event.type in {
            EventTypes.SESSION_STARTED,
            EventTypes.TOOL_CALL_REQUESTED,
            EventTypes.TOOL_CALL_COMPLETED,
            EventTypes.SESSION_COMPLETED,
        }:
            print(f"{event.type:30}  {event.data}")
        elif event.type == EventTypes.MODEL_TEXT_DELTA:
            print(event.data["delta"])


if __name__ == "__main__":
    asyncio.run(main())
