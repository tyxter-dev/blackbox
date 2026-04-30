from __future__ import annotations

import json

from blackbox.core.events import AgentEvent, EventTypes
from blackbox.core.raw import RawEnvelope
from blackbox.observability import MemoryMetricExporter, ObservabilityPreset


def test_production_preset_builds_redacted_jsonl_logging(tmp_path) -> None:
    log_path = tmp_path / "events.jsonl"

    preset = ObservabilityPreset.production(
        service_name="agent-service",
        traces="otlp",
        metrics="otlp",
        logs="jsonl",
        log_path=log_path,
    )

    assert preset.service_name == "agent-service"
    assert preset.traces_enabled is True
    assert preset.metrics_enabled is True
    assert preset.event_logging_enabled is True
    assert preset.metric_exporter is not None
    assert preset.redact is True
    assert preset.keep_raw_payloads is False
    assert preset.log_path == log_path


def test_memory_metric_backend_installs_memory_exporter(tmp_path) -> None:
    preset = ObservabilityPreset.production(
        service_name="agent-service",
        metrics="memory",
        logs="none",
        log_path=tmp_path / "unused.jsonl",
    )

    assert isinstance(preset.metric_exporter, MemoryMetricExporter)


async def test_production_jsonl_logging_drops_raw_payloads_by_default(tmp_path) -> None:
    log_path = tmp_path / "events.jsonl"
    preset = ObservabilityPreset.production(
        service_name="agent-service",
        logs="jsonl",
        log_path=log_path,
    )
    assert preset.event_sink is not None

    await preset.event_sink.emit(
        AgentEvent(
            type=EventTypes.MODEL_TEXT_DELTA,
            raw=RawEnvelope(
                provider="test",
                payload={"api_key": "sk-secret"},
                sensitivity="secret",
            ),
        )
    )

    log_text = log_path.read_text(encoding="utf-8")
    payload = json.loads(log_text)
    assert payload["raw"] is None
    assert "sk-secret" not in log_text


async def test_production_jsonl_logging_redacts_retained_raw_payloads(tmp_path) -> None:
    log_path = tmp_path / "events.jsonl"
    preset = ObservabilityPreset.production(
        service_name="agent-service",
        logs="jsonl",
        log_path=log_path,
        keep_raw_payloads=True,
    )
    assert preset.event_sink is not None

    await preset.event_sink.emit(
        AgentEvent(
            type=EventTypes.MODEL_TEXT_DELTA,
            raw=RawEnvelope(
                provider="test",
                payload={"api_key": "sk-secret"},
                sensitivity="secret",
            ),
        )
    )

    log_text = log_path.read_text(encoding="utf-8")
    payload = json.loads(log_text)
    assert payload["raw"]["payload"] == "<redacted>"
    assert payload["raw"]["redaction_status"] == "redacted"
    assert "sk-secret" not in log_text
