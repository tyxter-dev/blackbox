"""Agent-to-agent handoff: triage transfers a customer to a specialist.

Mirrors the ``transfer_to_agent`` pattern from ``docs/USE_CASE_VALIDATION.md``
(103 production agents transfer between agents; auto-routing is enabled on
all 610). The boundary demonstrated here is deliberate:

- the *runtime* supplies the contract — a ``transfer_to_agent`` tool whose
  ``ToolResult`` carries canonical ``HANDOFF_REQUESTED`` events into the run
  stream and a structured directive in the deferred payload;
- the *application* owns routing policy — it reads the directive, composes
  the context for the target agent, and starts the second run.

Offline with scripted models for both agents.

Run::

    python examples/agent_handoff.py
"""
from __future__ import annotations

# ruff: noqa: E402
import asyncio
from collections.abc import AsyncIterator
from typing import Any

from _bootstrap import bootstrap

bootstrap()

from blackbox import AgentRuntime, EventTypes
from blackbox.core.capabilities import ModelCapabilities
from blackbox.core.events import AgentEvent
from blackbox.core.state import ProviderState
from blackbox.providers.base import TurnRequest
from blackbox.tools import ToolResult

SPECIALISTS = {"billing", "scheduling"}


def transfer_to_agent(target: str, reason: str, context: str) -> ToolResult:
    if target not in SPECIALISTS:
        return ToolResult(
            content=f"Unknown specialist {target!r}. Available: {sorted(SPECIALISTS)}",
            metadata={"error": "unknown_target"},
        )
    return ToolResult(
        content=f"Transfer to {target} acknowledged.",
        payload={"handoff": {"target": target, "reason": reason, "context": context}},
        events=[
            AgentEvent(
                type=EventTypes.HANDOFF_REQUESTED,
                data={"target": target, "reason": reason},
            )
        ],
    )


class ScriptedModel:
    provider_id = "scripted"

    def __init__(self) -> None:
        self._turns: list[Any] = []

    def queue(self, turn: Any) -> None:
        self._turns.append(turn)

    def capabilities(self, model: str | None = None) -> ModelCapabilities:
        return ModelCapabilities(supports_streaming_events=True, supports_function_tools=True)

    async def stream_turn(self, request: TurnRequest) -> AsyncIterator[AgentEvent]:
        turn = self._turns.pop(0)
        for event in turn(request):
            yield event


def _tool_call(call_id: str, name: str, arguments: dict[str, Any]):
    def turn(request: TurnRequest):
        yield AgentEvent(type=EventTypes.MODEL_REQUEST_STARTED, provider="scripted")
        yield AgentEvent(
            type=EventTypes.TOOL_CALL_REQUESTED,
            provider="scripted",
            item_id=call_id,
            data={"call_id": call_id, "name": name, "arguments": arguments},
        )
        yield AgentEvent(
            type=EventTypes.MODEL_COMPLETED,
            provider="scripted",
            data={"provider_state": ProviderState(provider="scripted")},
        )

    return turn


def _final_text(text: str):
    def turn(request: TurnRequest):
        yield AgentEvent(type=EventTypes.MODEL_REQUEST_STARTED, provider="scripted")
        yield AgentEvent(
            type=EventTypes.MODEL_TEXT_DELTA, provider="scripted", data={"delta": text}
        )
        yield AgentEvent(
            type=EventTypes.MODEL_COMPLETED,
            provider="scripted",
            data={"provider_state": ProviderState(provider="scripted")},
        )

    return turn


def _runtime_for(scripted: ScriptedModel) -> AgentRuntime:
    runtime = AgentRuntime()
    runtime.registry.register_model(scripted)
    runtime.tools.register(
        transfer_to_agent,
        name="transfer_to_agent",
        description="Transfer the conversation to a specialist agent.",
        category="routing",
        tags=["handoff"],
    )
    return runtime


async def main() -> None:
    customer_message = "My last invoice was charged twice, I want one charge reversed."

    # --- run 1: the triage agent decides this is a billing matter ----------
    triage_model = ScriptedModel()
    triage_model.queue(_tool_call("t1", "transfer_to_agent", {
        "target": "billing",
        "reason": "duplicate charge dispute",
        "context": "Customer reports invoice INV-2207 charged twice; wants one reversal.",
    }))
    triage_model.queue(_final_text(
        "I'm transferring you to our billing specialist - one moment."
    ))
    triage = _runtime_for(triage_model)

    print("[triage agent]")
    triage_result = await triage.run(
        provider="scripted:triage",
        input=customer_message,
        tools=["transfer_to_agent"],
    )
    for event in triage_result.events:
        if event.type == EventTypes.HANDOFF_REQUESTED:
            print(f"  handoff requested -> {event.data['target']} ({event.data['reason']})")
    print(f"  triage reply: {triage_result.output}")

    # --- application routing: read the directive, start the specialist ----
    directive = next(
        p.payload["handoff"] for p in triage_result.payloads
        if p.payload and "handoff" in p.payload
    )
    specialist_input = (
        f"[handoff from triage] {directive['context']}\n"
        f"Customer message: {customer_message}"
    )

    billing_model = ScriptedModel()
    billing_model.queue(_final_text(
        "I found the duplicate on INV-2207 and queued the reversal; you'll "
        "see the credit in 3-5 business days."
    ))
    billing = _runtime_for(billing_model)

    print(f"\n[application router] -> starting {directive['target']!r} specialist")
    billing_result = await billing.run(
        provider="scripted:billing",
        input=specialist_input,
    )
    print("\n[billing agent]")
    print(f"  specialist reply: {billing_result.output}")


if __name__ == "__main__":
    asyncio.run(main())
