from __future__ import annotations

from types import SimpleNamespace

import pytest

from blackbox import (
    BUNDLED_PRICING_CATALOG_VERSION,
    AgentRuntime,
    ModelPricing,
    bundled_model_catalog,
    bundled_provider_pricing,
)
from blackbox.providers.model_adapters.openai_responses import OpenAIResponsesProvider
from tests.fixtures.fake_openai_client import FakeOpenAIClient, evt, final_response, item


def test_bundled_provider_pricing_contains_common_models_with_provenance() -> None:
    pricing = bundled_provider_pricing()
    by_key = {(item.provider, item.model): item for item in pricing}

    openai = by_key[("openai", "gpt-5.4")]
    anthropic = by_key[("anthropic", "claude-haiku-4-5-20251001")]
    gemini = by_key[("google", "gemini-2.5-flash")]
    xai = by_key[("xai", "grok-4-1-fast-reasoning")]

    assert openai.input_per_million == 2.5
    assert openai.cached_input_per_million == 0.25
    assert anthropic.cache_creation_input_per_million == 1.25
    assert anthropic.cache_read_input_per_million == 0.1
    assert gemini.cached_input_per_million == 0.03
    assert xai.input_per_million == 1.25
    assert xai.output_per_million == 2.5
    assert {openai.source, anthropic.source, gemini.source, xai.source} == {"blackbox-bundled"}
    assert {
        openai.catalog_version,
        anthropic.catalog_version,
        gemini.catalog_version,
        xai.catalog_version,
    } == {BUNDLED_PRICING_CATALOG_VERSION}


async def test_agent_runtime_uses_bundled_provider_pricing_by_default() -> None:
    runtime = AgentRuntime()
    runtime.registry.register_model(OpenAIResponsesProvider(client=_usage_client()))

    result = await runtime.models.run(provider="openai:gpt-5.4", input="ping")

    assert result.metadata["provider_cost"]["source"] == "blackbox-bundled"
    assert result.metadata["provider_cost"]["total"] == 0.001
    assert result.metadata["cost"] == result.metadata["provider_cost"]


async def test_agent_runtime_uses_bundled_model_aliases_for_pricing() -> None:
    runtime = AgentRuntime()
    runtime.registry.register_model(OpenAIResponsesProvider(client=_usage_client()))

    result = await runtime.models.run(
        provider="openai:gpt-5.4-mini-2026-03-17",
        input="ping",
    )

    assert runtime.provider_model_catalog.contains(
        provider="openai",
        model="gpt-5.4-mini-2026-03-17",
    )
    assert result.metadata["provider_cost"]["model"] == "gpt-5.4-mini"
    assert result.metadata["provider_cost"]["total"] == 0.0003


async def test_agent_runtime_can_disable_bundled_provider_pricing() -> None:
    runtime = AgentRuntime(pricing=None)
    runtime.registry.register_model(OpenAIResponsesProvider(client=_usage_client()))

    result = await runtime.models.run(provider="openai:gpt-5.4", input="ping")

    assert "provider_cost" not in result.metadata
    assert "cost" not in result.metadata


async def test_user_pricing_overrides_bundled_provider_catalog() -> None:
    runtime = AgentRuntime()
    runtime.model_catalog.register_pricing(
        ModelPricing(
            provider="openai",
            model="gpt-5.4",
            input_per_million=1,
            output_per_million=2,
            source="tenant-contract",
        )
    )
    runtime.registry.register_model(OpenAIResponsesProvider(client=_usage_client()))

    result = await runtime.models.run(provider="openai:gpt-5.4", input="ping")

    assert result.metadata["provider_cost"]["source"] == "tenant-contract"
    assert result.metadata["provider_cost"]["total"] == 0.0002


