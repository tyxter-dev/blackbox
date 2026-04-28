"""Answer a drawer-assistant question with OpenAI file search.

This validates the retrieval workflow for support drawers, help panels, and
single-response assistants. The example creates a temporary OpenAI vector store
from inline markdown documents, asks the ModelProvider layer to use
``FileSearch``, then deletes the demo vector store and uploaded files.

Set ``OPENAI_API_KEY`` in your environment, install the optional extras
(``pip install -e .[openai,validate]``), then run::

    python examples/model_provider_knowledge_drawer.py
"""

from __future__ import annotations

# ruff: noqa: E402
import asyncio
import json

from _bootstrap import bootstrap, example_model
from pydantic import BaseModel

bootstrap(load_env=True)

from agent_runtime import (
    create_runtime_with_default_providers,
    structured_output,
    temporary_openai_file_search,
)


class SupportDrawerAnswer(BaseModel):
    """Structured answer a product support drawer can render directly."""

    answer: str
    cited_policy: str
    should_escalate: bool
    next_step: str


DOCUMENTS = {
    "billing_policy.md": (
        "# Billing policy\n"
        "Duplicate charges after an annual-plan upgrade should be credited within "
        "three business days. Support should keep the invoice open until the credit "
        "appears and escalate only when the duplicate charge is older than three "
        "business days or the customer reports a failed refund."
    ),
    "trial_onboarding.md": (
        "# Trial onboarding\n"
        "Analytics trials can include up to 40 dispatch-manager seats. For teams in "
        "IST, email is the default support channel before kickoff. Slack channels are "
        "created after the kickoff call when the customer requests shared operations "
        "support."
    ),
}


async def main() -> None:
    model = example_model()
    runtime = create_runtime_with_default_providers(include=["openai"])

    try:
        async with temporary_openai_file_search(
            name="agent-runtime-knowledge-drawer-example",
            documents=DOCUMENTS,
            max_num_results=4,
            include_results=True,
        ) as file_search:
            result = await runtime.run(
                provider=f"openai:{model}",
                input=(
                    "A customer in IST wants a 35-seat analytics trial starting next Monday. "
                    "What should the drawer assistant say about seat limits and support setup?"
                ),
                instructions=(
                    "Use file search before answering. Ground the answer in the uploaded "
                    "knowledge base and keep it ready for a support drawer UI."
                ),
                hosted_tools=[file_search],
                output_spec=structured_output(SupportDrawerAnswer, name="support_drawer_answer"),
                max_output_tokens=1000,
            )
    finally:
        await runtime.close()

    print(json.dumps(result.output.model_dump(), indent=2))
    print("\nmetadata:")
    print(json.dumps(result.summary(), indent=2, default=str))


if __name__ == "__main__":
    asyncio.run(main())
