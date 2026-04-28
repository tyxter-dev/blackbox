from __future__ import annotations

from collections import OrderedDict
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from datetime import datetime
from hashlib import sha1
from typing import Any
from uuid import uuid4

from agent_runtime.core.events import AgentEvent, EventTypes


class SpanKinds:
    WORKFLOW = "workflow"
    SESSION = "session"
    MODEL = "model"
    REALTIME_SESSION = "realtime.session"
    REALTIME_TURN = "realtime.turn"
    REALTIME_RESPONSE = "realtime.response"
    REALTIME_MEDIA = "realtime.media"
    TOOL = "tool"
    AGENT_TOOL = "agent_tool"
    TOOL_SEARCH = "tool_search"
    HOSTED_TOOL = "hosted_tool"
    MCP = "mcp"
    CLOUD_AGENT = "cloud_agent"
    WORKSPACE_FILE = "workspace.file"
    WORKSPACE_COMMAND = "workspace.command"
    WORKSPACE_PATCH = "workspace.patch"
    APPROVAL = "approval"
    HANDOFF = "handoff"
    GUARDRAIL = "guardrail"
    ARTIFACT = "artifact"
    RETRY = "retry"
    EVAL = "eval"
    CUSTOM = "custom"


@dataclass(slots=True)
class Trace:
    id: str = field(default_factory=lambda: f"trace_{uuid4().hex}")
    events: list[AgentEvent] = field(default_factory=list)
    spans: list[TraceSpan] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def record(self, event: AgentEvent) -> None:
        self.events.append(event)

    def to_dict(self) -> dict[str, Any]:
        return {
            "trace_id": self.id,
            "spans": [span.to_dict() for span in self.spans],
            "events": [_event_ref(event) for event in self.events],
            "metadata": dict(self.metadata),
        }


@dataclass(slots=True, frozen=True)
class TraceSpan:
    """A compact tracing span derived from runtime events."""

    name: str
    kind: str
    span_id: str = field(default_factory=lambda: f"span_{uuid4().hex}")
    trace_id: str | None = None
    parent_span_id: str | None = None
    run_id: str | None = None
    provider: str | None = None
    model: str | None = None
    item_id: str | None = None
    status: str = "ok"
    started_at: datetime | None = None
    ended_at: datetime | None = None
    duration_ms: float | None = None
    attributes: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "span_id": self.span_id,
            "trace_id": self.trace_id,
            "parent_span_id": self.parent_span_id,
            "run_id": self.run_id,
            "name": self.name,
            "kind": self.kind,
            "provider": self.provider,
            "model": self.model,
            "item_id": self.item_id,
            "status": self.status,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "ended_at": self.ended_at.isoformat() if self.ended_at else None,
            "duration_ms": self.duration_ms,
            "attributes": dict(self.attributes),
        }


@dataclass(slots=True)
class TraceContext:
    """Stateful event stamper for one runtime workflow trace."""

    trace_id: str
    root_span_id: str = field(default_factory=lambda: f"span_{uuid4().hex}")
    _span_ids: dict[tuple[str, str], str] = field(default_factory=dict)

    @classmethod
    def for_run(cls, run_id: str, *, root_span_id: str | None = None) -> TraceContext:
        return cls(trace_id=run_id, root_span_id=root_span_id or f"span_{uuid4().hex}")

    def stamp(
        self,
        event: AgentEvent,
        *,
        run_id: str,
        sequence: int | None = None,
        preserve_sequence: bool = True,
    ) -> AgentEvent:
        kind = event.span_kind or infer_span_kind(event.type)
        if event.span_id is not None:
            self._span_ids.setdefault(_span_key(event, kind), event.span_id)
        span_id = event.span_id or self._span_id_for(event, kind)
        parent_span_id = event.parent_span_id
        if parent_span_id is None and span_id != self.root_span_id:
            parent_span_id = self.root_span_id

        provider_trace_id = event.provider_trace_id or _first_str(
            event.data, "provider_trace_id", "trace_id"
        )
        provider_span_id = event.provider_span_id or _first_str(
            event.data, "provider_span_id", "span_id"
        )
        provider_request_id = event.provider_request_id or _first_str(
            event.data,
            "provider_request_id",
            "request_id",
            "response_id",
            "event_id",
            "id",
        )

        return replace(
            event,
            run_id=event.run_id or run_id,
            sequence=(
                event.sequence
                if preserve_sequence and event.sequence is not None
                else sequence
            ),
            trace_id=event.trace_id or self.trace_id,
            span_id=span_id,
            parent_span_id=parent_span_id,
            span_kind=kind,
            provider_trace_id=provider_trace_id,
            provider_span_id=provider_span_id,
            provider_request_id=provider_request_id,
        )

    def _span_id_for(self, event: AgentEvent, kind: str) -> str:
        if kind in {SpanKinds.WORKFLOW, SpanKinds.SESSION} and event.type.startswith("run."):
            return self.root_span_id
        key = _span_key(event, kind)
        return self._span_ids.setdefault(key, f"span_{uuid4().hex}")


