"""Fill multiple application forms from one natural-language description.

This validates the small structured-output workflow where an app extracts
typed fields for several downstream forms without building an agent loop.

Set ``OPENAI_API_KEY`` in your environment, install the optional extras
(``pip install -e .[openai,validate]``), then run::

    python examples/model_provider_form_fill.py
"""

from __future__ import annotations

# ruff: noqa: E402
import asyncio
import json
from typing import Literal

from _bootstrap import bootstrap, example_model
from pydantic import BaseModel, Field

bootstrap(load_env=True)

from agent_runtime import create_runtime_with_default_providers, structured_output


class ContactForm(BaseModel):
    """CRM contact form fields extracted from the user description."""

    full_name: str
    email: str
    company: str
    role: str


class TrialRequestForm(BaseModel):
    """Product trial request fields for sales operations."""

    product_area: Literal["analytics", "automation", "security", "unknown"]
    seats_requested: int
    urgency: Literal["low", "medium", "high"]
    requested_start_date: str


class SupportPreferencesForm(BaseModel):
    """Support handoff fields for onboarding and success teams."""

    preferred_channel: Literal["email", "phone", "slack", "unknown"]
    timezone: str
    notes: str = Field(description="Short operational note for the human team.")


class FormFillResult(BaseModel):
    """All forms the app can persist after one model call."""

    contact: ContactForm
    trial_request: TrialRequestForm
    support_preferences: SupportPreferencesForm
    missing_fields: list[str]


async def main() -> None:
    model = example_model()
    runtime = create_runtime_with_default_providers(include=["openai"])

    user_description = (
        "Hi, I am Priya Shah, VP Operations at Northwind Logistics. "
        "Use priya.shah@example.com for follow-up. We want to trial the analytics "
        "dashboard for 35 dispatch managers next Monday. Email is best because I "
        "am usually in IST, but add that we may need a Slack channel after kickoff."
    )

    try:
        result = await runtime.run(
            provider=f"openai:{model}",
            input=user_description,
            instructions=(
                "Extract fields for the app forms. Use 'unknown' for enum fields that "
                "cannot be inferred. Put human-readable gaps in missing_fields."
            ),
            output_spec=structured_output(FormFillResult, name="multi_form_fill"),
            max_output_tokens=900,
        )
    finally:
        await runtime.close()

    print(json.dumps(result.output.model_dump(), indent=2))
    print("\nmetadata:")
    print(json.dumps(result.summary(), indent=2, default=str))


if __name__ == "__main__":
    asyncio.run(main())
