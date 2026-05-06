from __future__ import annotations

from types import SimpleNamespace

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
    assert xai.input_per_million == 0.2
    assert xai.output_per_million == 0.5
    assert {openai.source, anthropic.source, gemini.source, xai.source} == {
        "blackbox-bundled"
    }
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
