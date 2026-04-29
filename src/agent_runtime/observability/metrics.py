from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from importlib import import_module
from typing import Any, Protocol

from agent_runtime.core.events import AgentEvent, EventTypes
from agent_runtime.observability.traces import SpanKinds, Trace, TraceSpan

MetricAttribute = str | bool | int | float


@dataclass(slots=True, frozen=True)
class MetricRecord:
    """A backend-neutral metric emitted from runtime traces and metadata."""

    name: str
    value: int | float
    unit: str = "1"
    attributes: dict[str, MetricAttribute] = field(default_factory=dict)
    description: str | None = None


class MetricExporter(Protocol):
    """Protocol for sinks that export metric records."""

    def export(self, metrics: Iterable[MetricRecord]) -> None: ...


class MemoryMetricExporter:
    """Collect metric records in memory for tests and local diagnostics."""

    def __init__(self) -> None:
        self.records: list[MetricRecord] = []

    def export(self, metrics: Iterable[MetricRecord]) -> None:
        self.records.extend(metrics)


class OpenTelemetryMetricExporter:
    """Export metric records through the configured OpenTelemetry meter."""

    def __init__(
        self,
        *,
        meter: Any | None = None,
        meter_name: str = "agent_runtime",
    ) -> None:
        self._meter = meter
        self._meter_name = meter_name
        self._histograms: dict[tuple[str, str], Any] = {}

    def export(self, metrics: Iterable[MetricRecord]) -> None:
        meter = self._meter or _default_meter(self._meter_name)
        for metric in metrics:
            histogram = self._histogram(meter, metric)
            histogram.record(metric.value, attributes=metric.attributes)

    def _histogram(self, meter: Any, metric: MetricRecord) -> Any:
        key = (metric.name, metric.unit)
        existing = self._histograms.get(key)
        if existing is not None:
            return existing
        histogram = meter.create_histogram(
            metric.name,
            unit=metric.unit,
            description=metric.description or "",
        )
        self._histograms[key] = histogram
        return histogram


def metrics_from_trace(
    trace: Trace,
    *,
    metadata: dict[str, Any] | None = None,
    service_name: str | None = None,
) -> list[MetricRecord]:
    """Derive standardized production metrics from a trace and result metadata."""

    result: list[MetricRecord] = []
    attrs = _base_attributes(trace, service_name=service_name)
    events = _ordered_events(trace.events)
    metadata = dict(metadata or trace.metadata)

    if events:
        first = events[0]
        result.append(
            MetricRecord(
                "time_to_first_event",
                0,
                unit="ms",
                attributes=_event_attributes(first, attrs),
                description="Milliseconds until the first runtime event.",
            )
        )
        first_token_ms = _time_to_first_token_ms(events)
        if first_token_ms is not None:
            result.append(
                MetricRecord(
                    "time_to_first_token",
                    first_token_ms,
                    unit="ms",
                    attributes=_event_attributes(first, attrs),
                    description="Milliseconds until the first streamed token.",
                )
            )

    for span in trace.spans:
        latency_metric = _latency_metric_name(span)
        if latency_metric is None or span.duration_ms is None:
            continue
        result.append(
            MetricRecord(
                latency_metric,
                span.duration_ms,
                unit="ms",
                attributes=_span_attributes(span, attrs),
            )
        )

    result.extend(_metadata_metrics(metadata, attrs))
    result.extend(_error_metrics(events, attrs))
    return result


def _metadata_metrics(
    metadata: dict[str, Any],
    attrs: dict[str, MetricAttribute],
) -> list[MetricRecord]:
    result: list[MetricRecord] = []
    validation_attempts = _number(metadata.get("validation_attempts"))
    if validation_attempts is not None:
        result.append(
            MetricRecord("output_validation_attempts", validation_attempts, attributes=attrs)
        )

    retry_count = _number(metadata.get("retry_count"))
    if retry_count is None and validation_attempts is not None:
        retry_count = max(validation_attempts - 1, 0)
    if retry_count is not None:
        result.append(MetricRecord("retry_count", retry_count, attributes=attrs))

    usage = metadata.get("usage")
    if isinstance(usage, dict):
        for source, metric_name in (
            ("input_tokens", "input_tokens"),
            ("output_tokens", "output_tokens"),
            ("cached_input_tokens", "cached_tokens"),
        ):
            value = _number(usage.get(source))
            if value is not None:
                result.append(MetricRecord(metric_name, value, attributes=attrs))

    cache = metadata.get("cache")
    if isinstance(cache, dict):
        hit_rate = _number(cache.get("hit_ratio"))
        if hit_rate is None and isinstance(cache.get("hit"), bool):
            hit_rate = 1 if cache["hit"] else 0
        if hit_rate is not None:
            result.append(MetricRecord("cache_hit_rate", hit_rate, attributes=attrs))

    cost = metadata.get("cost")
    if isinstance(cost, dict):
        total = _number(cost.get("total"))
        if total is not None:
            result.append(
                MetricRecord(
                    "estimated_cost",
                    total,
                    unit=str(cost.get("currency") or "USD"),
                    attributes=attrs,
                )
            )
    return result


