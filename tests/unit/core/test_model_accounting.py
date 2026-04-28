from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent_runtime import AgentResult, AgentRuntime, ModelCacheControl, ModelPricing
from agent_runtime.core.accounting import (
    ModelUsage,
    usage_from_anthropic_message,
    usage_from_gemini_chunk,
)
from agent_runtime.providers.model_adapters.openai_responses import OpenAIResponsesProvider
from tests.fixtures.fake_openai_client import FakeOpenAIClient, evt, final_response, item
from tests.fixtures.scripted_model import (
    ScriptedModelProvider,
    text_only_turn,
    tool_call_turn,
)


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
        "cache_read_input_tokens": 20,
        "cache_creation_input_tokens": 0,
        "reasoning_tokens": 10,
        "tool_calls": 0,
    }
    assert result.metadata["cache"]["requested"] is False
    assert result.metadata["cache"]["hit"] is True
    assert result.metadata["accounting"]["usage"] == result.metadata["usage"]
    assert result.metadata["usage_provider_details"]["input_tokens"] == 100
    assert result.metadata["usage_provider_details"]["input_tokens_details"] == {
        "cached_tokens": 20
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


async def test_model_runtime_tracks_cache_records_for_explicit_cache_key() -> None:
    client = FakeOpenAIClient()
    msg = item("message", id_="msg_1")
    usage = SimpleNamespace(
        input_tokens=100,
        output_tokens=5,
        total_tokens=105,
        input_tokens_details=SimpleNamespace(cached_tokens=75),
    )
    client.queue(
        events=[
            evt("response.output_item.added", item=msg),
            evt("response.output_text.delta", delta="pong", item_id="msg_1"),
        ],
        final_response=final_response(id_="resp_1", output=[msg], usage=usage),
    )
    runtime = AgentRuntime()
    runtime.registry.register_model(OpenAIResponsesProvider(client=client))

    result = await runtime.models.run(
        provider="openai:gpt-test",
        input="ping",
        cache=ModelCacheControl(key="tenant-a", ttl="24h"),
    )
    record = await runtime.caches.get(
        provider="openai", model="gpt-test", key="tenant-a"
    )

    assert result.metadata["cache"]["requested"] is True
    assert result.metadata["cache"]["record"]["hits"] == 1
    assert record is not None
    assert record.hits == 1
    assert record.cache_read_input_tokens == 75


async def test_agent_runtime_usage_counts_tool_calls() -> None:
    provider = ScriptedModelProvider()
    provider.queue(tool_call_turn(call_id="call_1", name="lookup", arguments={"q": "x"}))
    provider.queue(text_only_turn("done"))
    runtime = AgentRuntime()
    runtime.registry.register_model(provider)
    runtime.tools.register(lambda q: f"found {q}", name="lookup")

    result: AgentResult[str] = await runtime.run(
        provider="scripted:test", input="search", tools=["lookup"]
    )

    assert result.metadata["usage"]["tool_calls"] == 1


async def test_model_runtime_collects_mcp_cache_metadata() -> None:
    client = FakeOpenAIClient()
    listed = item(
        "mcp_list_tools",
        id_="mcpl_1",
        server_label="github",
        tools=[{"name": "list_issues"}, {"name": "get_pr"}],
    )
    called = item(
        "mcp_call",
        id_="mcp_1",
        server_label="github",
        name="list_issues",
        arguments='{"state": "open"}',
        output="[]",
    )
    msg = item("message", id_="msg_1")
    client.queue(
        events=[
            evt("response.output_item.added", item=listed),
            evt("response.output_item.added", item=called),
            evt("response.output_item.done", item=called),
            evt("response.output_item.added", item=msg),
            evt("response.output_text.delta", delta="done", item_id="msg_1"),
        ],
        final_response=final_response(id_="resp_mcp", output=[listed, called, msg]),
    )

    runtime = AgentRuntime()
    runtime.registry.register_model(OpenAIResponsesProvider(client=client))

    result = await runtime.models.run(provider="openai:gpt-test", input="use mcp")

    assert result.metadata["mcp"] == {
        "tool_lists": [
            {
                "server_label": "github",
                "tool_count": 2,
                "tool_names": ["list_issues", "get_pr"],
                "item_id": "mcpl_1",
            }
        ],
        "calls": [
            {
                "server_label": "github",
                "name": "list_issues",
                "item_id": "mcp_1",
                "failed": False,
            }
        ],
        "approval_requests": [],
        "tool_list_cacheable": True,
        "context_item_ids": ["mcpl_1"],
    }


async def test_agent_runtime_result_metadata_includes_mcp_cache_metadata() -> None:
    client = FakeOpenAIClient()
    approval = item(
        "mcp_approval_request",
        id_="mcpr_1",
        server_label="github",
        name="delete_repo",
        arguments='{"repo": "danger"}',
    )
    msg = item("message", id_="msg_1")
    client.queue(
        events=[
            evt("response.output_item.added", item=approval),
            evt("response.output_item.added", item=msg),
            evt("response.output_text.delta", delta="needs approval", item_id="msg_1"),
        ],
        final_response=final_response(id_="resp_mcp_approval", output=[approval, msg]),
    )

    runtime = AgentRuntime()
    runtime.registry.register_model(OpenAIResponsesProvider(client=client))

    result: AgentResult[str] = await runtime.run(provider="openai:gpt-test", input="use mcp")

    assert result.metadata["mcp"]["approval_requests"] == [
        {"server_label": "github", "name": "delete_repo", "item_id": "mcpr_1"}
    ]


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
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
        "reasoning_tokens": 0,
        "tool_calls": 0,
    }
    assert estimate["total"] == 0.000183


