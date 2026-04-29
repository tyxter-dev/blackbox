from __future__ import annotations

from typing import cast

from agent_runtime import AgentResult, AgentRuntime, EventTypes, ObservabilityPreset
from agent_runtime.observability import (
    MemoryEventSink,
    MemoryMetricExporter,
    MemoryTraceExporter,
)
from agent_runtime.providers.agent_adapters.claude_code import ClaudeCodeAgentProvider
from tests.fixtures.fake_claude_code_client import FakeClaudeCodeClient
from tests.fixtures.scripted_model import ScriptedModelProvider, text_only_turn


async def test_runtime_observability_preset_records_each_high_level_event_once() -> None:
    sink = MemoryEventSink()
    preset = ObservabilityPreset.production(
        service_name="agent-service",
        traces="memory",
        metrics="memory",
        logs="none",
        extra_sinks=[sink],
    )
    runtime = AgentRuntime(observability=preset)
    scripted = ScriptedModelProvider()
    scripted.queue(text_only_turn("done"))
    runtime.registry.register_model(scripted)

    result: AgentResult[str] = await runtime.run(provider="scripted:test", input="ping")

    assert sink.events == result.events
    trace_exporter = cast(MemoryTraceExporter, preset.trace_exporter)
    assert trace_exporter.traces[0].id == result.events[0].run_id
    metric_exporter = cast(MemoryMetricExporter, preset.metric_exporter)
    assert {record.name for record in metric_exporter.records} >= {
        "time_to_first_event",
        "model_latency_ms",
    }


async def test_direct_model_runtime_uses_observability_preset() -> None:
    sink = MemoryEventSink()
    preset = ObservabilityPreset.production(
        service_name="agent-service",
        traces="memory",
        metrics="memory",
        logs="none",
        extra_sinks=[sink],
    )
    runtime = AgentRuntime(observability=preset)
    scripted = ScriptedModelProvider()
    scripted.queue(text_only_turn("pong"))
    runtime.registry.register_model(scripted)

    result = await runtime.models.run(provider="scripted:test", input="ping")

    assert sink.events == result.events
    assert cast(MemoryTraceExporter, preset.trace_exporter).traces
    assert cast(MemoryMetricExporter, preset.metric_exporter).records


async def test_agent_session_runtime_uses_observability_preset() -> None:
    sink = MemoryEventSink()
    preset = ObservabilityPreset.production(
        service_name="agent-service",
        traces="memory",
        metrics="memory",
        logs="none",
        extra_sinks=[sink],
    )
    client = FakeClaudeCodeClient(
        events=[
            {"id": "evt_1", "type": "status", "status": "running"},
            {"id": "evt_2", "type": "completed", "message": "done"},
        ]
    )
    runtime = AgentRuntime(observability=preset)
    runtime.registry.register_agent(ClaudeCodeAgentProvider(client=client))

    result = await runtime.agents.run(
        provider="claude-code",
        agent="repo-fixer",
        task="Fix failing tests",
    )

    assert sink.events == result.events
    assert [event.type for event in sink.events] == [
        EventTypes.CLOUD_AGENT_STATUS_CHANGED,
        EventTypes.SESSION_COMPLETED,
    ]
    assert cast(MemoryTraceExporter, preset.trace_exporter).traces
    assert cast(MemoryMetricExporter, preset.metric_exporter).records
