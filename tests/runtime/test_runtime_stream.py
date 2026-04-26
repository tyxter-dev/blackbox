"""AgentRuntime.stream() event correlation and parity tests.

The stream surface must produce the same events that .run() collects, plus
stamp every event with run_id and a monotonic sequence so consumers can
replay or correlate across the EventStore.
"""
from __future__ import annotations

from agent_runtime import AgentResult, AgentRuntime, EventTypes
from agent_runtime.tools import ToolResult
from tests.fixtures.scripted_model import (
    ScriptedModelProvider,
    text_only_turn,
    tool_call_turn,
)


def _runtime() -> tuple[AgentRuntime, ScriptedModelProvider]:
    runtime = AgentRuntime()
    scripted = ScriptedModelProvider()
    runtime.registry.register_model(scripted)
    return runtime, scripted


async def test_stream_yields_same_events_as_run_collects() -> None:
    runtime, scripted = _runtime()
    runtime.tools.register(
        lambda x: ToolResult(content=f"r{x}"),
        name="t",
        parameters={"type": "object", "properties": {"x": {"type": "integer"}}, "required": ["x"]},
    )
    scripted.queue(tool_call_turn(call_id="c1", name="t", arguments={"x": 1}))
    scripted.queue(text_only_turn("done"))

    streamed = [
        e async for e in runtime.stream(
            provider="scripted:test", input="x", tools=["t"],
        )
    ]
    types = [e.type for e in streamed]
    assert EventTypes.TOOL_CALL_REQUESTED in types
    assert EventTypes.TOOL_CALL_STARTED in types
    assert EventTypes.TOOL_CALL_COMPLETED in types
    assert EventTypes.MODEL_TEXT_DELTA in types


async def test_every_event_carries_run_id_and_monotonic_sequence() -> None:
    runtime, scripted = _runtime()
    scripted.queue(text_only_turn("ok"))

    events = [e async for e in runtime.stream(provider="scripted:test", input="hi")]

    run_ids = {e.run_id for e in events}
    assert len(run_ids) == 1
    assert next(iter(run_ids)) is not None

    from itertools import pairwise

    sequences = [e.sequence for e in events if e.sequence is not None]
    assert sequences == sorted(sequences)
    assert sequences[0] == 0
    assert all(b - a == 1 for a, b in pairwise(sequences))


async def test_every_event_carries_trace_context_and_tool_span_is_shared() -> None:
    runtime, scripted = _runtime()
    runtime.tools.register(
        lambda x: ToolResult(content=f"r{x}"),
        name="t",
        parameters={"type": "object", "properties": {"x": {"type": "integer"}}, "required": ["x"]},
    )
    scripted.queue(tool_call_turn(call_id="c1", name="t", arguments={"x": 1}))
    scripted.queue(text_only_turn("done"))

    events = [e async for e in runtime.stream(provider="scripted:test", input="x", tools=["t"])]

    run_id = events[0].run_id
    assert run_id is not None
    assert all(e.trace_id == run_id for e in events)
    assert all(e.span_id is not None for e in events)
    assert all(e.span_kind is not None for e in events)

    call_events = [
        e
        for e in events
        if e.data.get("call_id") == "c1"
        and e.type
        in {
            EventTypes.TOOL_CALL_REQUESTED,
            EventTypes.TOOL_CALL_STARTED,
            EventTypes.TOOL_CALL_COMPLETED,
        }
    ]
    assert {e.span_id for e in call_events} == {call_events[0].span_id}


async def test_run_and_stream_yield_correlated_run_ids() -> None:
    """run() collects from stream(); each invocation gets its own run_id."""
    runtime, scripted = _runtime()
    scripted.queue(text_only_turn("first"))
    scripted.queue(text_only_turn("second"))

    first: AgentResult[str] = await runtime.run(provider="scripted:test", input="a")
    second_events = [
        e async for e in runtime.stream(provider="scripted:test", input="b")
    ]

    first_run_id = first.events[0].run_id
    second_run_id = second_events[0].run_id
    assert first_run_id != second_run_id
    assert all(e.run_id == first_run_id for e in first.events)
    assert all(e.run_id == second_run_id for e in second_events)


async def test_result_metadata_includes_workflow_trace_tree() -> None:
    runtime, scripted = _runtime()
    runtime.tools.register(
        lambda x: ToolResult(content=f"r{x}"),
        name="t",
        parameters={"type": "object", "properties": {"x": {"type": "integer"}}, "required": ["x"]},
    )
    scripted.queue(tool_call_turn(call_id="c1", name="t", arguments={"x": 1}))
    scripted.queue(text_only_turn("done"))

    result: AgentResult[str] = await runtime.run(provider="scripted:test", input="x", tools=["t"])

    trace = result.metadata["trace"]
    assert trace["trace_id"] == result.events[0].run_id
    assert trace["root_span"]["name"] == "workflow.run"
    span_names = {span["name"] for span in trace["spans"]}
    assert {"model.turn", "tool.call"}.issubset(span_names)
