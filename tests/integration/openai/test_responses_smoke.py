"""Network-gated smoke test for the OpenAI Responses provider.

Run with::

    OPENAI_API_KEY=... pytest -m integration_openai

Skipped automatically when the key is absent so the default suite stays offline.
"""
from __future__ import annotations

import os

import pytest
from pydantic import BaseModel

from agent_runtime import AgentRuntime, EventTypes
from agent_runtime.core.results import AgentResult, OutputSpec
from agent_runtime.providers.model_adapters.openai_responses import OpenAIResponsesProvider

pytestmark = pytest.mark.integration_openai


class SmokeDecision(BaseModel):
    word: str
    ok: bool


def _model() -> str:
    return os.environ.get("OPENAI_TEST_MODEL", "gpt-4o-mini")


def _runtime() -> AgentRuntime:
    runtime = AgentRuntime()
    runtime.registry.register_model(
        OpenAIResponsesProvider(api_key=os.environ["OPENAI_API_KEY"])
    )
    return runtime


@pytest.mark.skipif(
    not os.environ.get("OPENAI_API_KEY"),
    reason="OPENAI_API_KEY not set",
)
async def test_real_text_only_turn() -> None:
    runtime = _runtime()

    result = await runtime.models.run(
        provider="openai",
        model=_model(),
        input="Reply with exactly the word: pong",
    )

    assert result.text.strip().lower().startswith("pong")
    assert any(e.type == EventTypes.MODEL_TEXT_DELTA for e in result.events)
    assert result.provider_state is not None
    assert result.provider_state.previous_response_id is not None


@pytest.mark.skipif(
    not os.environ.get("OPENAI_API_KEY"),
    reason="OPENAI_API_KEY not set",
)
async def test_real_provider_state_continuation() -> None:
    runtime = _runtime()

    first = await runtime.models.run(
        provider="openai",
        model=_model(),
        input="Remember the code word 'orchid'. Reply with exactly: stored",
        max_output_tokens=256,
    )
    assert first.provider_state is not None

    second = await runtime.models.run(
        provider="openai",
        model=_model(),
        input="What code word did I ask you to remember? Reply with only the word.",
        provider_state=first.provider_state,
        max_output_tokens=256,
    )

    assert "orchid" in second.text.strip().lower()


@pytest.mark.skipif(
    not os.environ.get("OPENAI_API_KEY"),
    reason="OPENAI_API_KEY not set",
)
async def test_real_provider_native_structured_output_through_runtime_run() -> None:
    runtime = _runtime()

    result: AgentResult[SmokeDecision] = await runtime.run(
        provider=f"openai:{_model()}",
        input="Return the word pong and ok=true.",
        output_spec=OutputSpec(
            schema=SmokeDecision,
            strategy="provider_native",
            name="smoke_decision",
        ),
        max_output_tokens=512,
    )

    assert result.output.word.lower() == "pong"
    assert result.output.ok is True
    assert result.provider_state is not None


@pytest.mark.skipif(
    not os.environ.get("OPENAI_API_KEY"),
    reason="OPENAI_API_KEY not set",
)
async def test_real_runtime_run_dispatches_local_tool() -> None:
    runtime = _runtime()
    runtime.tools.register(
        lambda: "pong",
        name="get_secret_word",
        description="Return the secret word pong. Takes no arguments.",
        parameters={
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    )

    result: AgentResult[str] = await runtime.run(
        provider=f"openai:{_model()}",
        input=(
            "You must call the get_secret_word tool exactly once. Then reply "
            "with exactly the returned word and no other text."
        ),
        tools=["get_secret_word"],
        max_output_tokens=256,
    )

    assert result.text.strip().lower() == "pong"
    assert any(event.type == EventTypes.TOOL_CALL_REQUESTED for event in result.events)
    assert any(event.type == EventTypes.TOOL_CALL_COMPLETED for event in result.events)
