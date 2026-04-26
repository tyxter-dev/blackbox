from __future__ import annotations

from agent_runtime.core.events import AgentEvent, EventTypes
from agent_runtime.core.stores import InMemoryEventStore
from agent_runtime.observability import (
    EvaluationResult,
    diff_traces,
    evaluate_trace,
    replay_run,
    span_to_otel_attributes,
    trace_from_events,
)


async def test_replay_run_reconstructs_trace_from_store() -> None:
    store = InMemoryEventStore()
    events = [
        AgentEvent(
            type=EventTypes.MODEL_REQUEST_STARTED,
            run_id="run_1",
            sequence=0,
            trace_id="run_1",
            span_id="span_model",
            parent_span_id="span_root",
            span_kind="model",
            provider="scripted",
            data={"model": "test"},
        ),
        AgentEvent(
            type=EventTypes.MODEL_COMPLETED,
            run_id="run_1",
            sequence=1,
            trace_id="run_1",
            span_id="span_model",
            parent_span_id="span_root",
            span_kind="model",
            provider="scripted",
        ),
    ]
    for event in events:
        await store.append(event)

    replay = await replay_run(store, "run_1")

    assert replay.trace.id == "run_1"
    assert replay.trace.spans[0].name == "workflow.run"
    assert replay.trace.spans[1].name == "model.turn"
    assert replay.timeline()[0].endswith("#0 model model.request.started")


def test_diff_traces_reports_status_and_duration_changes() -> None:
    left = trace_from_events(
        [
            AgentEvent(
                type=EventTypes.TOOL_CALL_STARTED,
                run_id="left",
                sequence=0,
                trace_id="left",
                span_id="span_tool",
                parent_span_id="span_root",
                span_kind="tool",
                item_id="call_1",
                data={"call_id": "call_1", "name": "lookup"},
            ),
            AgentEvent(
                type=EventTypes.TOOL_CALL_COMPLETED,
                run_id="left",
                sequence=1,
                trace_id="left",
                span_id="span_tool",
                parent_span_id="span_root",
                span_kind="tool",
                item_id="call_1",
                data={"call_id": "call_1", "name": "lookup"},
            ),
        ]
    )
    right = trace_from_events(
        [
            AgentEvent(
                type=EventTypes.TOOL_CALL_STARTED,
                run_id="right",
                sequence=0,
                trace_id="right",
                span_id="span_tool",
                parent_span_id="span_root",
                span_kind="tool",
                item_id="call_1",
                data={"call_id": "call_1", "name": "lookup"},
            ),
            AgentEvent(
                type=EventTypes.TOOL_CALL_FAILED,
                run_id="right",
                sequence=1,
                trace_id="right",
                span_id="span_tool",
                parent_span_id="span_root",
                span_kind="tool",
                item_id="call_1",
                data={"call_id": "call_1", "name": "lookup"},
            ),
        ]
    )

    diff = diff_traces(left, right)

    assert diff.has_changes()
    assert diff.status_changes[0].left_status == "ok"
    assert diff.status_changes[0].right_status == "error"


async def test_evaluate_trace_emits_eval_events() -> None:
    trace = trace_from_events(
        [
            AgentEvent(
                type=EventTypes.MODEL_COMPLETED,
                run_id="run_1",
                sequence=0,
                trace_id="run_1",
                span_id="span_model",
                parent_span_id="span_root",
                span_kind="model",
            )
        ]
    )

    async def evaluator(candidate: object) -> EvaluationResult:
        assert candidate is trace
        return EvaluationResult(name="has-model", passed=True, score=1.0)

    report = await evaluate_trace(trace, [evaluator])

    assert report.passed is True
    assert [event.type for event in report.events] == [
        EventTypes.EVAL_STARTED,
        EventTypes.EVAL_COMPLETED,
    ]
    assert {event.span_id for event in report.events if event.span_kind == "eval"}


def test_span_to_otel_attributes_flattens_nested_values() -> None:
    trace = trace_from_events(
        [
            AgentEvent(
                type=EventTypes.MODEL_COMPLETED,
                run_id="run_1",
                sequence=0,
                trace_id="run_1",
                span_id="span_model",
                parent_span_id="span_root",
                span_kind="model",
                provider="scripted",
                data={"model": "test"},
            )
        ],
        metadata={"usage": {"total_tokens": 3}},
    )

    attrs = span_to_otel_attributes(trace.spans[1])

    assert attrs["agent_runtime.trace_id"] == "run_1"
    assert attrs["agent_runtime.span_kind"] == "model"
    assert attrs["gen_ai.system"] == "scripted"
    assert attrs["agent_runtime.usage"] == '{"total_tokens": 3}'
