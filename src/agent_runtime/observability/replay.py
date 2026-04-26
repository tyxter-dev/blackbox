from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agent_runtime.core.events import AgentEvent
from agent_runtime.core.stores import EventStore
from agent_runtime.observability.traces import Trace, TraceSpan, trace_from_events


@dataclass(slots=True, frozen=True)
class TraceReplay:
    run_id: str
    events: list[AgentEvent]
    trace: Trace

    def timeline(self) -> list[str]:
        lines: list[str] = []
        for event in self.events:
            timestamp = event.timestamp.isoformat()
            sequence = "" if event.sequence is None else f"#{event.sequence} "
            span = event.span_kind or "event"
            lines.append(f"{timestamp} {sequence}{span} {event.type}")
        return lines

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "trace": self.trace.to_dict(),
            "timeline": self.timeline(),
        }


@dataclass(slots=True, frozen=True)
class SpanDiff:
    key: str
    left_status: str | None = None
    right_status: str | None = None
    duration_ms_delta: float | None = None


@dataclass(slots=True, frozen=True)
class RunDiff:
    left_trace_id: str
    right_trace_id: str
    added_spans: list[str] = field(default_factory=list)
    removed_spans: list[str] = field(default_factory=list)
    status_changes: list[SpanDiff] = field(default_factory=list)
    duration_changes: list[SpanDiff] = field(default_factory=list)
    cost_delta: float | None = None

    def has_changes(self) -> bool:
        return bool(
            self.added_spans
            or self.removed_spans
            or self.status_changes
            or self.duration_changes
            or self.cost_delta not in {None, 0}
        )


async def replay_run(
    store: EventStore,
    run_id: str,
    *,
    metadata: dict[str, Any] | None = None,
) -> TraceReplay:
    events = await store.list_events(run_id)
    trace = trace_from_events(events, metadata=metadata)
    return TraceReplay(run_id=run_id, events=events, trace=trace)


def diff_traces(
    left: Trace,
    right: Trace,
    *,
    duration_threshold_ms: float = 0,
) -> RunDiff:
    left_spans = {_span_key(span): span for span in left.spans}
    right_spans = {_span_key(span): span for span in right.spans}
    left_keys = set(left_spans)
    right_keys = set(right_spans)

    status_changes: list[SpanDiff] = []
    duration_changes: list[SpanDiff] = []
    for key in sorted(left_keys & right_keys):
        left_span = left_spans[key]
        right_span = right_spans[key]
        if left_span.status != right_span.status:
            status_changes.append(
                SpanDiff(
                    key=key,
                    left_status=left_span.status,
                    right_status=right_span.status,
                )
            )
        delta = _duration_delta(left_span, right_span)
        if delta is not None and abs(delta) > duration_threshold_ms:
            duration_changes.append(SpanDiff(key=key, duration_ms_delta=delta))

    return RunDiff(
        left_trace_id=left.id,
        right_trace_id=right.id,
        added_spans=sorted(right_keys - left_keys),
        removed_spans=sorted(left_keys - right_keys),
        status_changes=status_changes,
        duration_changes=duration_changes,
        cost_delta=_cost_delta(left, right),
    )


def diff_runs(
    left_events: list[AgentEvent],
    right_events: list[AgentEvent],
    *,
    left_metadata: dict[str, Any] | None = None,
    right_metadata: dict[str, Any] | None = None,
    duration_threshold_ms: float = 0,
) -> RunDiff:
    return diff_traces(
        trace_from_events(left_events, metadata=left_metadata),
        trace_from_events(right_events, metadata=right_metadata),
        duration_threshold_ms=duration_threshold_ms,
    )


def _span_key(span: TraceSpan) -> str:
    item = span.item_id or span.attributes.get("call_id") or span.attributes.get("approval_id")
    suffix = f":{item}" if item else ""
    return f"{span.kind}:{span.name}{suffix}"


def _duration_delta(left: TraceSpan, right: TraceSpan) -> float | None:
    if left.duration_ms is None or right.duration_ms is None:
        return None
    return right.duration_ms - left.duration_ms


def _cost_delta(left: Trace, right: Trace) -> float | None:
    left_cost = _trace_cost(left)
    right_cost = _trace_cost(right)
    if left_cost is None or right_cost is None:
        return None
    return right_cost - left_cost


def _trace_cost(trace: Trace) -> float | None:
    cost = trace.metadata.get("cost")
    if not isinstance(cost, dict):
        return None
    value = cost.get("total")
    return float(value) if isinstance(value, int | float) else None
