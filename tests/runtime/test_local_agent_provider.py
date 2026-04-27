"""LocalAgentProvider session lifecycle and tool/approval/cancel loop tests.

Combines the formerly separate test_local_agent.py (basic streaming) and
test_local_agent_tools.py (tool/approval flow) into one provider-focused
suite. Tests for the high-level runtime.run/.stream surface live in
test_runtime_run.py / test_runtime_stream.py.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from agent_runtime import AgentRuntime, AgentSpec, EventTypes
from agent_runtime.agents.local import LocalAgentProvider
from agent_runtime.core.approvals import ApprovalDecision
from agent_runtime.core.events import AgentEvent
from agent_runtime.core.sessions import AgentSession
from agent_runtime.models.echo import EchoModelProvider
from agent_runtime.tools import ToolRegistry, ToolResult, ToolRuntime
from tests.fixtures.scripted_model import (
    ScriptedModelProvider,
    text_only_turn,
    tool_call_turn,
)


def _build_runtime(
    *,
    tool_runtime: ToolRuntime | None = None,
    approval_policy: Any = None,
    max_iterations: int = 8,
) -> tuple[AgentRuntime, ScriptedModelProvider, LocalAgentProvider]:
    runtime = AgentRuntime()
    scripted = ScriptedModelProvider()
    runtime.registry.register_model(scripted)
    local = LocalAgentProvider(
        runtime.models,
        tools=tool_runtime,
        approval_policy=approval_policy,
        max_iterations=max_iterations,
    )
    runtime.registry.register_agent(local)
    return runtime, scripted, local


async def _drive_session(
    runtime: AgentRuntime, model: str = "scripted/test"
) -> tuple[list[AgentEvent], AgentSession]:
    await runtime.agents.create_agent(provider="local", spec=AgentSpec(name="default"))
    session = await runtime.agents.create_session(
        provider="local", agent="default", task="go", model=model,
    )
    return [event async for event in runtime.agents.stream(session)], session


# --- basic streaming through the agent facade -------------------------------

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


async def test_send_message_updates_session_and_returns_invocation_ref() -> None:
    runtime = AgentRuntime()
    runtime.registry.register_model(EchoModelProvider())
    runtime.registry.register_agent(LocalAgentProvider(runtime.models))

    await runtime.agents.create_agent(provider="local", spec=AgentSpec(name="default"))
    session = await runtime.agents.create_session(
        provider="local",
        agent="default",
        task="initial task",
        model="echo/echo-mini",
    )

    invocation = await runtime.agents.send_message(
        session,
        "follow-up request",
    )

    assert invocation.provider == "local"
    assert invocation.session_id == session.id
    assert invocation.id.startswith("inv_")
    assert session.task == "initial task\n\nUser: follow-up request"


# --- tool dispatch loop -----------------------------------------------------

async def test_tool_call_loop_dispatches_and_feeds_results_back() -> None:
    registry = ToolRegistry()
    registry.register(
        lambda x: ToolResult(content=f"sum={x + 1}"),
        name="add_one",
        description="Add one to x.",
    )
    tools = ToolRuntime(registry)

    runtime, scripted, _ = _build_runtime(tool_runtime=tools)
    scripted.queue(tool_call_turn(call_id="c1", name="add_one", arguments={"x": 41}))
    scripted.queue(text_only_turn("done: 42"))

    events, session = await _drive_session(runtime)

    types = [e.type for e in events]
    assert types[0] == EventTypes.SESSION_STARTED
    assert EventTypes.TOOL_CALL_REQUESTED in types
    assert EventTypes.TOOL_CALL_STARTED in types
    assert EventTypes.TOOL_CALL_COMPLETED in types
    assert types[-1] == EventTypes.SESSION_COMPLETED
    assert session.status == "completed"

    assert len(scripted.calls) == 2
    follow_up_input = scripted.calls[1].input
    assert isinstance(follow_up_input, list) and len(follow_up_input) == 1
    assert follow_up_input[0].data["content"] == "sum=42"


async def test_agent_spec_tools_and_instructions_reach_model_turn() -> None:
    registry = ToolRegistry()
    registry.register(lambda: ToolResult(content="ok"), name="allowed", description="allowed")
    registry.register(lambda: ToolResult(content="nope"), name="hidden", description="hidden")
    tools = ToolRuntime(registry)

    runtime, scripted, _ = _build_runtime(tool_runtime=tools)
    scripted.queue(text_only_turn("done"))

    await runtime.agents.create_agent(
        provider="local",
        spec=AgentSpec(
            name="tool-aware",
            instructions="Use local support tools when they help.",
            tools=["allowed"],
        ),
    )
    session = await runtime.agents.create_session(
        provider="local",
        agent="tool-aware",
        task="go",
        model="scripted/test",
    )

    events = [event async for event in runtime.agents.stream(session)]

    assert events[-1].type == EventTypes.SESSION_COMPLETED
    assert scripted.calls[0].controls.instructions == "Use local support tools when they help."
    assert [tool["name"] for tool in scripted.calls[0].tools] == ["allowed"]


async def test_tool_call_without_tool_runtime_raises() -> None:
    runtime, scripted, _ = _build_runtime()
    scripted.queue(tool_call_turn(call_id="c1", name="missing", arguments={}))

    await runtime.agents.create_agent(provider="local", spec=AgentSpec(name="default"))
    session = await runtime.agents.create_session(
        provider="local", agent="default", task="go", model="scripted/test",
    )
    try:
        async for _ in runtime.agents.stream(session):
            pass
    except Exception as exc:
        assert "ToolRuntime" in str(exc)
    else:
        raise AssertionError("expected SessionError")


# --- approval pause / resume ------------------------------------------------

async def test_approval_pause_and_approve() -> None:
    registry = ToolRegistry()
    registry.register(lambda: ToolResult(content="ok"), name="dangerous", description="d")
    tools = ToolRuntime(registry)

    runtime, scripted, local = _build_runtime(
        tool_runtime=tools,
        approval_policy=lambda name, args: name == "dangerous",
    )
    scripted.queue(tool_call_turn(call_id="c1", name="dangerous", arguments={}))
    scripted.queue(text_only_turn("done"))

    await runtime.agents.create_agent(provider="local", spec=AgentSpec(name="d"))
    session = await runtime.agents.create_session(
        provider="local", agent="d", task="go", model="scripted/test",
    )

    events: list[AgentEvent] = []
    stream = runtime.agents.stream(session)

    async def collector() -> None:
        async for event in stream:
            events.append(event)

    task = asyncio.create_task(collector())

    for _ in range(100):
        if local._approvals:
            break
        await asyncio.sleep(0.01)

    approval_id = next(iter(local._approvals))
    assert session.status == "waiting"
    await runtime.agents.approve(
        provider="local",
        approval_id=approval_id,
        decision=ApprovalDecision.approve("ok"),
    )

    await task
    types = [e.type for e in events]
    assert EventTypes.APPROVAL_REQUESTED in types
    assert EventTypes.APPROVAL_APPROVED in types
    assert EventTypes.TOOL_CALL_COMPLETED in types
    assert types[-1] == EventTypes.SESSION_COMPLETED


async def test_approval_denial_skips_tool_and_records_failure() -> None:
    registry = ToolRegistry()
    registry.register(lambda: ToolResult(content="ok"), name="dangerous", description="d")
    tools = ToolRuntime(registry)

    runtime, scripted, local = _build_runtime(
        tool_runtime=tools,
        approval_policy=lambda name, args: True,
    )
    scripted.queue(tool_call_turn(call_id="c1", name="dangerous", arguments={}))
    scripted.queue(text_only_turn("noop"))

    await runtime.agents.create_agent(provider="local", spec=AgentSpec(name="d"))
    session = await runtime.agents.create_session(
        provider="local", agent="d", task="go", model="scripted/test",
    )

    events: list[AgentEvent] = []
    stream = runtime.agents.stream(session)
    task = asyncio.create_task(_collect(stream, events))

    for _ in range(100):
        if local._approvals:
            break
        await asyncio.sleep(0.01)

    approval_id = next(iter(local._approvals))
    await runtime.agents.approve(
        provider="local",
        approval_id=approval_id,
        decision=ApprovalDecision.deny("blocked"),
    )

    await task
    types = [e.type for e in events]
    assert EventTypes.APPROVAL_DENIED in types
    assert EventTypes.TOOL_CALL_STARTED not in types
    follow_up_input = scripted.calls[1].input
    assert isinstance(follow_up_input, list)
    assert follow_up_input[0].data["error"] == "denied_by_approval"


# --- safety nets ------------------------------------------------------------

async def test_max_iterations_fails_session() -> None:
    registry = ToolRegistry()
    registry.register(lambda: ToolResult(content="loop"), name="loop", description="l")
    tools = ToolRuntime(registry)

    runtime, scripted, _ = _build_runtime(tool_runtime=tools, max_iterations=2)
    for _ in range(3):
        scripted.queue(tool_call_turn(call_id="x", name="loop", arguments={}))

    events, session = await _drive_session(runtime)
    assert session.status == "failed"
    assert events[-1].type == EventTypes.SESSION_FAILED
    assert events[-1].data["reason"] == "max_iterations_reached"


async def test_cancel_between_turns() -> None:
    registry = ToolRegistry()
    registry.register(lambda: ToolResult(content="x"), name="t", description="t")
    tools = ToolRuntime(registry)

    runtime, scripted, _ = _build_runtime(tool_runtime=tools)
    scripted.queue(tool_call_turn(call_id="c1", name="t", arguments={}))
    scripted.queue(text_only_turn("won't be reached"))

    await runtime.agents.create_agent(provider="local", spec=AgentSpec(name="d"))
    session = await runtime.agents.create_session(
        provider="local", agent="d", task="go", model="scripted/test",
    )

    events: list[AgentEvent] = []
    async for event in runtime.agents.stream(session):
        events.append(event)
        if event.type == EventTypes.TOOL_CALL_COMPLETED:
            await runtime.agents.cancel(session)

    types = [e.type for e in events]
    assert EventTypes.SESSION_CANCELLED in types
    assert types[-1] == EventTypes.SESSION_CANCELLED


async def _collect(
    stream: AsyncIterator[AgentEvent], sink: list[AgentEvent]
) -> None:
    async for event in stream:
        sink.append(event)
