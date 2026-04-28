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
import os

from _bootstrap import bootstrap
from pydantic import BaseModel

bootstrap()

from agent_runtime import (
    AgentRuntime,
    FileSearch,
    OpenAIVectorStoreDocument,
    OutputSpec,
    temporary_openai_vector_store,
)
from agent_runtime.providers.model_adapters.openai_responses import OpenAIResponsesProvider


class SupportDrawerAnswer(BaseModel):
    """Structured answer a product support drawer can render directly."""

    answer: str
    cited_policy: str
    should_escalate: bool
    next_step: str


DOCUMENTS = [
    OpenAIVectorStoreDocument(
        filename="billing_policy.md",
        text=(
            "# Billing policy\n"
            "Duplicate charges after an annual-plan upgrade should be credited within "
            "three business days. Support should keep the invoice open until the credit "
            "appears and escalate only when the duplicate charge is older than three "
            "business days or the customer reports a failed refund."
        ),
    ),
    OpenAIVectorStoreDocument(
        filename="trial_onboarding.md",
        text=(
            "# Trial onboarding\n"
            "Analytics trials can include up to 40 dispatch-manager seats. For teams in "
            "IST, email is the default support channel before kickoff. Slack channels are "
            "created after the kickoff call when the customer requests shared operations "
            "support."
        ),
    ),
]


async def main() -> None:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise SystemExit("OPENAI_API_KEY is not set")

    try:
        from openai import AsyncOpenAI
    except ImportError as exc:
        raise SystemExit("Install OpenAI support with `pip install -e .[openai]`.") from exc

    model = os.getenv("OPENAI_EXAMPLE_MODEL", "gpt-4o-mini")
    client = AsyncOpenAI(api_key=api_key)
    runtime = AgentRuntime()
    runtime.registry.register_model(OpenAIResponsesProvider(client=client))

    try:
        async with temporary_openai_vector_store(
            client=client,
            name="agent-runtime-knowledge-drawer-example",
            documents=DOCUMENTS,
        ) as vector_store:
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
                hosted_tools=[
                    FileSearch(
                        vector_store_ids=[vector_store.id],
                        max_num_results=4,
                        include_results=True,
                    )
                ],
                output_spec=OutputSpec(
                    schema=SupportDrawerAnswer,
                    strategy="provider_native",
                    fallback="posthoc_parse",
                    name="support_drawer_answer",
                ),
                max_output_tokens=1000,
            )
    finally:
        await runtime.close()
        await client.close()

    print(json.dumps(result.output.model_dump(), indent=2))
    print("\nmetadata:")
    print(json.dumps(_summary(result), indent=2, default=str))


def _summary(result: object) -> dict[str, object]:
    metadata = getattr(result, "metadata", {})
    provider_state = getattr(result, "provider_state", None)
    tool_state = getattr(provider_state, "tool_state", {}) if provider_state is not None else {}
    return {
        "validation_attempts": metadata.get("validation_attempts"),
        "hosted_tools": metadata.get("hosted_tools"),
        "source_references": tool_state.get("source_references", []),
        "usage": metadata.get("usage"),
    }


if __name__ == "__main__":
    asyncio.run(main())
