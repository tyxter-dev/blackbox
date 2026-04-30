"""Reusable autonomous tool dispatch loop.

The :class:`AgentLoop` is the execution layer between provider-native model
turns and tool execution. It owns the iterative control flow described in
PRD §8.4 and §14.5.

Both :class:`blackbox.providers.agent_adapters.local.LocalAgentProvider`
(session-shaped API) and :meth:`blackbox.runtime.AgentRuntime.run`
(blackbox high-level API) delegate their loop body to this class.
"""
from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from blackbox.core.approvals import ApprovalDecision
from blackbox.core.artifacts import Artifact
from blackbox.core.errors import ApprovalError, SessionError
from blackbox.core.events import AgentEvent, EventTypes
from blackbox.core.items import ItemTypes, RunItem
from blackbox.core.policy import (
    ApprovalCallbackPolicy,
    Policy,
    PolicyDecision,
    PolicyRequest,
)
from blackbox.core.state import ProviderState
from blackbox.tools.hosted.calls import HostedToolCall, HostedToolContext
from blackbox.tools.hosted.specs import (
    ApplyPatch,
    ComputerUse,
    HostedToolHandlers,
    HostedToolSpec,
    Memory,
    Shell,
    TextEditor,
)
from blackbox.tools.hosted_runtime import HostedToolRunner
from blackbox.tools.runtime import ToolRuntime
from blackbox.workspaces.provider import PendingWorkspaceOperation, WorkspaceApprovalRequired

ApprovalPolicy = Callable[[str, dict[str, Any]], bool]
ToolPlanRefresh = Callable[
    [int, str | list[Any], ProviderState | None],
    Awaitable[Iterable[AgentEvent]],
]
ToolLateBinder = Callable[
    [str, int],
    Awaitable[tuple[bool, Iterable[AgentEvent]]],
]


@dataclass(slots=True)
class _PendingToolCall:
    call_id: str
    name: str
    arguments: dict[str, Any]