def infer_span_kind(event_type: str) -> str:
    if event_type.startswith("run."):
        return SpanKinds.WORKFLOW
    if event_type.startswith("session."):
        return SpanKinds.SESSION
    if event_type.startswith("model."):
        return SpanKinds.MODEL
    if event_type.startswith("realtime.session.") or event_type.startswith(
        "realtime.transport."
    ):
        return SpanKinds.REALTIME_SESSION
    if event_type.startswith("realtime.turn.") or event_type.startswith(
        "realtime.interruption."
    ):
        return SpanKinds.REALTIME_TURN
    if event_type.startswith("realtime.response."):
        return SpanKinds.REALTIME_RESPONSE
    if event_type.startswith("realtime.input.") or event_type.startswith(
        "realtime.output."
    ):
        if ".audio." in event_type or ".image." in event_type or ".video_frame." in event_type:
            return SpanKinds.REALTIME_MEDIA
        return SpanKinds.REALTIME_RESPONSE
    if event_type.startswith("realtime.history.") or event_type.startswith(
        "realtime.usage."
    ):
        return SpanKinds.REALTIME_SESSION
    if event_type.startswith("realtime.tool."):
        return SpanKinds.TOOL
    if event_type.startswith("tool.call."):
        return SpanKinds.TOOL
    if event_type.startswith("agent_tool.call."):
        return SpanKinds.AGENT_TOOL
    if event_type.startswith("tool.routing."):
        return SpanKinds.TOOL_SEARCH
    if event_type.startswith("tool_search."):
        return SpanKinds.TOOL_SEARCH
    if event_type.startswith("hosted_tool."):
        return SpanKinds.HOSTED_TOOL
    if event_type.startswith("mcp."):
        return SpanKinds.MCP
    if event_type.startswith("cloud_agent."):
        return SpanKinds.CLOUD_AGENT
    if event_type.startswith("workspace.command."):
        return SpanKinds.WORKSPACE_COMMAND
    if event_type.startswith("workspace.file."):
        return SpanKinds.WORKSPACE_FILE
    if event_type.startswith("workspace.patch."):
        return SpanKinds.WORKSPACE_PATCH
    if event_type.startswith("approval."):
        return SpanKinds.APPROVAL
    if event_type.startswith("handoff."):
        return SpanKinds.HANDOFF
    if event_type.startswith("guardrail."):
        return SpanKinds.GUARDRAIL
    if event_type.startswith("artifact."):
        return SpanKinds.ARTIFACT
    if event_type.startswith("retry."):
        return SpanKinds.RETRY
    if event_type.startswith("eval."):
        return SpanKinds.EVAL
    return SpanKinds.CUSTOM


def trace_from_events(
    events: Iterable[AgentEvent],
    *,
    metadata: dict[str, Any] | None = None,
    trace_id: str | None = None,
) -> Trace:
    ordered = _ordered_events(events)
    if not ordered:
        return Trace(id=trace_id or f"trace_{uuid4().hex}", metadata=dict(metadata or {}))

    effective_trace_id = (
        trace_id
        or ordered[0].trace_id
        or ordered[0].run_id
        or f"trace_{uuid4().hex}"
    )
    spans = spans_from_events(
        ordered,
        metadata=metadata,
        trace_id=effective_trace_id,
    )
    return Trace(
        id=effective_trace_id,
        events=ordered,
        spans=spans,
        metadata=dict(metadata or {}),
    )


def trace_metadata_from_events(
    events: Iterable[AgentEvent],
    *,
    metadata: dict[str, Any] | None = None,
    trace_id: str | None = None,
) -> dict[str, Any]:
    trace = trace_from_events(events, metadata=metadata, trace_id=trace_id)
    if not trace.spans:
        return {"trace_id": trace.id, "spans": []}
    root, *children = trace.spans
    return {
        "trace_id": trace.id,
        "root_span": root.to_dict(),
        "spans": [span.to_dict() for span in children],
    }


