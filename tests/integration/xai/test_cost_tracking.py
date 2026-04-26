"""Live xAI validation for OpenAI-compatible usage and cost tracking."""
from __future__ import annotations

import os

import pytest

from agent_runtime import AgentRuntime, ModelPricing
from agent_runtime.models.xai_responses import XAIResponsesProvider

pytestmark = pytest.mark.integration_xai


def _model() -> str:
    return os.environ.get("XAI_MODEL", "grok-4")


@pytest.mark.skipif(
    not os.environ.get("XAI_API_KEY"),
    reason="XAI_API_KEY not set",
)
async def test_real_usage_and_cost_metadata() -> None:
    runtime = AgentRuntime()
    runtime.registry.register_model(XAIResponsesProvider(api_key=os.environ["XAI_API_KEY"]))
    runtime.model_catalog.register_pricing(
        ModelPricing(
            provider="xai",
            model=_model(),
            input_per_million=1.00,
            output_per_million=2.00,
            cache_read_input_per_million=0.10,
        )
    )

    try:
        result = await runtime.models.run(
            provider="xai",
            model=_model(),
            input="Reply with exactly: pong",
            max_output_tokens=128,
        )
    finally:
        await runtime.close()

    assert "cost" in result.metadata
    assert result.metadata["cost"]["provider"] == "xai"
    assert result.metadata["usage"]["input_tokens"] > 0
    assert result.metadata["usage"]["output_tokens"] >= 0
    assert "usage_provider_details" in result.metadata