@dataclass(slots=True)
class _PendingHostedToolCall:
    call: HostedToolCall


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
    hosted_tools: list[HostedToolSpec] = field(default_factory=list)
    hosted_tool_handlers: HostedToolHandlers = field(default_factory=HostedToolHandlers)
    hosted_tool_runner: HostedToolRunner = field(default_factory=HostedToolRunner)
    approval_policy: ApprovalPolicy | None = None
    policy: Policy | None = None
    max_iterations: int = 8
    max_tool_calls: int | None = None
    finalizer_tool_name: str | None = None
    tool_plan_refresh: ToolPlanRefresh | None = None
    tool_late_binder: ToolLateBinder | None = None

    _approvals: dict[str, asyncio.Future[ApprovalDecision]] = field(default_factory=dict)
    _cancelled: bool = False
    _tool_calls_used: int = 0

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

            if self.tool_plan_refresh is not None:
                for event in await self.tool_plan_refresh(iteration, turn_input, provider_state):
                    yield event

            pending_calls: list[_PendingToolCall] = []
            pending_hosted_calls: list[_PendingHostedToolCall] = []
            last_state: ProviderState | None = None

            async for event in self.stream_factory(
                input=turn_input, provider_state=provider_state
            ):
                yield AgentEvent(
                    type=event.type,
                    run_id=event.run_id,
                    sequence=event.sequence,
                    trace_id=event.trace_id,
                    span_id=event.span_id,
                    parent_span_id=event.parent_span_id,
                    span_kind=event.span_kind,
                    provider=event.provider,
                    session_id=session_id,
                    item_id=event.item_id,
                    provider_trace_id=event.provider_trace_id,
                    provider_span_id=event.provider_span_id,
                    provider_request_id=event.provider_request_id,
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
                if event.type == EventTypes.HOSTED_TOOL_CALL_REQUESTED:
                    hosted_call = _hosted_call_from_event(event)
                    if hosted_call is not None:
                        pending_hosted_calls.append(_PendingHostedToolCall(hosted_call))
                maybe_state = event.data.get("provider_state")
                if isinstance(maybe_state, ProviderState):
                    last_state = maybe_state

            provider_state = last_state

            if not pending_calls and not pending_hosted_calls:
                return

            hosted_results: list[RunItem] = []
            if pending_hosted_calls:
                context = HostedToolContext(
                    run_id="",
                    session_id=session_id,
                    provider=provider_id,
                    model="",
                    policy=self._effective_policy(),
                )
                for pending in pending_hosted_calls:
                    hosted_call = pending.call
                    yield AgentEvent(
                        type=EventTypes.HOSTED_TOOL_CALL_STARTED,
                        provider=provider_id,
                        session_id=session_id,
                        item_id=hosted_call.item_id or hosted_call.call_id,
                        data={
                            "call_id": hosted_call.call_id,
                            "hosted_tool_type": hosted_call.hosted_tool_type,
                            "provider_item_type": hosted_call.provider_item_type,
                            "arguments": dict(hosted_call.arguments),
                        },
                        raw=hosted_call.raw,
                    )
                    try:
                        output = await self.hosted_tool_runner.call(
                            hosted_call,
                            context=context,
                            handlers=self.hosted_tool_handlers,
                            spec=_find_matching_hosted_spec(hosted_call, self.hosted_tools),
                        )
                    except Exception as exc:
                        yield AgentEvent(
                            type=EventTypes.HOSTED_TOOL_CALL_FAILED,
                            provider=provider_id,
                            session_id=session_id,
                            item_id=hosted_call.item_id or hosted_call.call_id,
                            data={
                                "call_id": hosted_call.call_id,
                                "hosted_tool_type": hosted_call.hosted_tool_type,
                                "error": str(exc),
                            },
                        )
                        raise

                    result_item = self.hosted_tool_runner.output_item(output)
                    hosted_results.append(result_item)
                    for artifact in output.artifacts:
                        yield AgentEvent(
                            type=EventTypes.ARTIFACT_CREATED,
                            provider=provider_id,
                            session_id=session_id,
                            item_id=artifact.id,
                            data={"artifact": artifact},
                        )
                    event_type = {
                        "completed": EventTypes.HOSTED_TOOL_CALL_COMPLETED,
                        "failed": EventTypes.HOSTED_TOOL_CALL_FAILED,
                        "denied": EventTypes.HOSTED_TOOL_CALL_DENIED,
                    }[output.status]
                    yield AgentEvent(
                        type=event_type,
                        provider=provider_id,
                        session_id=session_id,
                        item_id=hosted_call.item_id or hosted_call.call_id,
                        data={
                            "call_id": output.call_id,
                            "hosted_tool_type": output.hosted_tool_type,
                            "content": output.content,
                            "provider_input_item": output.provider_input_item,
                            "item": result_item,
                            "artifact_ids": [artifact.id for artifact in output.artifacts],
                        },
                        raw=output.raw,
                    )
                    yield AgentEvent(
                        type=EventTypes.HOSTED_TOOL_OUTPUT_PREPARED,
                        provider=provider_id,
                        session_id=session_id,
                        item_id=result_item.id,
                        data={
                            "call_id": output.call_id,
                            "hosted_tool_type": output.hosted_tool_type,
                            "provider_input_item": output.provider_input_item,
                            "item": result_item,
                        },
                    )

            if not pending_calls:
                turn_input = hosted_results
                continue

            finalizer_call = self._find_finalizer_call(pending_calls)
            if finalizer_call is not None:
                content = json.dumps(finalizer_call.arguments)
                item = RunItem(
                    type=ItemTypes.FUNCTION_RESULT,
                    provider=provider_id,
                    status="completed",
                    data={
                        "call_id": finalizer_call.call_id,
                        "name": finalizer_call.name,
                        "content": content,
                        "arguments": dict(finalizer_call.arguments),
                    },
                )
                yield AgentEvent(
                    type=EventTypes.TOOL_CALL_COMPLETED,
                    provider=provider_id,
                    session_id=session_id,
                    item_id=finalizer_call.call_id,
                    data={
                        "call_id": finalizer_call.call_id,
                        "name": finalizer_call.name,
                        "arguments": dict(finalizer_call.arguments),
                        "content": content,
                        "item": item,
                        "metadata": {
                            "blackbox_hidden": True,
                            "purpose": "final_output",
                        },
                    },
                )
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
                if (
                    tool_runtime.allowed_tools is not None
                    and call.name not in tool_runtime.allowed_tools
                ):
                    late_bound = False
                    if self.tool_late_binder is not None:
                        late_bound, late_bind_events = await self.tool_late_binder(
                            call.name,
                            iteration,
                        )
                        for event in late_bind_events:
                            yield event
                    if not late_bound:
                        tool_results[index] = RunItem(
                            type=ItemTypes.FUNCTION_RESULT,
                            provider=provider_id,
                            status="failed",
                            data={
                                "call_id": call.call_id,
                                "name": call.name,
                                "error": "tool_not_in_selected_plan",
                            },
                        )
                        yield AgentEvent(
                            type=EventTypes.TOOL_CHOICE_REJECTED,
                            provider=provider_id,
                            session_id=session_id,
                            item_id=call.call_id,
                            data={
                                "call_id": call.call_id,
                                "name": call.name,
                                "reason": "tool_not_in_selected_plan",
                                "checkpoint": "tool_routing",
                            },
                        )
                        continue

                if (
                    self.max_tool_calls is not None
                    and self._tool_calls_used >= self.max_tool_calls
                ):
                    tool_results[index] = RunItem(
                        type=ItemTypes.FUNCTION_RESULT,
                        provider=provider_id,
                        status="failed",
                        data={
                            "call_id": call.call_id,
                            "name": call.name,
                            "error": "tool_call_budget_exceeded",
                            "limit": self.max_tool_calls,
                        },
                    )
                    yield AgentEvent(
                        type=EventTypes.TOOL_CHOICE_REJECTED,
                        provider=provider_id,
                        session_id=session_id,
                        item_id=call.call_id,
                        data={
                            "call_id": call.call_id,
                            "name": call.name,
                            "reason": "max_tool_calls_exceeded",
                            "limit": self.max_tool_calls,
                        },
                    )
                    continue
                if effective_policy is not None:
                    decision = await effective_policy.check(_policy_request_for_call(call))
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
                    yield AgentEvent(
                        type=EventTypes.TOOL_CHOICE_REJECTED,
                        provider=provider_id,
                        session_id=session_id,
                        item_id=call.call_id,
                        data={
                            "call_id": call.call_id,
                            "name": call.name,
                            "reason": decision.reason or "denied_by_policy",
                            "checkpoint": "before_tool_call",
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
                        yield AgentEvent(
                            type=EventTypes.TOOL_CHOICE_REJECTED,
                            provider=provider_id,
                            session_id=session_id,
                            item_id=call.call_id,
                            data={
                                "call_id": call.call_id,
                                "name": call.name,
                                "reason": self._last_decision.reason
                                or "denied_by_approval",
                                "checkpoint": "approval",
                            },
                        )
                        continue

                allowed_calls.append((index, call))
                self._tool_calls_used += 1

            for _, call in allowed_calls:
                yield AgentEvent(
                    type=EventTypes.TOOL_CALL_STARTED,
                    provider=provider_id,
                    session_id=session_id,
                    item_id=call.call_id,
                    data={"call_id": call.call_id, "name": call.name},
                )
                yield AgentEvent(
                    type=EventTypes.TOOL_CHOICE_CALLED,
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
                if isinstance(result, WorkspaceApprovalRequired):
                    async for ev in self._await_workspace_approval(
                        result.pending,
                        session_id=session_id,
                        provider_id=provider_id,
                    ):
                        yield ev
                    await result.approve(self._last_decision)
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
                    result = await tool_runtime.call(
                        call.name,
                        call.arguments,
                        mock=mock_tools,
                    )

                if isinstance(result, BaseException):
                    yield AgentEvent(
                        type=EventTypes.TOOL_CALL_FAILED,
                        provider=provider_id,
                        session_id=session_id,
                        item_id=call.call_id,
                        data={"call_id": call.call_id, "name": call.name, "error": str(result)},
                    )
                    yield AgentEvent(
                        type=EventTypes.TOOL_CHOICE_FAILED,
                        provider=provider_id,
                        session_id=session_id,
                        item_id=call.call_id,
                        data={
                            "call_id": call.call_id,
                            "name": call.name,
                            "error": str(result),
                        },
                    )
                    tool_results[index] = RunItem(
                        type=ItemTypes.FUNCTION_RESULT,
                        provider=provider_id,
                        status="failed",
                        data={"call_id": call.call_id, "name": call.name, "error": str(result)},
                    )
                    continue

                artifact_ids_from_events: set[str] = set()
                for tool_event in result.events:
                    if tool_event.type == EventTypes.ARTIFACT_CREATED:
                        event_artifact = tool_event.data.get("artifact")
                        if isinstance(event_artifact, Artifact):
                            artifact_ids_from_events.add(event_artifact.id)
                    yield AgentEvent(
                        type=tool_event.type,
                        run_id=tool_event.run_id,
                        sequence=tool_event.sequence,
                        trace_id=tool_event.trace_id,
                        span_id=tool_event.span_id,
                        parent_span_id=tool_event.parent_span_id,
                        span_kind=tool_event.span_kind,
                        provider=tool_event.provider,
                        session_id=session_id,
                        item_id=tool_event.item_id,
                        provider_trace_id=tool_event.provider_trace_id,
                        provider_span_id=tool_event.provider_span_id,
                        provider_request_id=tool_event.provider_request_id,
                        data=tool_event.data,
                        raw=tool_event.raw,
                    )
                for artifact in result.artifacts:
                    if artifact.id in artifact_ids_from_events:
                        continue
                    yield AgentEvent(
                        type=EventTypes.ARTIFACT_CREATED,
                        provider=provider_id,
                        session_id=session_id,
                        item_id=artifact.id,
                        data={"artifact": artifact},
                    )

                completed_data: dict[str, Any] = {
                    "call_id": call.call_id,
                    "name": call.name,
                    "content": result.content,
                }
                if result.payload is not None:
                    completed_data["payload"] = result.payload
                if result.metadata:
                    completed_data["metadata"] = dict(result.metadata)
                if result.artifacts:
                    completed_data["artifact_ids"] = [artifact.id for artifact in result.artifacts]

                yield AgentEvent(
                    type=EventTypes.TOOL_CALL_COMPLETED,
                    provider=provider_id,
                    session_id=session_id,
                    item_id=call.call_id,
                    data=completed_data,
                )
                tool_results[index] = (
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

            turn_input = [
                *hosted_results,
                *[item for item in tool_results if item is not None],
            ]
        else:
            yield AgentEvent(
                type=EventTypes.SESSION_FAILED,
                provider=provider_id,
                session_id=session_id,
                data={"reason": "max_iterations_reached", "limit": self.max_iterations},
            )

    def _find_finalizer_call(
        self, pending_calls: list[_PendingToolCall]
    ) -> _PendingToolCall | None:
        if self.finalizer_tool_name is None:
            return None
        for call in pending_calls:
            if call.name == self.finalizer_tool_name:
                return call
        return None

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

    async def _await_workspace_approval(
        self,
        pending: PendingWorkspaceOperation,
        *,
        session_id: str | None,
        provider_id: str,
    ) -> AsyncIterator[AgentEvent]:
        approval_id = pending.id
        yield AgentEvent(
            type=EventTypes.APPROVAL_REQUESTED,
            provider=provider_id,
            session_id=session_id,
            item_id=approval_id,
            data={
                "approval_id": approval_id,
                "action": pending.operation,
                "arguments": pending.arguments,
                "workspace_id": pending.workspace_id,
                "workspace_provider": pending.provider,
                "reason": pending.reason,
                "metadata": dict(pending.metadata),
            },
        )
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


def _hosted_call_from_event(event: AgentEvent) -> HostedToolCall | None:
    hosted_tool_type = event.data.get("hosted_tool_type")
    if not isinstance(hosted_tool_type, str):
        item = event.data.get("item")
        if isinstance(item, RunItem):
            value = item.data.get("hosted_tool_type")
            hosted_tool_type = value if isinstance(value, str) else None
    if not isinstance(hosted_tool_type, str):
        return None
    call_id = event.data.get("call_id") or event.item_id or ""
    if not isinstance(call_id, str):
        call_id = str(call_id)
    arguments = event.data.get("arguments")
    if not isinstance(arguments, dict):
        arguments = {}
    provider_item_type = event.data.get("provider_item_type") or event.data.get("item_type") or ""
    status = event.data.get("status")
    return HostedToolCall(
        provider=event.provider or "",
        hosted_tool_type=hosted_tool_type,
        provider_item_type=str(provider_item_type),
        call_id=call_id,
        item_id=event.item_id,
        status=status if isinstance(status, str) else None,
        arguments=dict(arguments),
        raw=event.raw,
        metadata=dict(event.data.get("metadata") or {}),
    )


def _policy_request_for_call(call: _PendingToolCall) -> PolicyRequest:
    if call.name.startswith("mcp:"):
        server_tool = call.name[4:]
        server, sep, tool = server_tool.partition(".")
        return PolicyRequest(
            checkpoint="before_mcp_call",
            action=call.name,
            arguments=dict(call.arguments),
            metadata={
                "server": server if sep else None,
                "tool": tool if sep else server_tool,
            },
        )
    return PolicyRequest(
        checkpoint="before_tool_call",
        action=call.name,
        arguments=dict(call.arguments),
    )


def _find_matching_hosted_spec(
    call: HostedToolCall, specs: list[HostedToolSpec]
) -> HostedToolSpec | None:
    for spec in specs:
        if call.hosted_tool_type == "shell" and isinstance(spec, Shell):
            return spec
        if call.hosted_tool_type == "apply_patch" and isinstance(spec, ApplyPatch):
            return spec
        if call.hosted_tool_type == "computer" and isinstance(spec, ComputerUse):
            return spec
        if call.hosted_tool_type == "text_editor" and isinstance(spec, TextEditor):
            return spec
        if call.hosted_tool_type == "memory" and isinstance(spec, Memory):
            return spec
    return None
