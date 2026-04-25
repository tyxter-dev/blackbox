"""Reusable autonomous tool dispatch loop.

The :class:`AgentLoop` is the execution layer between provider-native model
turns and tool execution. It owns the iterative control flow described in
PRD §8.4 and §14.5.

Both :class:`agent_runtime.agents.local.LocalAgentProvider` (session-shaped
API) and :meth:`agent_runtime.runtime.AgentRuntime.run` (blackbox high-level
API) delegate their loop body to this class.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any

from agent_runtime.core.approvals import ApprovalDecision
from agent_runtime.core.errors import ApprovalError, SessionError
from agent_runtime.core.events import AgentEvent, EventTypes
from agent_runtime.core.items import ItemTypes, RunItem
from agent_runtime.core.policy import (
    ApprovalCallbackPolicy,
    Policy,
    PolicyDecision,
    PolicyRequest,
)
from agent_runtime.core.state import ProviderState
from agent_runtime.tools.runtime import ToolRuntime

ApprovalPolicy = Callable[[str, dict[str, Any]], bool]


@dataclass(slots=True)
class _PendingToolCall:
    call_id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class AgentLoop:
    """Autonomous local tool dispatch loop.

    Drives a model provider through repeated turns: streams events, dispatches
    ``tool.call.requested`` events through ``ToolRuntime``, optionally pauses
    for approvals, and feeds tool results back via provider-native continuation
    (``ProviderState``) until the model produces a final answer with no tool
    calls or the loop hits a terminal condition.
    """

    stream_factory: Callable[..., AsyncIterator[AgentEvent]]
    """Callable that takes ``input`` and ``provider_state`` and returns an
    async iterator of model events. Typically ``ModelRuntime.stream`` bound
    with the chosen provider/model."""

    tools: ToolRuntime | None = None
    approval_policy: ApprovalPolicy | None = None
    policy: Policy | None = None
    max_iterations: int = 8

    _approvals: dict[str, asyncio.Future[ApprovalDecision]] = field(default_factory=dict)
    _cancelled: bool = False

    async def run(
        self,
        *,
        input: str | list[Any],
        provider_state: ProviderState | None = None,
        session_id: str | None = None,
        provider_id: str = "local",
        mock_tools: bool = False,
    ) -> AsyncIterator[AgentEvent]:
        """Drive the loop, yielding canonical events for every turn."""
        turn_input: str | list[Any] = input

        for iteration in range(self.max_iterations):
            if self._cancelled:
                yield AgentEvent(
                    type=EventTypes.SESSION_CANCELLED,
                    provider=provider_id,
                    session_id=session_id,
                    data={"iteration": iteration},
                )
                return

            pending_calls: list[_PendingToolCall] = []
            last_state: ProviderState | None = None

            async for event in self.stream_factory(
                input=turn_input, provider_state=provider_state
            ):
                yield AgentEvent(
                    type=event.type,
                    provider=event.provider,
                    session_id=session_id,
                    item_id=event.item_id,
                    data=event.data,
                    raw=event.raw,
                )
                if event.type == EventTypes.TOOL_CALL_REQUESTED:
                    pending_calls.append(
                        _PendingToolCall(
                            call_id=event.data.get("call_id") or event.item_id or "",
                            name=event.data["name"],
                            arguments=dict(event.data.get("arguments") or {}),
                        )
                    )
                maybe_state = event.data.get("provider_state")
                if isinstance(maybe_state, ProviderState):
                    last_state = maybe_state

            provider_state = last_state

            if not pending_calls:
                return

            if self.tools is None:
                raise SessionError(
                    "Model requested a tool call but the loop has no ToolRuntime."
                )

            effective_policy = self._effective_policy()
            tool_runtime = self.tools
            tool_results: list[RunItem | None] = [None] * len(pending_calls)
            allowed_calls: list[tuple[int, _PendingToolCall]] = []
            for index, call in enumerate(pending_calls):
                if effective_policy is not None:
                    decision = await effective_policy.check(
                        PolicyRequest(
                            checkpoint="before_tool_call",
                            action=call.name,
                            arguments=dict(call.arguments),
                        )
                    )
                else:
                    decision = PolicyDecision.allow()

                if decision.verdict == "deny":
                    tool_results[index] = RunItem(
                        type=ItemTypes.FUNCTION_RESULT,
                        provider=provider_id,
                        status="failed",
                        data={
                            "call_id": call.call_id,
                            "name": call.name,
                            "error": "denied_by_policy",
                            "reason": decision.reason,
                        },
                    )
                    continue

                if decision.verdict == "require_approval":
                    async for ev in self._await_approval(
                        call, session_id=session_id, provider_id=provider_id
                    ):
                        yield ev
                    if not self._last_decision.approved:
                        tool_results[index] = RunItem(
                            type=ItemTypes.FUNCTION_RESULT,
                            provider=provider_id,
                            status="failed",
                            data={
                                "call_id": call.call_id,
                                "name": call.name,
                                "error": "denied_by_approval",
                                "reason": self._last_decision.reason,
                            },
                        )
                        continue

                allowed_calls.append((index, call))

            for _, call in allowed_calls:
                yield AgentEvent(
                    type=EventTypes.TOOL_CALL_STARTED,
                    provider=provider_id,
                    session_id=session_id,
                    item_id=call.call_id,
                    data={"call_id": call.call_id, "name": call.name},
                )

            task_results = await tool_runtime.call_many(
                [(call.name, call.arguments) for _, call in allowed_calls],
                mock=mock_tools,
            )

            for (index, call), result in zip(allowed_calls, task_results, strict=True):
                if isinstance(result, BaseException):
                    yield AgentEvent(
                        type=EventTypes.TOOL_CALL_FAILED,
                        provider=provider_id,
                        session_id=session_id,
                        item_id=call.call_id,
                        data={"call_id": call.call_id, "name": call.name, "error": str(result)},
                    )
                    tool_results[index] = RunItem(
                        type=ItemTypes.FUNCTION_RESULT,
                        provider=provider_id,
                        status="failed",
                        data={"call_id": call.call_id, "name": call.name, "error": str(result)},
                    )
                    continue

                completed_data: dict[str, Any] = {
                    "call_id": call.call_id,
                    "name": call.name,
                    "content": result.content,
                }
                if result.payload is not None:
                    completed_data["payload"] = result.payload
                if result.metadata:
                    completed_data["metadata"] = dict(result.metadata)

                yield AgentEvent(
                    type=EventTypes.TOOL_CALL_COMPLETED,
                    provider=provider_id,
                    session_id=session_id,
                    item_id=call.call_id,
                    data=completed_data,
                )
                tool_results.append(
                    RunItem(
                        type=ItemTypes.FUNCTION_RESULT,
                        provider=provider_id,
                        status="completed",
                        data={
                            "call_id": call.call_id,
                            "name": call.name,
                            "content": result.content,
                        },
                    )
                )

            turn_input = [item for item in tool_results if item is not None]
        else:
            yield AgentEvent(
                type=EventTypes.SESSION_FAILED,
                provider=provider_id,
                session_id=session_id,
                data={"reason": "max_iterations_reached", "limit": self.max_iterations},
            )

    async def _await_approval(
        self,
        call: _PendingToolCall,
        *,
        session_id: str | None,
        provider_id: str,
    ) -> AsyncIterator[AgentEvent]:
        approval_id = f"approval_{call.call_id}"
        yield AgentEvent(
            type=EventTypes.APPROVAL_REQUESTED,
            provider=provider_id,
            session_id=session_id,
            item_id=approval_id,
            data={
                "approval_id": approval_id,
                "action": call.name,
                "arguments": call.arguments,
            },
        )
        # Register the future after the yield so the consumer can observe
        # APPROVAL_REQUESTED and update session status before the test/app
        # sees the approval id appear in the pending dict.
        loop = asyncio.get_running_loop()
        future: asyncio.Future[ApprovalDecision] = loop.create_future()
        self._approvals[approval_id] = future
        decision = await future
        self._last_decision = decision
        yield AgentEvent(
            type=(
                EventTypes.APPROVAL_APPROVED
                if decision.approved
                else EventTypes.APPROVAL_DENIED
            ),
            provider=provider_id,
            session_id=session_id,
            item_id=approval_id,
            data={"approval_id": approval_id, "reason": decision.reason},
        )

    def _effective_policy(self) -> Policy | None:
        """Resolve which policy to consult for the current call.

        Priority: an explicit ``self.policy`` if provided; otherwise wrap the
        legacy ``approval_policy`` callable in ``ApprovalCallbackPolicy``;
        otherwise no gate.
        """
        if self.policy is not None:
            return self.policy
        if self.approval_policy is not None:
            return ApprovalCallbackPolicy(self.approval_policy)
        return None

    async def approve(self, approval_id: str, decision: ApprovalDecision) -> None:
        future = self._approvals.pop(approval_id, None)
        if future is None or future.done():
            raise ApprovalError(f"No pending approval with id '{approval_id}'.")
        future.set_result(decision)

    def cancel(self) -> None:
        self._cancelled = True
