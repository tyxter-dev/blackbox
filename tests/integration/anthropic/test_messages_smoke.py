"""Network-gated smoke test for the Anthropic Messages provider.

Run with::

    ANTHROPIC_API_KEY=... pytest -m integration_anthropic

Skipped automatically when the key is absent so the default suite stays offline.
"""
from __future__ import annotations

import os

import pytest

from blackbox import AgentRuntime, EventTypes
from blackbox.providers.model_adapters.anthropic_messages import AnthropicMessagesProvider

pytestmark = pytest.mark.integration_anthropic


@pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY"),
    reason="ANTHROPIC_API_KEY not set",
)
async def test_real_text_only_turn() -> None:
    runtime = AgentRuntime()
    runtime.registry.register_model(
        AnthropicMessagesProvider(api_key=os.environ["ANTHROPIC_API_KEY"])
    )

    result = await runtime.models.run(
        provider="anthropic",
        model="claude-haiku-4-5-20251001",
        input="Reply with exactly the word: pong",
    )

    assert "pong" in result.text.strip().lower()
    assert any(e.type == EventTypes.MODEL_TEXT_DELTA for e in result.events)
    assert result.provider_state is not None
    assert isinstance(result.provider_state.native_history, list)
    assert result.provider_state.native_history[-1]["role"] == "assistant"
