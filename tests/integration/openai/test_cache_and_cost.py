"""Live OpenAI validation for model cache controls and cost tracking."""
from __future__ import annotations

import os

import pytest

from agent_runtime import AgentRuntime, ModelCacheControl, ModelPricing
from agent_runtime.models.openai_responses import OpenAIResponsesProvider

pytestmark = pytest.mark.integration_openai


def _runtime() -> AgentRuntime:
    runtime = AgentRuntime()
    runtime.registry.register_model(
        OpenAIResponsesProvider(api_key=os.environ["OPENAI_API_KEY"])
    )
    runtime.model_catalog.register_pricing(
        ModelPricing(
            provider="openai",
            model=_model(),
            input_per_million=1.00,
            output_per_million=2.00,
            cache_read_input_per_million=0.10,
        )
    )
    return runtime


def _model() -> str:
    return os.environ.get("OPENAI_CACHE_TEST_MODEL", "gpt-4o-mini")


def _long_prompt() -> str:
    repeated = "Cache validation sentence with stable prefix and harmless content. "
    return repeated * 260 + "\nReply with exactly: pong"


@pytest.mark.skipif(
    not os.environ.get("OPENAI_API_KEY"),
    reason="OPENAI_API_KEY not set",
)
async def test_real_prompt_cache_key_usage_and_cost_metadata() -> None:
    runtime = _runtime()
    cache = ModelCacheControl(
        key="agent-runtime-integration-cache",
        ttl="24h",
    )

    try:
        first = await runtime.models.run(
            provider="openai",
            model=_model(),
            input=_long_prompt(),
            cache=cache,
            max_output_tokens=128,
        )
        second = await runtime.models.run(
            provider="openai",
            model=_model(),
            input=_long_prompt(),
            cache=cache,
            max_output_tokens=128,
        )
    finally:
        await runtime.close()

    assert first.metadata["usage"]["input_tokens"] > 1024
    assert second.metadata["usage"]["input_tokens"] > 1024
    assert "cost" in second.metadata
    assert second.metadata["cost"]["provider"] == "openai"
    assert second.metadata["cost"]["cache_read_input"] >= 0
    assert "usage_provider_details" in second.metadata
