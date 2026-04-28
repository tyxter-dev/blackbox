"""Classify a short user request with provider-native structured output.

This validates the "choose an option" workflow common in routing, moderation,
intent detection, and single-step product assistants.

Set ``OPENAI_API_KEY`` in your environment, install the optional extras
(``pip install -e .[openai,validate]``), then run::

    python examples/model_provider_classification.py
"""

from __future__ import annotations

# ruff: noqa: E402
import asyncio
import json
import os
from typing import Literal

from _bootstrap import bootstrap
from pydantic import BaseModel, Field

bootstrap()

from agent_runtime import AgentRuntime, OutputSpec
from agent_runtime.providers.model_adapters.openai_responses import OpenAIResponsesProvider


class RequestClassification(BaseModel):
    """Decision object for routing one user request to an application queue."""

    intent: Literal["billing", "technical_support", "sales", "account_admin", "other"]
    queue: Literal["finance", "support", "revenue", "identity", "triage"]
    confidence: float = Field(ge=0, le=1)
    rationale: str


async def main() -> None:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise SystemExit("OPENAI_API_KEY is not set")

    model = os.getenv("OPENAI_EXAMPLE_MODEL", "gpt-4o-mini")
    runtime = AgentRuntime()
    runtime.registry.register_model(OpenAIResponsesProvider(api_key=api_key))

    try:
        result = await runtime.run(
            provider=f"openai:{model}",
            input=(
                "I was charged twice after upgrading to the annual plan. "
                "Please fix the invoice before the card closes tomorrow."
            ),
            instructions=(
                "Classify the user request for an application router. Pick exactly one "
                "intent and one queue. Keep the rationale short."
            ),
            output_spec=OutputSpec(
                schema=RequestClassification,
                strategy="provider_native",
                fallback="posthoc_parse",
                name="request_classification",
            ),
            max_output_tokens=500,
        )
    finally:
        await runtime.close()

    print(json.dumps(result.output.model_dump(), indent=2))
    print("\nmetadata:")
    print(json.dumps(_summary(result.metadata), indent=2, default=str))


def _summary(metadata: dict[str, object]) -> dict[str, object]:
    return {
        "validation_attempts": metadata.get("validation_attempts"),
        "provider_native_fallback": metadata.get("provider_native_fallback"),
        "usage": metadata.get("usage"),
    }


if __name__ == "__main__":
    asyncio.run(main())
