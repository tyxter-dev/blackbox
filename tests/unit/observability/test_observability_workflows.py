from __future__ import annotations

from agent_runtime.core.events import AgentEvent, EventTypes
from agent_runtime.core.stores import InMemoryEventStore
from agent_runtime.observability import (
    EvaluationResult,
    OpenTelemetryTraceExporter,
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
    assert replay.trace.spans[0].name == "agent.run"
    assert replay.trace.spans[1].name == "model.turn"
    assert replay.timeline()[0].endswith("#0 model model.request.started")
    assert replay.to_dict()["trace_link"] == "trace://run_1"


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
    assert diff.to_dict()["trace_links"] == {
        "left": "trace://left",
        "right": "trace://right",
    }


def test_trace_spans_use_standard_names_and_correlation_attributes() -> None:
    events = [
        AgentEvent(
            type=EventTypes.MCP_LIST_TOOLS_STARTED,
            run_id="run_1",
            sequence=0,
            trace_id="run_1",
            span_id="span_mcp_list",
            parent_span_id="span_root",
            span_kind="mcp",
            session_id="sess_1",
            provider_request_id="req_1",
        ),
        AgentEvent(
            type=EventTypes.MCP_LIST_TOOLS_COMPLETED,
            run_id="run_1",
            sequence=1,
            trace_id="run_1",
            span_id="span_mcp_list",
            parent_span_id="span_root",
            span_kind="mcp",
            session_id="sess_1",
            provider_request_id="req_2",
        ),
        AgentEvent(
            type=EventTypes.MCP_CALL_STARTED,
            run_id="run_1",
            sequence=2,
            trace_id="run_1",
            span_id="span_mcp_call",
            parent_span_id="span_root",
            span_kind="mcp",
            data={"name": "lookup"},
        ),
    ]

    trace = trace_from_events(events)
    spans = {span.name: span for span in trace.spans}

    assert spans["agent.run"].attributes["session_id"] == "sess_1"
    assert spans["agent.run"].attributes["provider_request_ids"] == ["req_1", "req_2"]
    assert spans["mcp.list_tools"].events[0]["name"] == "mcp.list_tools"
    assert spans["mcp.call_tool"].events[0]["name"] == "mcp.call_tool"


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


def test_open_telemetry_exporter_projects_trace_events(monkeypatch) -> None:
    trace = trace_from_events(
        [
            AgentEvent(
                type=EventTypes.MODEL_TEXT_DELTA,
                run_id="run_1",
                sequence=0,
                trace_id="run_1",
                span_id="span_model",
                parent_span_id="span_root",
                span_kind="model",
                data={"delta": "hi"},
            )
        ]
    )
    fake_trace_api = _FakeTraceAPI()
    monkeypatch.setattr(
        "agent_runtime.observability.otel._otel_trace_api",
        lambda: fake_trace_api,
    )
    monkeypatch.setattr(
        "agent_runtime.observability.otel._otel_status_api",
        lambda: (_FakeStatus, _FakeStatusCode),
    )
    span = _FakeOTelSpan()
    exporter = OpenTelemetryTraceExporter(tracer=_FakeTracer(span))

    exporter.export(trace)

    assert span.events[0][0] == "model.stream"
    assert span.events[0][1]["agent_runtime.event.type"] == EventTypes.MODEL_TEXT_DELTA


class _FakeOTelSpan:
    def __init__(self) -> None:
        self.events = []

    def set_status(self, status) -> None:
        self.status = status

    def set_attribute(self, key, value) -> None:
        self.attribute = (key, value)

    def add_event(self, name, *, attributes, timestamp) -> None:
        self.events.append((name, attributes, timestamp))

    def end(self, *, end_time) -> None:
        self.end_time = end_time


class _FakeTracer:
    def __init__(self, span: _FakeOTelSpan) -> None:
        self.span = span

    def start_span(self, name, *, attributes, start_time):
        self.started = (name, attributes, start_time)
        return self.span


class _FakeTraceAPI:
    def use_span(self, span, *, end_on_exit):
        return _FakeContext()


class _FakeContext:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False


class _FakeStatus:
    def __init__(self, code) -> None:
        self.code = code


class _FakeStatusCode:
    ERROR = "error"