def test_add_usage_keeps_missing_usage_absent() -> None:
    from agent_runtime.core.accounting import add_usage

    assert add_usage(None, None) is None


def test_usage_accumulates_split_cache_fields_and_estimates_split_cost() -> None:
    usage = ModelUsage(
        input_tokens=100,
        output_tokens=20,
        cache_read_input_tokens=25,
        cache_creation_input_tokens=5,
        reasoning_tokens=3,
        provider_details={"provider": "first"},
    )
    other = ModelUsage(
        input_tokens=50,
        output_tokens=10,
        cache_read_input_tokens=5,
        cache_creation_input_tokens=2,
        reasoning_tokens=1,
        provider_details={"provider": "second"},
    )
    pricing = ModelPricing(
        provider="p",
        model="m",
        input_per_million=1,
        output_per_million=2,
        cache_read_input_per_million=0.1,
        cache_creation_input_per_million=1.25,
        reasoning_output_per_million=3,
    )

    total = usage.add(other)
    estimate = pricing.estimate(total)

    assert total.to_dict() == {
        "input_tokens": 150,
        "output_tokens": 30,
        "total_tokens": 0,
        "cached_input_tokens": 37,
        "cache_read_input_tokens": 30,
        "cache_creation_input_tokens": 7,
        "reasoning_tokens": 4,
        "tool_calls": 0,
    }
    assert total.provider_details == {
        "turns": [{"provider": "first"}, {"provider": "second"}]
    }
    assert estimate["input"] == 0.000113
    assert estimate["cache_read_input"] == 0.000003
    assert estimate["cache_creation_input"] == 0.00000875
    assert estimate["output"] == 0.00006
    assert estimate["reasoning_output"] == 0.000012
    assert estimate["total"] == pytest.approx(0.00019675)


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
        "cache_read_input_tokens": 10,
        "cache_creation_input_tokens": 5,
        "reasoning_tokens": 0,
        "tool_calls": 0,
    }
    assert usage.provider_details["cache_read_input_tokens"] == 10


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
        "cache_read_input_tokens": 3,
        "cache_creation_input_tokens": 0,
        "reasoning_tokens": 7,
        "tool_calls": 0,
    }
    assert usage.provider_details["cached_content_token_count"] == 3