def test_bundled_model_catalog_accepts_user_billable_pricing() -> None:
    catalog = bundled_model_catalog(
        extra_billable_pricing=[
            ModelPricing(
                provider="openai",
                model="gpt-5.4",
                input_per_million=10,
                output_per_million=20,
                source="tenant-billable",
            )
        ]
    )

    billable = catalog.estimate_billable(
        provider="openai",
        model="gpt-5.4",
        usage={"input_tokens": 100, "output_tokens": 50},
    )

    assert billable is not None
    assert billable["source"] == "tenant-billable"
    assert billable["total"] == 0.002


def test_bundled_model_catalog_resolves_provider_model_aliases() -> None:
    catalog = bundled_model_catalog()

    provider_cost = catalog.estimate_provider_cost(
        provider="xai",
        model="grok-4.20-non-reasoning",
        usage={"input_tokens": 100, "output_tokens": 50},
    )

    assert provider_cost is None

    openai_cost = catalog.estimate_provider_cost(
        provider="openai",
        model="gpt-5.4-mini-2026-03-17",
        usage={"input_tokens": 100, "output_tokens": 50},
    )

    assert openai_cost is not None
    assert openai_cost["model"] == "gpt-5.4-mini"
    assert openai_cost["total"] == 0.0003


def _usage_client() -> FakeOpenAIClient:
    client = FakeOpenAIClient()
    msg = item("message", id_="msg_1")
    usage = SimpleNamespace(input_tokens=100, output_tokens=50, total_tokens=150)
    client.queue(
        events=[
            evt("response.output_item.added", item=msg),
            evt("response.output_text.delta", delta="pong", item_id="msg_1"),
            evt("response.output_item.done", item=msg),
        ],
        final_response=final_response(id_="resp_1", output=[msg], usage=usage),
    )
    return client


def test_current_standard_rates_cache_semantics_and_alias_override() -> None:
    from blackbox.core.accounting import ModelUsage

    rows = {(row.provider, row.model): row for row in bundled_provider_pricing()}
    expected = {
        ("openai", "gpt-6-astra"): (10, 50),
        ("openai", "gpt-5.6-sol"): (4, 20),
        ("openai", "gpt-5.6-terra"): (2, 12),
        ("openai", "gpt-5.6-luna"): (0.2, 1.2),
        ("anthropic", "claude-fable-5-1"): (10, 50),
        ("anthropic", "claude-fable-5"): (10, 50),
        ("anthropic", "claude-opus-5"): (5, 25),
        ("anthropic", "claude-sonnet-5"): (2, 10),
        ("anthropic", "claude-opus-4-8"): (5, 25),
        ("xai", "grok-4.6"): (2, 6),
        ("xai", "grok-4.3"): (1.25, 2.5),
        ("xai", "grok-4-1-fast-reasoning"): (1.25, 2.5),
        ("xai", "grok-4-1-fast-non-reasoning"): (1.25, 2.5),
    }
    for key, rates in expected.items():
        row = rows[key]
        assert (row.input_per_million, row.output_per_million) == rates
        assert row.retrieved_at == "2026-09-05"
    fable = rows["anthropic", "claude-fable-5-1"]
    assert fable.cache_read_input_per_million == 0.25
    assert fable.cache_creation_input_per_million == 12.5
    assert (
        fable.estimate(ModelUsage(input_tokens=1_000_000, cache_read_input_tokens=1_000_000))[
            "total"
        ]
        == 0.25
    )
    assert rows["anthropic", "claude-fable-5"].cache_read_input_per_million == 1
    assert rows["xai", "grok-4.6"].cached_input_per_million == 0.5
    assert rows["google", "gemini-2.5-flash"].retrieved_at == "2026-05-06"
    custom = ModelPricing(
        provider="openai",
        model="gpt-5.6-sol",
        input_per_million=1,
        output_per_million=2,
        source="tenant",
    )
    catalog = bundled_model_catalog(extra_provider_pricing=[custom])
    cost = catalog.estimate_provider_cost(
        provider="openai", model="gpt-5.6", usage=ModelUsage(input_tokens=1_000_000)
    )
    assert cost is not None and cost["total"] == 1 and cost["source"] == "tenant"


