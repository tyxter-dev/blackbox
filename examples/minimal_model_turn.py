"""Smallest direct model-turn integration.

Uses EchoModelProvider, so it runs offline and needs no API keys.

Run:

    python examples/minimal_model_turn.py
"""
from __future__ import annotations

# ruff: noqa: E402
import asyncio

from _bootstrap import bootstrap

bootstrap()

from agent_runtime import AgentRuntime
from agent_runtime.providers.model_adapters.echo import EchoModelProvider


async def main() -> None:
    runtime = AgentRuntime()
    runtime.registry.register_model(EchoModelProvider())

    result = await runtime.models.run(
        provider="echo/echo-mini",
        input="hello from a direct model turn",
    )

    print(result.text)


if __name__ == "__main__":
    asyncio.run(main())
