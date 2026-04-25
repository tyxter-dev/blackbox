from __future__ import annotations

from types import SimpleNamespace

from agent_runtime import AgentResult, AgentRuntime, ModelPricing
from agent_runtime.core.accounting import (
    ModelUsage,
    usage_from_anthropic_message,
    usage_from_gemini_chunk,
)
from agent_runtime.models.openai_responses import OpenAIResponsesProvider
from tests.fixtures.fake_openai_client import FakeOpenAIClient, evt, final_response, item


async def test_model_runtime_collects_usage_and_estimates_cost() -> None:
    client = FakeOpenAIClient()
    msg = item("message", id_="msg_1")
    usage = SimpleNamespace(
        input_tokens=100,
        output_tokens=50,
        total_tokens=150,
        input_tokens_details=SimpleNamespace(cached_tokens=20),
        output_tokens_details=SimpleNamespace(reasoning_tokens=10),
    )
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
            input_per_million=1.00,
            output_per_million=2.00,
            cached_input_per_million=0.10,
        )
    )

    result = await runtime.models.run(provider="openai:gpt-test", input="ping")

    assert result.metadata["usage"] == {
        "input_tokens": 100,
        "output_tokens": 50,
        "total_tokens": 150,
        "cached_input_tokens": 20,
        "reasoning_tokens": 10,
    }
    assert result.metadata["cost"]["total"] == 0.000182


async def test_agent_runtime_result_metadata_includes_usage_and_cost() -> None:
    client = FakeOpenAIClient()
    msg = item("message", id_="msg_1")
    usage = SimpleNamespace(input_tokens=10, output_tokens=5, total_tokens=15)
    client.queue(
        events=[
            evt("response.output_item.added", item=msg),
            evt("response.output_text.delta", delta="done", item_id="msg_1"),
            evt("response.output_item.done", item=msg),
        ],
        final_response=final_response(id_="resp_1", output=[msg], usage=usage),
    )

    runtime = AgentRuntime()
    runtime.registry.register_model(OpenAIResponsesProvider(client=client))
    runtime.model_catalog.register_pricing(
        ModelPricing(provider="openai", model="gpt-test", input_per_million=1, output_per_million=2)
    )

    result: AgentResult[str] = await runtime.run(provider="openai:gpt-test", input="ping")

    assert result.metadata["usage"]["total_tokens"] == 15
    assert result.metadata["cost"]["total"] == 0.00002


def test_usage_accumulates_and_estimates_cached_input_cost() -> None:
    usage = ModelUsage(input_tokens=100, output_tokens=20, cached_input_tokens=25)
    other = ModelUsage(input_tokens=50, output_tokens=10, cached_input_tokens=5)
    pricing = ModelPricing(
        provider="p",
        model="m",
        input_per_million=1,
        output_per_million=2,
        cached_input_per_million=0.1,
    )

    total = usage.add(other)
    estimate = pricing.estimate(total)

    assert total.to_dict() == {
        "input_tokens": 150,
        "output_tokens": 30,
        "total_tokens": 0,
        "cached_input_tokens": 30,
        "reasoning_tokens": 0,
    }
    assert estimate["total"] == 0.000183


def test_anthropic_usage_extraction_handles_cache_tokens() -> None:
    message = SimpleNamespace(
        usage=SimpleNamespace(
            input_tokens=100,
            output_tokens=25,
            cache_read_input_tokens=10,
            cache_creation_input_tokens=5,
        )
    )

    usage = usage_from_anthropic_message(message)

    assert usage is not None
    assert usage.to_dict() == {
        "input_tokens": 100,
        "output_tokens": 25,
        "total_tokens": 125,
        "cached_input_tokens": 15,
        "reasoning_tokens": 0,
    }


def test_gemini_usage_extraction_handles_thought_tokens() -> None:
    chunk = SimpleNamespace(
        usage_metadata=SimpleNamespace(
            prompt_token_count=30,
            candidates_token_count=12,
            total_token_count=45,
            cached_content_token_count=3,
            thoughts_token_count=7,
        )
    )

    usage = usage_from_gemini_chunk(chunk)

    assert usage is not None
    assert usage.to_dict() == {
        "input_tokens": 30,
        "output_tokens": 12,
        "total_tokens": 45,
        "cached_input_tokens": 3,
        "reasoning_tokens": 7,
    }
