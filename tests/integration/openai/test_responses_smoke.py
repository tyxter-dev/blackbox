"""Network-gated smoke test for the OpenAI Responses provider.

Run with::

    OPENAI_API_KEY=... pytest -m integration_openai

Skipped automatically when the key is absent so the default suite stays offline.
"""
from __future__ import annotations

import os

import pytest

from agent_runtime import AgentRuntime, EventTypes
from agent_runtime.models.openai_responses import OpenAIResponsesProvider

pytestmark = pytest.mark.integration_openai


@pytest.mark.skipif(
    not os.environ.get("OPENAI_API_KEY"),
    reason="OPENAI_API_KEY not set",
)
async def test_real_text_only_turn() -> None:
    runtime = AgentRuntime()
    runtime.registry.register_model(
        OpenAIResponsesProvider(api_key=os.environ["OPENAI_API_KEY"])
    )

    result = await runtime.models.run(
        provider="openai",
        model="gpt-4o-mini",
        input="Reply with exactly the word: pong",
    )

    assert result.text.strip().lower().startswith("pong")
    assert any(e.type == EventTypes.MODEL_TEXT_DELTA for e in result.events)
    assert result.provider_state is not None
    assert result.provider_state.previous_response_id is not None