def _error_metrics(
    events: list[AgentEvent],
    attrs: dict[str, MetricAttribute],
) -> list[MetricRecord]:
    provider_events = [
        event
        for event in events
        if event.provider is not None or event.provider_request_id is not None
    ]
    if not provider_events:
        return []
    failed = sum(1 for event in provider_events if event.type.endswith(".failed"))
    return [
        MetricRecord(
            "provider_error_rate",
            failed / len(provider_events),
            attributes=attrs,
        )
    ]


def _latency_metric_name(span: TraceSpan) -> str | None:
    if span.name == "model.turn" or span.kind == SpanKinds.MODEL:
        return "model_latency_ms"
    if span.name == "tool.call" or span.kind in {
        SpanKinds.TOOL,
        SpanKinds.AGENT_TOOL,
        SpanKinds.HOSTED_TOOL,
    }:
        return "tool_latency_ms"
    if span.name == "mcp.list_tools":
        return "mcp_discovery_latency_ms"
    if span.name == "workspace.command" or span.kind == SpanKinds.WORKSPACE_COMMAND:
        return "workspace_command_latency_ms"
    return None


def _base_attributes(
    trace: Trace,
    *,
    service_name: str | None,
) -> dict[str, MetricAttribute]:
    attrs: dict[str, MetricAttribute] = {"agent_runtime.trace_id": trace.id}
    if service_name:
        attrs["service.name"] = service_name
    for span in trace.spans:
        if span.run_id is not None:
            attrs["agent_runtime.run_id"] = span.run_id
            break
    return attrs


def _span_attributes(
    span: TraceSpan,
    base: dict[str, MetricAttribute],
) -> dict[str, MetricAttribute]:
    attrs = dict(base)
    attrs["agent_runtime.span_id"] = span.span_id
    attrs["agent_runtime.span_kind"] = span.kind
    attrs["agent_runtime.span_name"] = span.name
    if span.provider is not None:
        attrs["gen_ai.system"] = span.provider
    if span.model is not None:
        attrs["gen_ai.request.model"] = span.model
    for key in (
        "session_id",
        "provider_request_id",
        "provider_trace_id",
        "provider_span_id",
    ):
        flattened = _flatten_attribute(span.attributes.get(key))
        if flattened is not None:
            attrs[f"agent_runtime.{key}"] = flattened
    return attrs


def _event_attributes(
    event: AgentEvent,
    base: dict[str, MetricAttribute],
) -> dict[str, MetricAttribute]:
    attrs = dict(base)
    if event.run_id is not None:
        attrs["agent_runtime.run_id"] = event.run_id
    if event.provider is not None:
        attrs["gen_ai.system"] = event.provider
    return attrs


def _ordered_events(events: Iterable[AgentEvent]) -> list[AgentEvent]:
    return sorted(
        list(events),
        key=lambda event: (
            event.sequence is None,
            event.sequence if event.sequence is not None else 0,
            event.timestamp,
        ),
    )


def _time_to_first_token_ms(events: list[AgentEvent]) -> float | None:
    started_at = events[0].timestamp
    token_types = {
        EventTypes.MODEL_TEXT_DELTA,
        EventTypes.REALTIME_OUTPUT_TEXT_DELTA,
        EventTypes.REALTIME_OUTPUT_AUDIO_DELTA,
    }
    for event in events:
        if event.type in token_types:
            return (event.timestamp - started_at).total_seconds() * 1000
    return None


def _number(value: Any) -> int | float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return value
    return None


def _default_meter(meter_name: str) -> Any:
    try:
        return import_module("opentelemetry.metrics").get_meter(meter_name)
    except ImportError as exc:  # pragma: no cover - depends on optional extra
        raise RuntimeError(
            "OpenTelemetry metric export requires the 'otel' optional dependency."
        ) from exc


def _flatten_attribute(value: Any) -> MetricAttribute | None:
    if value is None:
        return None
    if isinstance(value, str | bool | int | float):
        return value
    return json.dumps(value, default=str, sort_keys=True)
