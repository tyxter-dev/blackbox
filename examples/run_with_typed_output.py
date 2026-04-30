"""High-level blackbox loop with structured Pydantic output.

Demonstrates the v1 product promise from PRD §6 UC0:

    input + tools + output_type -> AgentResult[T]

Uses a small inline scripted model so the example runs offline.

Run::

    python examples/run_with_typed_output.py
"""
from __future__ import annotations

# ruff: noqa: E402
import asyncio
import json
from collections.abc import AsyncIterator, Iterable

from _bootstrap import bootstrap
from pydantic import BaseModel

bootstrap()

from blackbox import AgentRuntime
from blackbox.core.capabilities import ModelCapabilities
from blackbox.core.events import AgentEvent, EventTypes
from blackbox.core.state import ProviderState
from blackbox.providers.base import TurnRequest
from blackbox.tools import ToolResult


class _ScriptedModel:
    provider_id = "scripted"

    def __init__(self) -> None:
        self._turn = 0

    def capabilities(self, model: str | None = None) -> ModelCapabilities:
        return ModelCapabilities(supports_streaming_events=True, supports_function_tools=True)

    async def stream_turn(self, request: TurnRequest) -> AsyncIterator[AgentEvent]:
        self._turn += 1
        if self._turn == 1:
            for ev in _tool_request_events():
                yield ev
        else:
            for ev in _final_text_events():
                yield ev


def _tool_request_events() -> Iterable[AgentEvent]:
    yield AgentEvent(type=EventTypes.MODEL_REQUEST_STARTED, provider="scripted")
    yield AgentEvent(
        type=EventTypes.TOOL_CALL_REQUESTED,
        provider="scripted",
        item_id="c1",
        data={"call_id": "c1", "name": "lookup_customer", "arguments": {"customer_id": "cust_1"}},
    )
    yield AgentEvent(
        type=EventTypes.MODEL_COMPLETED,
        provider="scripted",
        data={"provider_state": ProviderState(provider="scripted")},
    )


def _final_text_events() -> Iterable[AgentEvent]:
    body = json.dumps({
        "should_escalate": True,
        "priority": "P1",
        "summary": "Platinum customer with 3 open incidents — escalate.",
    })
    yield AgentEvent(type=EventTypes.MODEL_REQUEST_STARTED, provider="scripted")
    yield AgentEvent(
        type=EventTypes.MODEL_TEXT_DELTA, provider="scripted", data={"delta": body}
    )
    yield AgentEvent(
        type=EventTypes.MODEL_COMPLETED,
        provider="scripted",
        data={"provider_state": ProviderState(provider="scripted")},
    )


class TicketDecision(BaseModel):
    should_escalate: bool
    priority: str
    summary: str


async def main() -> None:
    runtime = AgentRuntime()
    runtime.registry.register_model(_ScriptedModel())

    runtime.tools.register(
        lambda customer_id: ToolResult(
            content=json.dumps({"tier": "platinum", "open_incidents": 3}),
            payload={"customer_id": customer_id, "tier": "platinum"},
        ),
        name="lookup_customer",
        description="Look up a customer profile.",
        parameters={
            "type": "object",
            "properties": {"customer_id": {"type": "string"}},
            "required": ["customer_id"],
        },
    )

    result = await runtime.run(
        provider="scripted/test",
        input="Review customer cust_1 and decide if we should escalate.",
        tools=["lookup_customer"],
        output_type=TicketDecision,
    )

    decision: TicketDecision = result.output
    print(f"escalate? {decision.should_escalate}")
    print(f"priority: {decision.priority}")
    print(f"summary:  {decision.summary}")
    print(f"payloads collected: {len(result.payloads)} -> {result.payloads[0].payload}")


if __name__ == "__main__":
    asyncio.run(main())
