from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol

from agent_runtime.core.events import AgentEvent
from agent_runtime.observability.metrics import (
    MemoryMetricExporter,
    MetricExporter,
    OpenTelemetryMetricExporter,
    metrics_from_trace,
)
from agent_runtime.observability.otel import OpenTelemetryTraceExporter
from agent_runtime.observability.sinks import (
    CompositeEventSink,
    EventSink,
    JSONLEventSink,
    RedactingEventSink,
)
from agent_runtime.observability.traces import Trace, trace_from_events

TraceBackend = Literal["none", "memory", "otlp"]
MetricBackend = Literal["none", "memory", "otlp"]
LogBackend = Literal["none", "jsonl"]


class TraceExporter(Protocol):
    """Protocol for trace exporters used by observability presets."""

    def export(self, trace: Trace) -> None: ...


class MemoryTraceExporter:
    """Collect exported traces in memory for tests and local diagnostics."""

    def __init__(self) -> None:
        self.traces: list[Trace] = []

    def export(self, trace: Trace) -> None:
        self.traces.append(trace)


@dataclass(slots=True, frozen=True)
class ObservabilityPreset:
    """Installable observability bundle for runtime events and run telemetry."""

    service_name: str
    traces: TraceBackend = "none"
    metrics: MetricBackend = "none"
    logs: LogBackend = "none"
    artifacts: str | None = None
    evaluations: str | None = None
    redact: bool = True
    event_sink: EventSink | None = None
    trace_exporter: TraceExporter | None = None
    metric_exporter: MetricExporter | None = None
    log_path: Path | None = None
    keep_raw_payloads: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def disabled(cls) -> ObservabilityPreset:
        return cls(service_name="agent-runtime")

    @classmethod
    def production(
        cls,
        *,
        service_name: str,
        traces: TraceBackend = "otlp",
        metrics: MetricBackend = "otlp",
        logs: LogBackend = "jsonl",
        artifacts: str | None = None,
        evaluations: str | None = None,
        redact: bool = True,
        log_path: str | Path | None = None,
        keep_raw_payloads: bool = False,
        extra_sinks: list[EventSink] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ObservabilityPreset:
        """Build a production preset with redacted event logging by default."""

        _validate_backend("traces", traces, {"none", "memory", "otlp"})
        _validate_backend("metrics", metrics, {"none", "memory", "otlp"})
        _validate_backend("logs", logs, {"none", "jsonl"})

        sinks = list(extra_sinks or [])
        resolved_log_path = Path(log_path) if log_path is not None else None
        if logs == "jsonl":
            resolved_log_path = resolved_log_path or Path("agent-runtime-events.jsonl")
            sinks.append(
                JSONLEventSink(
                    resolved_log_path,
                    keep_raw=keep_raw_payloads,
                    extra={"service_name": service_name},
                )
            )

        event_sink: EventSink | None
        if not sinks:
            event_sink = None
        elif len(sinks) == 1:
            event_sink = sinks[0]
        else:
            event_sink = CompositeEventSink(sinks)
        if redact and event_sink is not None:
            event_sink = RedactingEventSink(inner=event_sink)
        trace_exporter: TraceExporter | None = None
        if traces == "memory":
            trace_exporter = MemoryTraceExporter()
        elif traces == "otlp":
            trace_exporter = OpenTelemetryTraceExporter()
        metric_exporter: MetricExporter | None = None
        if metrics == "memory":
            metric_exporter = MemoryMetricExporter()
        elif metrics == "otlp":
            metric_exporter = OpenTelemetryMetricExporter()

        return cls(
            service_name=service_name,
            traces=traces,
            metrics=metrics,
            logs=logs,
            artifacts=artifacts,
            evaluations=evaluations,
            redact=redact,
            event_sink=event_sink,
            trace_exporter=trace_exporter,
            metric_exporter=metric_exporter,
            log_path=resolved_log_path,
            keep_raw_payloads=keep_raw_payloads,
            metadata=dict(metadata or {}),
        )

    @property
    def traces_enabled(self) -> bool:
        return self.traces != "none"

    @property
    def metrics_enabled(self) -> bool:
        return self.metrics != "none"

    @property
    def event_logging_enabled(self) -> bool:
        return self.event_sink is not None

    async def emit_event(self, event: AgentEvent) -> None:
        if self.event_sink is not None:
            await self.event_sink.emit(event)

    def export_run(
        self,
        events: list[AgentEvent],
        *,
        metadata: dict[str, Any] | None = None,
    ) -> Trace:
        trace = trace_from_events(events, metadata=metadata)
        if self.trace_exporter is not None:
            self.trace_exporter.export(trace)
        if self.metric_exporter is not None:
            self.metric_exporter.export(
                metrics_from_trace(
                    trace,
                    metadata=metadata,
                    service_name=self.service_name,
                )
            )
        return trace


def _validate_backend(name: str, value: str, allowed: set[str]) -> None:
    if value not in allowed:
        options = ", ".join(sorted(allowed))
        raise ValueError(f"Unsupported {name} backend {value!r}; expected one of {options}.")
