"""Minimal model-turn example using the dependency-free echo provider.

Run::

    python examples/echo_run.py
"""
from __future__ import annotations

import asyncio

from agent_runtime import AgentRuntime
from agent_runtime.models.echo import EchoModelProvider


async def main() -> None:
    runtime = AgentRuntime()
    runtime.registry.register_model(EchoModelProvider())

    result = await runtime.models.run(provider="echo/echo-mini", input="hello world")

    print("text:", result.text)
    print("events:", len(result.events))
    print("provider_state.turn:", result.provider_state.continuation["turn"])


if __name__ == "__main__":
    asyncio.run(main())
