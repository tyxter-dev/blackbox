from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime
from importlib import import_module
from typing import Any

from agent_runtime.observability.traces import Trace, TraceSpan

OTelAttribute = str | bool | int | float


class OpenTelemetryTraceExporter:
    """Export runtime traces through the installed OpenTelemetry SDK.

    The exporter is optional by design: importing this module does not require
    OpenTelemetry packages, but calling :meth:`export` does. Applications should
    configure the OpenTelemetry SDK/exporter pipeline before using this class.
    """

    def __init__(
        self,
        *,
        tracer: Any | None = None,
        tracer_name: str = "agent_runtime",
    ) -> None:
        self._tracer = tracer
        self._tracer_name = tracer_name

    def export(self, trace: Trace) -> None:
        tracer = self._tracer or _default_tracer(self._tracer_name)
        children: dict[str | None, list[TraceSpan]] = defaultdict(list)
        span_ids = {span.span_id for span in trace.spans}
        for span in trace.spans:
            parent_id = span.parent_span_id if span.parent_span_id in span_ids else None
            children[parent_id].append(span)
        for root in children[None]:
            self._export_span(tracer, root, children)

    def _export_span(
        self,
        tracer: Any,
        span: TraceSpan,
        children: dict[str | None, list[TraceSpan]],
    ) -> None:
        otel_trace = _otel_trace_api()
        status_cls, status_code_cls = _otel_status_api()
        started_at = _to_unix_ns(span.started_at)
        ended_at = _to_unix_ns(span.ended_at)
        otel_span = tracer.start_span(
            span.name,
            attributes=span_to_otel_attributes(span),
            start_time=started_at,
        )
        if span.status == "error":
            otel_span.set_status(status_cls(status_code_cls.ERROR))
        elif span.status in {"denied", "cancelled"}:
            otel_span.set_attribute("agent_runtime.status_detail", span.status)
        with otel_trace.use_span(otel_span, end_on_exit=False):
            for child in children.get(span.span_id, []):
                self._export_span(tracer, child, children)
        otel_span.end(end_time=ended_at)


def span_to_otel_attributes(span: TraceSpan) -> dict[str, OTelAttribute]:
    attrs: dict[str, OTelAttribute] = {
        "agent_runtime.trace_id": span.trace_id or "",
        "agent_runtime.span_id": span.span_id,
        "agent_runtime.span_kind": span.kind,
        "agent_runtime.status": span.status,
    }
    if span.parent_span_id is not None:
        attrs["agent_runtime.parent_span_id"] = span.parent_span_id
    if span.run_id is not None:
        attrs["agent_runtime.run_id"] = span.run_id
    if span.provider is not None:
        attrs["gen_ai.system"] = span.provider
    if span.model is not None:
        attrs["gen_ai.request.model"] = span.model
    if span.item_id is not None:
        attrs["agent_runtime.item_id"] = span.item_id
    if span.duration_ms is not None:
        attrs["agent_runtime.duration_ms"] = span.duration_ms

    for key, value in span.attributes.items():
        flattened = _flatten_attribute(value)
        if flattened is not None:
            attrs[f"agent_runtime.{key}"] = flattened
    return attrs


def _default_tracer(tracer_name: str) -> Any:
    return _otel_trace_api().get_tracer(tracer_name)


def _otel_trace_api() -> Any:
    try:
        return import_module("opentelemetry.trace")
    except ImportError as exc:  # pragma: no cover - depends on optional extra
        raise RuntimeError(
            "OpenTelemetry export requires the 'otel' optional dependency."
        ) from exc


def _otel_status_api() -> tuple[Any, Any]:
    try:
        module = import_module("opentelemetry.trace")
    except ImportError as exc:  # pragma: no cover - depends on optional extra
        raise RuntimeError(
            "OpenTelemetry export requires the 'otel' optional dependency."
        ) from exc
    return module.Status, module.StatusCode


def _to_unix_ns(value: datetime | None) -> int | None:
    if value is None:
        return None
    return int(value.timestamp() * 1_000_000_000)


def _flatten_attribute(value: Any) -> OTelAttribute | None:
    if value is None:
        return None
    if isinstance(value, str | bool | int | float):
        return value
    return json.dumps(value, default=str, sort_keys=True)