async def test_native_cache_writes_flow_into_usage_and_standard_cost() -> None:
    runtime = AgentRuntime()
    client = FakeOpenAIClient()
    client.queue(
        [],
        final_response(
            id_="cache",
            usage=SimpleNamespace(
                input_tokens=1000,
                output_tokens=100,
                total_tokens=1100,
                input_tokens_details=SimpleNamespace(cached_tokens=200, cache_write_tokens=300),
            ),
        ),
    )
    runtime.registry.register_model(OpenAIResponsesProvider(client=client))
    result = await runtime.models.run(provider="openai:gpt-5.6-sol", input="ping")
    assert result.metadata["usage"]["input_tokens"] == 1000
    assert result.metadata["usage"]["cached_input_tokens"] == 500
    assert result.metadata["usage"]["cache_read_input_tokens"] == 200
    assert result.metadata["usage"]["cache_creation_input_tokens"] == 300
    assert (
        result.metadata["usage_provider_details"]["input_tokens_details"]["cache_write_tokens"]
        == 300
    )
    assert result.metadata["provider_cost"]["total"] == 0.00558


def test_native_missing_cache_write_preserves_prior_usage() -> None:
    from blackbox.core.accounting import usage_from_openai_response

    usage = usage_from_openai_response(
        final_response(
            id_="old", usage={"input_tokens": 1000, "input_tokens_details": {"cached_tokens": 200}}
        )
    )
    assert usage is not None
    assert usage.input_tokens == 1000
    assert usage.cached_input_tokens == 200
    assert usage.cache_read_input_tokens == 200
    assert usage.cache_creation_input_tokens == 0


async def test_anthropic_cache_usage_normalizes_inclusive_totals_and_aggregate_cost() -> None:
    from blackbox.core.accounting import usage_from_mapping
    from blackbox.providers.model_adapters.anthropic_messages import AnthropicMessagesProvider
    from tests.fixtures.fake_anthropic_client import FakeAnthropicClient, final_message

    runtime = AgentRuntime()
    client = FakeAnthropicClient()
    client.queue(
        [],
        final_message(
            id_="cached",
            usage={
                "input_tokens": 1000,
                "output_tokens": 100,
                "cache_read_input_tokens": 200,
                "cache_creation_input_tokens": 300,
            },
        ),
    )
    client.queue(
        [], final_message(id_="uncached", usage={"input_tokens": 100, "output_tokens": 10})
    )
    runtime.registry.register_model(AnthropicMessagesProvider(client=client))
    cached = await runtime.models.run(provider="anthropic:claude-sonnet-5", input="first")
    uncached = await runtime.models.run(provider="anthropic:claude-sonnet-5", input="second")
    assert cached.metadata["usage"]["input_tokens"] == 1500
    assert cached.metadata["usage"]["total_tokens"] == 1600
    assert cached.metadata["usage_provider_details"]["input_tokens"] == 1000
    assert uncached.metadata["usage"]["input_tokens"] == 100
    assert uncached.metadata["usage"]["total_tokens"] == 110
    assert uncached.metadata["usage"]["cached_input_tokens"] == 0
    price = cached.metadata["provider_cost"]
    assert price["input"] == 0.002
    assert price["cache_read_input"] == 0.00004
    assert price["cache_creation_input"] == 0.00075
    assert price["output"] == 0.001
    assert price["total"] == 0.00379
    total = usage_from_mapping(cached.metadata["usage"]).add(
        usage_from_mapping(uncached.metadata["usage"])
    )
    assert total.input_tokens == 1600
    assert total.total_tokens == 1710
    cost = runtime.model_catalog.estimate_provider_cost(
        provider="anthropic", model="claude-sonnet-5", usage=total
    )
    assert cost is not None and cost["total"] == pytest.approx(0.00409)
