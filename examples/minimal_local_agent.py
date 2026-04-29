"""Smallest local AgentProvider integration.

Uses LocalAgentProvider backed by EchoModelProvider, so it runs offline and
needs no API keys.

Run:

    python examples/minimal_local_agent.py
"""
from __future__ import annotations

# ruff: noqa: E402
import asyncio

from _bootstrap import bootstrap

bootstrap()

from agent_runtime import AgentRuntime, AgentSpec
from agent_runtime.providers.agent_adapters.local import LocalAgentProvider
from agent_runtime.providers.model_adapters.echo import EchoModelProvider


async def main() -> None:
    runtime = AgentRuntime()
    runtime.registry.register_model(EchoModelProvider())
    runtime.registry.register_agent(LocalAgentProvider(runtime.models))

    result = await runtime.agents.run(
        provider="local",
        agent=AgentSpec(
            name="minimal-local-agent",
            instructions="Reply with the task text.",
            model="echo/echo-mini",
        ),
        task="summarize this incident in one line",
        model="echo/echo-mini",
    )

    print(result.status)
    print(result.text)


if __name__ == "__main__":
    asyncio.run(main())
