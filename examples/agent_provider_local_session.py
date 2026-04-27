"""Run a local AgentProvider session and a follow-up message.

This example uses EchoModelProvider, so it does not need API keys.

Run:

    python examples/agent_provider_local_session.py
"""
from __future__ import annotations

import asyncio

from agent_runtime import AgentRuntime, AgentSession, AgentSpec, EventTypes
from agent_runtime.agents.local import LocalAgentProvider
from agent_runtime.models.echo import EchoModelProvider


async def _print_stream(runtime: AgentRuntime, session: AgentSession) -> None:
    async for event in runtime.agents.stream(session):
        if event.type == EventTypes.MODEL_TEXT_DELTA:
            print(event.data.get("delta", ""), end="")
        elif event.type in {
            EventTypes.SESSION_STARTED,
            EventTypes.SESSION_COMPLETED,
            EventTypes.SESSION_FAILED,
            EventTypes.SESSION_CANCELLED,
        }:
            print(f"\n{event.type}: {event.data}")


async def main() -> None:
    runtime = AgentRuntime()
    runtime.registry.register_model(EchoModelProvider())
    runtime.registry.register_agent(LocalAgentProvider(runtime.models))

    agent = await runtime.agents.create_agent(
        provider="local",
        spec=AgentSpec(
            name="support-helper",
            instructions="Answer support questions briefly.",
            model="echo/echo-mini",
        ),
    )
    session = await runtime.agents.create_session(
        provider="local",
        agent=agent,
        task="Summarize the incident: customer cannot reset password.",
        model="echo/echo-mini",
        metadata={"ticket_id": "TCK-101"},
    )

    print(f"session={session.id} provider={session.provider}")
    await _print_stream(runtime, session)

    invocation = await runtime.agents.send_message(
        session,
        "Add one next step for the support agent.",
    )
    print(f"\nfollow_up={invocation.id}")
    await _print_stream(runtime, session)


if __name__ == "__main__":
    asyncio.run(main())