def spans_from_events(
    events: Iterable[AgentEvent],
    *,
    metadata: dict[str, Any] | None = None,
    trace_id: str | None = None,
) -> list[TraceSpan]:
    ordered = _ordered_events(events)
    if not ordered:
        return []

    effective_trace_id = trace_id or ordered[0].trace_id or ordered[0].run_id
    root_span_id = _root_span_id(ordered)
    root_span = _root_span(
        ordered,
        trace_id=effective_trace_id,
        span_id=root_span_id,
        metadata=metadata or {},
    )

    grouped: OrderedDict[str, list[AgentEvent]] = OrderedDict()
    for event in ordered:
        span_id = event.span_id or _legacy_span_id(event)
        if span_id == root_span_id:
            continue
        grouped.setdefault(span_id, []).append(event)

    spans = [root_span]
    spans.extend(
        _span_from_group(
            span_id,
            span_events,
            trace_id=effective_trace_id,
            root_span_id=root_span_id,
            metadata=metadata or {},
        )
        for span_id, span_events in grouped.items()
    )
    return spans


def model_turn_span_from_events(
    events: list[AgentEvent],
    *,
    provider: str | None,
    model: str | None,
    metadata: dict[str, Any],
) -> TraceSpan | None:
    """Build a single model-turn span from canonical model events."""

    if not events:
        return None
    started = _first_event(events, EventTypes.MODEL_REQUEST_STARTED) or events[0]
    completed = _last_event(events, EventTypes.MODEL_COMPLETED) or events[-1]
    duration_ms = (
        (completed.timestamp - started.timestamp).total_seconds() * 1000
        if completed.timestamp and started.timestamp
        else None
    )
    attributes: dict[str, Any] = {}
    for key in ("usage", "cost", "mcp", "hosted_tools"):
        if key in metadata:
            attributes[key] = metadata[key]
    return TraceSpan(
        name="model.turn",
        kind="model",
        span_id=started.span_id or f"span_{uuid4().hex}",
        trace_id=started.run_id,
        parent_span_id=started.parent_span_id,
        run_id=started.run_id,
        provider=provider or completed.provider or started.provider,
        model=model,
        item_id=started.item_id,
        status="ok" if _last_event(events, EventTypes.MODEL_COMPLETED) else "unknown",
        started_at=started.timestamp,
        ended_at=completed.timestamp,
        duration_ms=duration_ms,
        attributes=attributes,
    )


def _ordered_events(events: Iterable[AgentEvent]) -> list[AgentEvent]:
    return sorted(
        list(events),
        key=lambda event: (
            event.sequence is None,
            event.sequence if event.sequence is not None else 0,
            event.timestamp,
        ),
    )


def _root_span_id(events: list[AgentEvent]) -> str:
    for event in events:
        if event.span_kind == SpanKinds.WORKFLOW and event.span_id:
            return event.span_id
    for event in events:
        if event.parent_span_id:
            return event.parent_span_id
    return f"span_root_{events[0].run_id or uuid4().hex}"


def _root_span(
    events: list[AgentEvent],
    *,
    trace_id: str | None,
    span_id: str,
    metadata: dict[str, Any],
) -> TraceSpan:
    first = events[0]
    last = events[-1]
    attributes: dict[str, Any] = {
        "event_count": len(events),
        "event_types": sorted({event.type for event in events}),
    }
    for key in ("usage", "cost"):
        if key in metadata:
            attributes[key] = metadata[key]
    return TraceSpan(
        name="workflow.run",
        kind=SpanKinds.WORKFLOW,
        span_id=span_id,
        trace_id=trace_id,
        run_id=first.run_id,
        provider=first.provider,
        status=_status_from_events(events),
        started_at=first.timestamp,
        ended_at=last.timestamp,
        duration_ms=_duration_ms(first.timestamp, last.timestamp),
        attributes=attributes,
    )


def _span_from_group(
    span_id: str,
    events: list[AgentEvent],
    *,
    trace_id: str | None,
    root_span_id: str,
    metadata: dict[str, Any],
) -> TraceSpan:
    first = events[0]
    last = events[-1]
    kind = first.span_kind or infer_span_kind(first.type)
    attributes = _attributes_from_events(events)
    if kind == SpanKinds.MODEL:
        for key in ("usage", "cost", "mcp", "hosted_tools"):
            if key in metadata:
                attributes[key] = metadata[key]

    model = _first_str(first.data, "model")
    return TraceSpan(
        name=_span_name(kind, first),
        kind=kind,
        span_id=span_id,
        trace_id=trace_id or first.trace_id,
        parent_span_id=first.parent_span_id or root_span_id,
        run_id=first.run_id,
        provider=first.provider,
        model=model,
        item_id=first.item_id,
        status=_status_from_events(events),
        started_at=first.timestamp,
        ended_at=last.timestamp,
        duration_ms=_duration_ms(first.timestamp, last.timestamp),
        attributes=attributes,
    )


