"""Run a conversational AgentProvider support agent with OpenAI file search.

The example creates a temporary OpenAI vector store from inline support docs,
configures a local AgentProvider-backed support agent, and prints the final
turn as multiple assistant messages.

Run:

    python examples/agent_provider_support_conversation.py
"""

from __future__ import annotations

# ruff: noqa: E402
import asyncio
import json
import os
import sys

from _bootstrap import bootstrap, example_model

bootstrap(load_env=True)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from agent_runtime import (
    AgentSpec,
    conversational_response,
    create_runtime_with_default_providers,
    temporary_openai_file_search,
)

DOCUMENTS = {
    "trial_onboarding.md": (
        "# Trial onboarding\n"
        "Analytics trials can include up to 40 dispatch-manager seats. Teams in IST "
        "should use email as the default support channel before kickoff. Slack "
        "channels are created after the kickoff call when the customer asks for "
        "shared operations support."
    ),
    "support_tone.md": (
        "# Support tone\n"
        "For drawer and messaging assistants, answer in short, warm messages. Start "
        "with the direct policy answer, then add the next step. Avoid long blocks of "
        "text unless the customer asks for detail."
    ),
    "billing_policy.md": (
        "# Billing policy\n"
        "Duplicate charges after an annual-plan upgrade should be credited within "
        "three business days. Escalate only when the duplicate charge is older than "
        "three business days or the customer reports a failed refund."
    ),
}


async def main() -> None:
    """Run a support-agent turn and print chat-bubble style messages."""
    if not os.getenv("OPENAI_API_KEY"):
        raise SystemExit("OPENAI_API_KEY is not set; examples/.env is loaded when present.")

    model = example_model()
    runtime = create_runtime_with_default_providers(
        include=["openai"],
        agent_providers=["local"],
    )

    try:
        async with temporary_openai_file_search(
            name="agent-runtime-support-conversation-example",
            documents=DOCUMENTS,
            max_num_results=4,
            include_results=True,
        ) as file_search:
            result = await runtime.agents.run(
                provider="local",
                agent=AgentSpec(
                    name="support-conversation-agent",
                    instructions=(
                        "You are a support agent for a logistics analytics product. "
                        "Use the uploaded support knowledge before answering. If the "
                        "policy says a request is not available yet, say that plainly "
                        "and offer the next best step."
                    ),
                    model=f"openai:{model}",
                    hosted_tools=[file_search],
                    response=conversational_response(
                        min_messages=2,
                        max_messages=3,
                        max_chars_per_message=220,
                    ),
                ),
                task=(
                    "Customer message: We're starting an analytics trial for 35 "
                    "dispatch managers in IST. Can you create our Slack channel before "
                    "the kickoff call? Please answer like a support drawer assistant."
                ),
            )
    finally:
        await runtime.close()

    print("messages:")
    for message in result.messages:
        print(f"{message.index + 1}. {message.content}")

    print("\ncombined text:")
    print(result.text)

    print("\nmetadata:")
    print(json.dumps(result.summary(), indent=2, default=str))


if __name__ == "__main__":
    asyncio.run(main())
