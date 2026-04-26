"""Live Gemini validation for cached content references and cost tracking."""
from __future__ import annotations

import os

import pytest

from agent_runtime import AgentRuntime, ModelCacheControl, ModelPricing
from agent_runtime.models.gemini_generate_content import GeminiGenerateContentProvider

pytestmark = pytest.mark.integration_gemini


def _api_key() -> str | None:
    return os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")


def _model() -> str:
    return os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")


def _runtime() -> AgentRuntime:
    runtime = AgentRuntime()
    runtime.registry.register_model(GeminiGenerateContentProvider(api_key=_api_key()))
    runtime.model_catalog.register_pricing(
        ModelPricing(
            provider="google",
            model=_model(),
            input_per_million=1.00,
            output_per_million=2.00,
            cache_read_input_per_million=0.10,
        )
    )
    return runtime


@pytest.mark.skipif(
    not _api_key(),
    reason="GOOGLE_API_KEY or GEMINI_API_KEY not set",
)
async def test_real_usage_and_cost_metadata() -> None:
    runtime = _runtime()

    try:
        result = await runtime.models.run(
            provider="google",
            model=_model(),
            input="Reply with exactly: pong",
            max_output_tokens=128,
        )
    finally:
        await runtime.close()

    assert "cost" in result.metadata
    assert result.metadata["cost"]["provider"] == "google"
    assert result.metadata["usage"]["input_tokens"] > 0
    assert result.metadata["usage"]["output_tokens"] >= 0
    assert "usage_provider_details" in result.metadata


@pytest.mark.skipif(
    not (_api_key() and os.environ.get("GEMINI_CACHED_CONTENT")),
    reason="GOOGLE_API_KEY/GEMINI_API_KEY and GEMINI_CACHED_CONTENT are required",
)
async def test_real_cached_content_reference_usage_and_cost_metadata() -> None:
    runtime = _runtime()

    try:
        result = await runtime.models.run(
            provider="google",
            model=_model(),
            input="Use the cached context and reply with exactly: pong",
            cache=ModelCacheControl(cached_content=os.environ["GEMINI_CACHED_CONTENT"]),
            max_output_tokens=128,
        )
    finally:
        await runtime.close()

    assert "cost" in result.metadata
    assert result.metadata["cost"]["provider"] == "google"
    assert result.metadata["usage"]["input_tokens"] >= 0
    assert result.metadata["usage"]["cache_read_input_tokens"] >= 0
    assert "usage_provider_details" in result.metadata
