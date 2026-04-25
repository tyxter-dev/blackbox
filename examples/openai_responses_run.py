"""Minimal OpenAI Responses-native model turn.

Set ``OPENAI_API_KEY`` in your environment, install the optional extra
(``pip install -e .[openai]``) and run::

    python examples/openai_responses_run.py
"""
from __future__ import annotations

import asyncio
import os

from agent_runtime import AgentRuntime, EventTypes
from agent_runtime.models.openai_responses import OpenAIResponsesProvider


async def main() -> None:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise SystemExit("OPENAI_API_KEY is not set")

    runtime = AgentRuntime()
    runtime.registry.register_model(OpenAIResponsesProvider(api_key=api_key))

    print("Streaming...")
    async for event in runtime.models.stream(
        provider="openai",
        model="gpt-4o-mini",
        input="In one sentence, what is an Agent Runtime?",
    ):
        if event.type == EventTypes.MODEL_TEXT_DELTA:
            print(event.data["delta"], end="", flush=True)
    print()


if __name__ == "__main__":
    asyncio.run(main())
