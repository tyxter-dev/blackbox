from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any

from agent_runtime.core.approvals import ApprovalDecision
from agent_runtime.core.artifacts import Artifact
from agent_runtime.core.capabilities import AgentCapabilities
from agent_runtime.core.errors import ApprovalError, SessionError
from agent_runtime.core.events import AgentEvent, EventTypes
from agent_runtime.core.items import ItemTypes, RunItem
from agent_runtime.core.sessions import AgentRef, AgentSession, SessionRef, SessionStatus
from agent_runtime.core.state import ProviderState
from agent_runtime.providers.base import AgentSpec, TaskSpec
from agent_runtime.runtime import ModelRuntime
from agent_runtime.tools.runtime import ToolRuntime

ApprovalPolicy = Callable[[str, dict[str, Any]], bool]


@dataclass(slots=True)
class _PendingToolCall:
    call_id: str
    name: str
    arguments: dict[str, Any]


@dataclass(slots=True)
class LocalAgentProvider:
    """Agent provider backed by a registered ModelProvider.

    Drives a multi-turn loop: streams model events, dispatches local tool calls
    through ToolRuntime, optionally pauses for approvals, and feeds tool results
    back as the next model turn until the model produces a final answer.
    """

    models: ModelRuntime
    tools: ToolRuntime | None = None
    approval_policy: ApprovalPolicy | None = None
    max_iterations: int = 8
    provider_id: str = "local"
    _agents: dict[str, AgentSpec] = field(default_factory=dict)
    _sessions: dict[str, AgentSession] = field(default_factory=dict)
    _approvals: dict[str, asyncio.Future[ApprovalDecision]] = field(default_factory=dict)

    def capabilities(self) -> AgentCapabilities:
        return AgentCapabilities(
            supports_sessions=True,
            supports_streaming_events=True,
            supports_artifacts=False,
            supports_workspace=False,
            supports_approvals=self.approval_policy is not None,
            supports_mcp=False,
            supports_cancellation=True,
            supports_resume=False,
        )

    async def create_agent(self, spec: AgentSpec) -> AgentRef:
        agent_id = spec.name
        self._agents[agent_id] = spec
        return AgentRef(provider=self.provider_id, id=agent_id, metadata=spec.metadata)

    async def start_session(self, agent: AgentRef | str, task: TaskSpec) -> AgentSession:
        agent_id = agent.id if isinstance(agent, AgentRef) else agent
        session = AgentSession(
            provider=self.provider_id,
            agent_id=agent_id,
            task=task.prompt,
            model=task.model,
            status="created",
            metadata=task.metadata,
        )
        self._sessions[session.id] = session
        return session

    async def stream_events(self, session: SessionRef | AgentSession) -> AsyncIterator[AgentEvent]:
        session_obj = self._session(session)
        session_obj.status = "running"
        yield AgentEvent(
            type=EventTypes.SESSION_STARTED,
            provider=self.provider_id,
            session_id=session_obj.id,
            data={"agent_id": session_obj.agent_id},
        )

        model_ref = session_obj.model or "echo/echo-mini"
        turn_input: str | list[Any] = session_obj.task
        provider_state: ProviderState | None = None

        for iteration in range(self.max_iterations):
            current_status: SessionStatus = session_obj.status
            if current_status == "cancelled":
                yield AgentEvent(
                    type=EventTypes.SESSION_CANCELLED,
                    provider=self.provider_id,
                    session_id=session_obj.id,
                    data={"agent_id": session_obj.agent_id, "iteration": iteration},
                )
                return

            pending_calls: list[_PendingToolCall] = []
            last_state: ProviderState | None = None

            async for event in self.models.stream(
                provider=model_ref,
                input=turn_input,
                provider_state=provider_state,
            ):
                yield AgentEvent(
                    type=event.type,
                    provider=event.provider,
                    session_id=session_obj.id,
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
                break

            if self.tools is None:
                raise SessionError(
                    "Model requested a tool call but LocalAgentProvider has no ToolRuntime."
                )

            tool_results: list[RunItem] = []
            for call in pending_calls:
                if self.approval_policy and self.approval_policy(call.name, call.arguments):
                    approval_id = f"approval_{call.call_id}"
                    loop = asyncio.get_running_loop()
                    future: asyncio.Future[ApprovalDecision] = loop.create_future()
                    self._approvals[approval_id] = future
                    session_obj.status = "waiting"
                    yield AgentEvent(
                        type=EventTypes.APPROVAL_REQUESTED,
                        provider=self.provider_id,
                        session_id=session_obj.id,
                        item_id=approval_id,
                        data={
                            "approval_id": approval_id,
                            "action": call.name,
                            "arguments": call.arguments,
                        },
                    )
                    decision = await future
                    session_obj.status = "running"
                    yield AgentEvent(
                        type=(
                            EventTypes.APPROVAL_APPROVED
                            if decision.approved
                            else EventTypes.APPROVAL_DENIED
                        ),
                        provider=self.provider_id,
                        session_id=session_obj.id,
                        item_id=approval_id,
                        data={"approval_id": approval_id, "reason": decision.reason},
                    )
                    if not decision.approved:
                        tool_results.append(
                            RunItem(
                                type=ItemTypes.FUNCTION_RESULT,
                                provider=self.provider_id,
                                status="failed",
                                data={
                                    "call_id": call.call_id,
                                    "name": call.name,
                                    "error": "denied_by_approval",
                                    "reason": decision.reason,
                                },
                            )
                        )
                        continue

                yield AgentEvent(
                    type=EventTypes.TOOL_CALL_STARTED,
                    provider=self.provider_id,
                    session_id=session_obj.id,
                    item_id=call.call_id,
                    data={"call_id": call.call_id, "name": call.name},
                )
                try:
                    result = await self.tools.call(call.name, call.arguments)
                except Exception as exc:
                    yield AgentEvent(
                        type=EventTypes.TOOL_CALL_FAILED,
                        provider=self.provider_id,
                        session_id=session_obj.id,
                        item_id=call.call_id,
                        data={"call_id": call.call_id, "name": call.name, "error": str(exc)},
                    )
                    tool_results.append(
                        RunItem(
                            type=ItemTypes.FUNCTION_RESULT,
                            provider=self.provider_id,
                            status="failed",
                            data={"call_id": call.call_id, "name": call.name, "error": str(exc)},
                        )
                    )
                    continue

                yield AgentEvent(
                    type=EventTypes.TOOL_CALL_COMPLETED,
                    provider=self.provider_id,
                    session_id=session_obj.id,
                    item_id=call.call_id,
                    data={
                        "call_id": call.call_id,
                        "name": call.name,
                        "content": result.content,
                    },
                )
                tool_results.append(
                    RunItem(
                        type=ItemTypes.FUNCTION_RESULT,
                        provider=self.provider_id,
                        status="completed",
                        data={
                            "call_id": call.call_id,
                            "name": call.name,
                            "content": result.content,
                        },
                    )
                )

            turn_input = tool_results
        else:
            session_obj.status = "failed"
            yield AgentEvent(
                type=EventTypes.SESSION_FAILED,
                provider=self.provider_id,
                session_id=session_obj.id,
                data={"reason": "max_iterations_reached", "limit": self.max_iterations},
            )
            return

        session_obj.status = "completed"
        yield AgentEvent(
            type=EventTypes.SESSION_COMPLETED,
            provider=self.provider_id,
            session_id=session_obj.id,
            data={"agent_id": session_obj.agent_id},
        )

    async def send_message(self, session: SessionRef | AgentSession, message: str) -> None:
        session_obj = self._session(session)
        session_obj.task = f"{session_obj.task}\n\nUser: {message}"

    async def approve(self, approval_id: str, decision: ApprovalDecision) -> None:
        future = self._approvals.pop(approval_id, None)
        if future is None or future.done():
            raise ApprovalError(f"No pending approval with id '{approval_id}'.")
        future.set_result(decision)

    async def cancel(self, session: SessionRef | AgentSession) -> None:
        session_obj = self._session(session)
        session_obj.status = "cancelled"

    async def list_artifacts(self, session: SessionRef | AgentSession) -> list[Artifact]:
        self._session(session)
        return []

    def _session(self, session: SessionRef | AgentSession) -> AgentSession:
        if isinstance(session, AgentSession):
            return session
        return self._sessions[session.id]
