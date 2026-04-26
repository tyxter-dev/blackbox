from __future__ import annotations

from collections.abc import AsyncIterator
from types import SimpleNamespace

from agent_runtime import AgentResult, AgentRuntime, ModelPricing
from agent_runtime.core.capabilities import ModelCapabilities
from agent_runtime.core.events import AgentEvent, EventTypes
from agent_runtime.models.openai_responses import OpenAIResponsesProvider
from agent_runtime.providers.base import TurnRequest
from tests.fixtures.fake_openai_client import FakeOpenAIClient, evt, final_response, item


async def test_model_runtime_metadata_includes_model_turn_trace_span() -> None:
    client = FakeOpenAIClient()
    msg = item("message", id_="msg_1")
    usage = SimpleNamespace(input_tokens=8, output_tokens=3, total_tokens=11)
    client.queue(
        events=[
            evt("response.output_item.added", item=msg),
            evt("response.output_text.delta", delta="pong", item_id="msg_1"),
            evt("response.output_item.done", item=msg),
        ],
        final_response=final_response(id_="resp_1", output=[msg], usage=usage),
    )

    runtime = AgentRuntime()
    runtime.registry.register_model(OpenAIResponsesProvider(client=client))
    runtime.model_catalog.register_pricing(
        ModelPricing(
            provider="openai",
            model="gpt-test",
            input_per_million=1,
            output_per_million=2,
        )
    )

    result = await runtime.models.run(provider="openai:gpt-test", input="ping")

    span = result.metadata["trace"]["spans"][0]
    assert span["name"] == "model.turn"
    assert span["kind"] == "model"
    assert span["provider"] == "openai"
    assert span["model"] == "gpt-test"
    assert span["status"] == "ok"
    assert span["duration_ms"] >= 0
    assert span["attributes"]["usage"]["total_tokens"] == 11
    assert span["attributes"]["cost"]["provider"] == "openai"


async def test_agent_runtime_trace_span_uses_stream_run_id() -> None:
    runtime = AgentRuntime()
    scripted = _SingleTurnProvider()
    runtime.registry.register_model(scripted)

    result: AgentResult[str] = await runtime.run(provider="single:test", input="ping")

    span = result.metadata["trace"]["spans"][0]
    assert span["run_id"] == result.events[0].run_id
    assert span["trace_id"] == result.events[0].run_id


class _SingleTurnProvider:
    provider_id = "single"

    def capabilities(self, model: str | None = None) -> ModelCapabilities:
        return ModelCapabilities(supports_streaming_events=True)

    async def stream_turn(self, request: TurnRequest) -> AsyncIterator[AgentEvent]:
        from agent_runtime.core.state import ProviderState

        yield AgentEvent(type=EventTypes.MODEL_REQUEST_STARTED, provider="single")
        yield AgentEvent(
            type=EventTypes.MODEL_TEXT_DELTA,
            provider="single",
            data={"delta": "done"},
        )
        yield AgentEvent(
            type=EventTypes.MODEL_COMPLETED,
            provider="single",
            data={"model": "test", "provider_state": ProviderState(provider="single")},
        )
