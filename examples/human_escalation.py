"""Human escalation: a risky tool call pauses the run until a reviewer decides.

Mirrors the ``call_human`` pattern from ``docs/USE_CASE_VALIDATION.md``
(588/610 production agents can escalate to a human): a policy marks
``issue_refund`` as requiring approval, the loop emits
``APPROVAL_REQUESTED`` and blocks, and an out-of-band reviewer resolves the
decision through ``runtime.approve(...)``. The run then resumes (approved)
or feeds the denial back to the model (denied).

Two scenarios run back to back, fully offline with a scripted model:

1. A R$180 refund — the reviewer approves, the tool executes.
2. A R$5,000 refund — the reviewer denies, the model apologizes instead.

Run::

    python examples/human_escalation.py
"""
from __future__ import annotations

# ruff: noqa: E402
import asyncio
from collections.abc import AsyncIterator
from typing import Any

from _bootstrap import bootstrap

bootstrap()

from blackbox import AgentRuntime, ApprovalDecision, EventTypes
from blackbox.core.capabilities import ModelCapabilities
from blackbox.core.errors import ApprovalError
from blackbox.core.events import AgentEvent
from blackbox.core.policy import PolicyDecision, PolicyRequest
from blackbox.core.state import ProviderState
from blackbox.providers.base import TurnRequest
from blackbox.tools import ToolResult

REVIEWER_LIMIT = 1000.0


class RefundApprovalPolicy:
    """Require human approval before any refund is issued."""

    async def check(self, request: PolicyRequest) -> PolicyDecision:
        if request.checkpoint == "before_tool_call" and request.action == "issue_refund":
            return PolicyDecision.require_approval("Refunds require human review.")
        return PolicyDecision.allow()


class ScriptedModel:
    """Deterministic model: replays queued turn generators."""

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


def _refund_call(amount: float):
    def turn(request: TurnRequest):
        yield AgentEvent(type=EventTypes.MODEL_REQUEST_STARTED, provider="scripted")
        yield AgentEvent(
            type=EventTypes.TOOL_CALL_REQUESTED,
            provider="scripted",
            item_id="refund_1",
            data={
                "call_id": "refund_1",
                "name": "issue_refund",
                "arguments": {"order_id": "ord_881", "amount": amount},
            },
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


def issue_refund(order_id: str, amount: float) -> ToolResult:
    refund = {"refund_id": "rf_3301", "order_id": order_id, "amount": amount, "status": "issued"}
    return ToolResult(
        content=f"Refund of {amount:.2f} issued for {order_id}.",
        payload=refund,
    )


async def _reviewer(runtime: AgentRuntime, approval_id: str, amount: float) -> None:
    """Simulated human reviewer resolving the pending approval out-of-band."""

    if amount <= REVIEWER_LIMIT:
        decision = ApprovalDecision.approve(f"within reviewer limit ({REVIEWER_LIMIT:.0f})")
    else:
        decision = ApprovalDecision.deny(f"exceeds reviewer limit ({REVIEWER_LIMIT:.0f})")
    # The approval future is registered just after APPROVAL_REQUESTED is
    # consumed, so a real reviewer arriving "later" always wins; retry covers
    # the tiny startup window.
    for _ in range(100):
        try:
            await runtime.approve(approval_id, decision)
            return
        except ApprovalError:
            await asyncio.sleep(0.01)
    raise RuntimeError(f"approval {approval_id} never became pending")


async def run_scenario(title: str, amount: float, final_text: str) -> None:
    print(f"--- {title} ---")
    scripted = ScriptedModel()
    scripted.queue(_refund_call(amount))
    scripted.queue(_final_text(final_text))

    runtime = AgentRuntime()
    runtime.registry.register_model(scripted)
    runtime.tools.register(issue_refund, name="issue_refund", description="Issue a refund.")

    reviewer_tasks: list[asyncio.Task[None]] = []
    events: list[AgentEvent] = []
    async for event in runtime.stream(
        provider="scripted:support",
        input=f"Customer asks to refund order ord_881 ({amount:.2f}).",
        tools=["issue_refund"],
        policy=RefundApprovalPolicy(),
    ):
        events.append(event)
        if event.type == EventTypes.APPROVAL_REQUESTED:
            print(f"  paused: approval needed for {event.data['action']} "
                  f"{event.data['arguments']}")
            reviewer_tasks.append(asyncio.create_task(
                _reviewer(runtime, event.data["approval_id"], amount)
            ))
        elif event.type == EventTypes.APPROVAL_APPROVED:
            print(f"  reviewer approved: {event.data.get('reason')}")
        elif event.type == EventTypes.APPROVAL_DENIED:
            print(f"  reviewer denied: {event.data.get('reason')}")
        elif event.type == EventTypes.TOOL_CALL_COMPLETED:
            print(f"  executed: {event.data.get('name')}")
        elif event.type == EventTypes.MODEL_TEXT_DELTA:
            print(f"  agent: {event.data['delta']}")
    for task in reviewer_tasks:
        await task
    print()


async def main() -> None:
    await run_scenario(
        "scenario 1: refund within reviewer limit",
        amount=180.0,
        final_text="Done! R$180.00 was refunded to your original payment method.",
    )
    await run_scenario(
        "scenario 2: refund above reviewer limit",
        amount=5000.0,
        final_text=(
            "I can't issue this refund automatically; a specialist will "
            "contact you within one business day."
        ),
    )


if __name__ == "__main__":
    asyncio.run(main())
