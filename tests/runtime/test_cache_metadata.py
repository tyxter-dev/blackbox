from __future__ import annotations

import pytest

from blackbox import AgentEvent, AgentRuntime, EventTypes
from blackbox.providers.model_adapters.anthropic_messages import AnthropicMessagesProvider
from blackbox.providers.model_adapters.openai_responses import OpenAIResponsesProvider
from tests.fixtures.fake_anthropic_client import FakeAnthropicClient, final_message
from tests.fixtures.fake_openai_client import FakeOpenAIClient, final_response
from tests.fixtures.scripted_model import ScriptedModelProvider


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
@pytest.mark.parametrize("read", [200, 0])
async def test_native_cache_metadata_counts_only_reads_as_hits(provider: str, read: int) -> None:
    runtime = AgentRuntime()
    if provider == "openai":
        openai_client = FakeOpenAIClient()
        openai_client.queue(
            [],
            final_response(
                id_="cache",
                usage={
                    "input_tokens": 1000,
                    "output_tokens": 100,
                    "input_tokens_details": {"cached_tokens": read, "cache_write_tokens": 300},
                },
            ),
        )
        runtime.registry.register_model(OpenAIResponsesProvider(client=openai_client))
        model = "gpt-5.6-sol"
        inclusive_input = 1000
    else:
        anthropic_client = FakeAnthropicClient()
        anthropic_client.queue(
            [],
            final_message(
                id_="cache",
                usage={
                    "input_tokens": 1000,
                    "output_tokens": 100,
                    "cache_read_input_tokens": read,
                    "cache_creation_input_tokens": 300,
                },
            ),
        )
        runtime.registry.register_model(AnthropicMessagesProvider(client=anthropic_client))
        model = "claude-sonnet-5"
        inclusive_input = 1000 + read + 300
    result = await runtime.models.run(provider=f"{provider}:{model}", input="ping")
    cache = result.metadata["cache"]
    assert cache["input_tokens"] == inclusive_input
    assert cache["cached_input_tokens"] == read + 300
    assert cache["cache_creation_input_tokens"] == 300
    assert cache["hit"] is (read > 0)
    assert cache["hit_ratio"] == pytest.approx(read / inclusive_input)
    assert result.metadata["usage_provider_details"]["input_tokens"] == 1000


@pytest.mark.parametrize("input_tokens", [1000, 0, -1])
async def test_legacy_cache_metadata_retains_combined_fallback(input_tokens: int) -> None:
    runtime = AgentRuntime()
    provider = ScriptedModelProvider(
        scripts=[
            lambda request: [
                AgentEvent(
                    type=EventTypes.MODEL_COMPLETED,
                    provider="scripted",
                    data={
                        "model": request.model,
                        "usage": {"input_tokens": input_tokens, "cached_input_tokens": 200},
                    },
                )
            ]
        ]
    )
    runtime.registry.register_model(provider)
    result = await runtime.models.run(provider="scripted:legacy", input="ping")
    cache = result.metadata["cache"]
    assert cache["cache_read_input_tokens"] == 0
    assert cache["cache_creation_input_tokens"] == 0
    assert cache["hit"] is True
    if input_tokens > 0:
        assert cache["hit_ratio"] == 0.2
    else:
        assert "hit_ratio" not in cache