def _attributes_from_events(events: list[AgentEvent]) -> dict[str, Any]:
    first = events[0]
    attrs: dict[str, Any] = {
        "event_count": len(events),
        "event_ids": [event.id for event in events],
        "event_types": [event.type for event in events],
    }
    if first.session_id is not None:
        attrs["session_id"] = first.session_id
    for key in (
        "call_id",
        "name",
        "hosted_tool_type",
        "tool_name",
        "action",
        "approval_id",
        "artifact_id",
        "workspace_id",
    ):
        value = _first_str(first.data, key)
        if value is not None:
            attrs[key] = value
    if first.provider_trace_id is not None:
        attrs["provider_trace_id"] = first.provider_trace_id
    if first.provider_span_id is not None:
        attrs["provider_span_id"] = first.provider_span_id
    if first.provider_request_id is not None:
        attrs["provider_request_id"] = first.provider_request_id
    return attrs


def _span_name(kind: str, event: AgentEvent) -> str:
    if kind == SpanKinds.MODEL:
        return "model.turn"
    if kind == SpanKinds.REALTIME_SESSION:
        return "realtime.session"
    if kind == SpanKinds.REALTIME_TURN:
        return "realtime.turn"
    if kind == SpanKinds.REALTIME_RESPONSE:
        return "realtime.response"
    if kind == SpanKinds.REALTIME_MEDIA:
        return "realtime.media"
    if kind == SpanKinds.TOOL:
        return "tool.call"
    if kind == SpanKinds.AGENT_TOOL:
        return "agent_tool.call"
    if kind == SpanKinds.TOOL_SEARCH:
        return "tool_search"
    if kind == SpanKinds.HOSTED_TOOL:
        return "hosted_tool.call"
    if kind == SpanKinds.MCP:
        return "mcp.call"
    if kind == SpanKinds.CLOUD_AGENT:
        return "cloud_agent"
    if kind == SpanKinds.WORKSPACE_COMMAND:
        return "workspace.command"
    if kind == SpanKinds.WORKSPACE_FILE:
        return "workspace.file"
    if kind == SpanKinds.WORKSPACE_PATCH:
        return "workspace.patch"
    if kind == SpanKinds.APPROVAL:
        return "approval"
    if kind == SpanKinds.HANDOFF:
        return "handoff"
    if kind == SpanKinds.GUARDRAIL:
        return "guardrail"
    if kind == SpanKinds.ARTIFACT:
        return "artifact"
    if kind == SpanKinds.RETRY:
        return "retry"
    if kind == SpanKinds.EVAL:
        return "eval"
    return event.type


def _span_key(event: AgentEvent, kind: str) -> tuple[str, str]:
    for key in (
        "span_key",
        "call_id",
        "approval_id",
        "artifact_id",
        "checkpoint_id",
        "handoff_id",
        "guardrail_id",
        "eval_id",
        "tool_call_id",
        "request_id",
        "response_id",
    ):
        value = _first_str(event.data, key)
        if value is not None:
            return (kind, value)
    if event.item_id is not None:
        return (kind, event.item_id)
    if kind in {
        SpanKinds.WORKSPACE_FILE,
        SpanKinds.WORKSPACE_PATCH,
        SpanKinds.ARTIFACT,
        SpanKinds.CLOUD_AGENT,
    }:
        return (kind, event.id)
    return (kind, "default")


def _legacy_span_id(event: AgentEvent) -> str:
    kind = event.span_kind or infer_span_kind(event.type)
    key = "|".join(_span_key(event, kind))
    digest = sha1(key.encode("utf-8")).hexdigest()[:16]
    return f"span_legacy_{digest}"


def _status_from_events(events: list[AgentEvent]) -> str:
    event_types = {event.type for event in events}
    if any(event_type.endswith(".failed") for event_type in event_types):
        return "error"
    if any(event_type.endswith(".denied") for event_type in event_types):
        return "denied"
    if any(event_type.endswith(".cancelled") for event_type in event_types):
        return "cancelled"
    if any(event_type.endswith(".completed") for event_type in event_types):
        return "ok"
    if any(event_type.endswith(".started") for event_type in event_types):
        return "unknown"
    return "ok"


def _duration_ms(started_at: datetime | None, ended_at: datetime | None) -> float | None:
    if started_at is None or ended_at is None:
        return None
    return (ended_at - started_at).total_seconds() * 1000


def _event_ref(event: AgentEvent) -> dict[str, Any]:
    return {
        "id": event.id,
        "type": event.type,
        "run_id": event.run_id,
        "sequence": event.sequence,
        "trace_id": event.trace_id,
        "span_id": event.span_id,
        "parent_span_id": event.parent_span_id,
        "span_kind": event.span_kind,
        "timestamp": event.timestamp.isoformat(),
    }


def _first_str(data: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = data.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _first_event(events: list[AgentEvent], event_type: str) -> AgentEvent | None:
    return next((event for event in events if event.type == event_type), None)


def _last_event(events: list[AgentEvent], event_type: str) -> AgentEvent | None:
    return next((event for event in reversed(events) if event.type == event_type), None)
