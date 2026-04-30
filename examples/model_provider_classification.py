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
from typing import Literal

from _bootstrap import bootstrap, example_model
from pydantic import BaseModel, Field

bootstrap(load_env=True)

from blackbox import create_runtime_with_default_providers, structured_output


class RequestClassification(BaseModel):
    """Decision object for routing one user request to an application queue."""

    intent: Literal["billing", "technical_support", "sales", "account_admin", "other"]
    queue: Literal["finance", "support", "revenue", "identity", "triage"]
    confidence: float = Field(ge=0, le=1)
    rationale: str


async def main() -> None:
    model = example_model()
    runtime = create_runtime_with_default_providers(include=["openai"])

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
            output_spec=structured_output(
                RequestClassification,
                name="request_classification",
            ),
            max_output_tokens=500,
        )
    finally:
        await runtime.close()

    print(json.dumps(result.output.model_dump(), indent=2))
    print("\nmetadata:")
    print(json.dumps(result.summary(), indent=2, default=str))


if __name__ == "__main__":
    asyncio.run(main())
