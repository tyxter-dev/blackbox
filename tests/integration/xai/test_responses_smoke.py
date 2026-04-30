"""Network-gated smoke test for the xAI Responses provider.

Run with::

    XAI_API_KEY=... pytest -m integration_xai

Skipped automatically when the key is absent so the default suite stays offline.
"""
from __future__ import annotations

import os

import pytest

from blackbox import AgentRuntime, EventTypes
from blackbox.providers.model_adapters.xai_responses import XAIResponsesProvider

pytestmark = pytest.mark.integration_xai


@pytest.mark.skipif(
    not os.environ.get("XAI_API_KEY"),
    reason="XAI_API_KEY not set",
)
async def test_real_text_only_turn() -> None:
    runtime = AgentRuntime()
    runtime.registry.register_model(XAIResponsesProvider(api_key=os.environ["XAI_API_KEY"]))

    result = await runtime.models.run(
        provider="xai",
        model=os.environ.get("XAI_MODEL", "grok-4"),
        input="Reply with exactly the word: pong",
    )

    assert result.text.strip().lower().startswith("pong")
    assert any(e.type == EventTypes.MODEL_TEXT_DELTA for e in result.events)
    assert result.provider_state is not None
    assert result.provider_state.previous_response_id is not None
