"""Live Anthropic validation for prompt cache controls and cost tracking."""
from __future__ import annotations

import os

import pytest

from agent_runtime import AgentRuntime, ModelCacheControl, ModelPricing
from agent_runtime.providers.model_adapters.anthropic_messages import AnthropicMessagesProvider

pytestmark = pytest.mark.integration_anthropic


def _model() -> str:
    return os.environ.get("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")


def _runtime() -> AgentRuntime:
    runtime = AgentRuntime()
    runtime.registry.register_model(
        AnthropicMessagesProvider(api_key=os.environ["ANTHROPIC_API_KEY"])
    )
    runtime.model_catalog.register_pricing(
        ModelPricing(
            provider="anthropic",
            model=_model(),
            input_per_million=1.00,
            output_per_million=2.00,
            cache_read_input_per_million=0.10,
            cache_creation_input_per_million=1.25,
        )
    )
    return runtime


def _long_prompt() -> str:
    repeated = "Anthropic cache validation stable prefix with neutral documentation text. "
    return repeated * 260 + "\nReply with exactly: pong"


@pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY"),
    reason="ANTHROPIC_API_KEY not set",
)
async def test_real_ephemeral_cache_usage_and_cost_metadata() -> None:
    runtime = _runtime()
    cache = ModelCacheControl(strategy="ephemeral")

    try:
        first = await runtime.models.run(
            provider="anthropic",
            model=_model(),
            input=_long_prompt(),
            cache=cache,
            max_output_tokens=128,
        )
        second = await runtime.models.run(
            provider="anthropic",
            model=_model(),
            input=_long_prompt(),
            cache=cache,
            max_output_tokens=128,
        )
    finally:
        await runtime.close()

    assert first.metadata["usage"]["input_tokens"] > 1024
    assert "cost" in second.metadata
    assert second.metadata["cost"]["provider"] == "anthropic"
    assert second.metadata["cost"]["cache_creation_input"] >= 0
    assert second.metadata["cost"]["cache_read_input"] >= 0
    assert "usage_provider_details" in second.metadata
    if (
        first.metadata["usage"]["cache_creation_input_tokens"] == 0
        and second.metadata["usage"]["cache_read_input_tokens"] == 0
    ):
        pytest.skip("Anthropic accepted cache controls but did not report cache usage.")
