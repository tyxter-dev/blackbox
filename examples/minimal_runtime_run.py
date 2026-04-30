"""Smallest high-level ``AgentRuntime.run`` integration.

Uses EchoModelProvider and a dataclass output type, so it runs offline and
needs no API keys or Pydantic dependency.

Run:

    python examples/minimal_runtime_run.py
"""
from __future__ import annotations

# ruff: noqa: E402
import asyncio
from dataclasses import dataclass

from _bootstrap import bootstrap

bootstrap()

from blackbox import AgentRuntime
from blackbox.providers.model_adapters.echo import EchoModelProvider


@dataclass
class Decision:
    label: str
    confidence: float


async def main() -> None:
    runtime = AgentRuntime()
    runtime.registry.register_model(EchoModelProvider())

    result = await runtime.run(
        provider="echo/echo-mini",
        input='{"label": "ready", "confidence": 0.98}',
        output_type=Decision,
    )

    decision: Decision = result.output
    print(decision.label, decision.confidence)


if __name__ == "__main__":
    asyncio.run(main())
