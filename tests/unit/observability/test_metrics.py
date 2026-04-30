from __future__ import annotations

from datetime import UTC, datetime, timedelta

from blackbox.core.events import AgentEvent, EventTypes
from blackbox.observability import (
    MemoryMetricExporter,
    MetricRecord,
    OpenTelemetryMetricExporter,
    metrics_from_trace,
    trace_from_events,
)


def test_metrics_from_trace_reports_standard_runtime_metrics() -> None:
    started_at = datetime(2026, 1, 1, tzinfo=UTC)
    events = [
        AgentEvent(
            type=EventTypes.MODEL_REQUEST_STARTED,
            run_id="run_1",
            sequence=0,
            trace_id="trace_1",
            span_id="span_model",
            parent_span_id="span_root",
            span_kind="model",
            provider="openai",
            data={"model": "gpt-test"},
            timestamp=started_at,
        ),
        AgentEvent(
            type=EventTypes.MODEL_TEXT_DELTA,
            run_id="run_1",
            sequence=1,
            trace_id="trace_1",
            span_id="span_model",
            parent_span_id="span_root",
            span_kind="model",
            provider="openai",
            data={"delta": "hello"},
            timestamp=started_at + timedelta(milliseconds=40),
        ),
        AgentEvent(
            type=EventTypes.MODEL_COMPLETED,
            run_id="run_1",
            sequence=2,
            trace_id="trace_1",
            span_id="span_model",
            parent_span_id="span_root",
            span_kind="model",
            provider="openai",
            data={"model": "gpt-test"},
            timestamp=started_at + timedelta(milliseconds=125),
        ),
    ]
    metadata = {
        "validation_attempts": 2,
        "usage": {
            "input_tokens": 100,
            "output_tokens": 25,
            "cached_input_tokens": 40,
        },
        "cache": {"hit": True, "hit_ratio": 0.4},
        "cost": {"total": 0.012, "currency": "USD"},
    }
    trace = trace_from_events(events, metadata=metadata, trace_id="trace_1")

    records = metrics_from_trace(trace, service_name="agent-service")
    by_name = {record.name: record for record in records}

    assert by_name["time_to_first_event"].value == 0
    assert by_name["time_to_first_token"].value == 40
    assert by_name["model_latency_ms"].value == 125
    assert by_name["output_validation_attempts"].value == 2
    assert by_name["retry_count"].value == 1
    assert by_name["cache_hit_rate"].value == 0.4
    assert by_name["input_tokens"].value == 100
    assert by_name["output_tokens"].value == 25
    assert by_name["cached_tokens"].value == 40
    assert by_name["estimated_cost"].value == 0.012
    assert by_name["provider_error_rate"].value == 0
    assert by_name["model_latency_ms"].attributes["service.name"] == "agent-service"


def test_memory_metric_exporter_collects_records() -> None:
    exporter = MemoryMetricExporter()
    records = [MetricRecord("retry_count", 1)]

    exporter.export(records)

    assert exporter.records == records


def test_open_telemetry_metric_exporter_records_histograms() -> None:
    histogram = _FakeHistogram()
    meter = _FakeMeter(histogram)
    exporter = OpenTelemetryMetricExporter(meter=meter)

    exporter.export([MetricRecord("model_latency_ms", 10, unit="ms")])

    assert meter.created == [("model_latency_ms", "ms", "")]
    assert histogram.records == [(10, {})]


class _FakeHistogram:
    def __init__(self) -> None:
        self.records = []

    def record(self, value, *, attributes) -> None:
        self.records.append((value, attributes))


class _FakeMeter:
    def __init__(self, histogram: _FakeHistogram) -> None:
        self.histogram = histogram
        self.created = []

    def create_histogram(self, name, *, unit, description):
        self.created.append((name, unit, description))
        return self.histogram
