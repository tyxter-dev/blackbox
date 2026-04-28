"""Network-gated smoke test for the Gemini GenerateContent provider.

Run with::

    GOOGLE_API_KEY=... pytest -m integration_gemini

Skipped automatically when the key is absent so the default suite stays offline.
"""
from __future__ import annotations

import os

import pytest

from agent_runtime import AgentRuntime, EventTypes
from agent_runtime.providers.model_adapters.gemini_generate_content import (
    GeminiGenerateContentProvider,
)

pytestmark = pytest.mark.integration_gemini


@pytest.mark.skipif(
    not os.environ.get("GOOGLE_API_KEY"),
    reason="GOOGLE_API_KEY not set",
)
async def test_real_text_only_turn() -> None:
    runtime = AgentRuntime()
    runtime.registry.register_model(
        GeminiGenerateContentProvider(api_key=os.environ["GOOGLE_API_KEY"])
    )

    result = await runtime.models.run(
        provider="google",
        model=os.environ.get("GEMINI_MODEL", "gemini-2.5-flash"),
        input="Reply with exactly the word: pong",
    )

    assert "pong" in result.text.strip().lower()
    assert any(e.type == EventTypes.MODEL_TEXT_DELTA for e in result.events)
    assert result.provider_state is not None
    assert isinstance(result.provider_state.native_history, list)
    assert result.provider_state.native_history[-1]["role"] == "model"
