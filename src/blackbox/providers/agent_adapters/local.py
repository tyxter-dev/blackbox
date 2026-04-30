from __future__ import annotations

from collections import deque
from collections.abc import AsyncIterator
from dataclasses import dataclass, field, replace
from typing import Any
from uuid import uuid4

from blackbox.core.approvals import ApprovalDecision
from blackbox.core.artifacts import ArtifactPage
from blackbox.core.capabilities import AgentCapabilities, ControlName
from blackbox.core.events import AgentEvent, EventTypes
from blackbox.core.results import (
    AgentResponseSpec,
    agent_response_instructions,
    agent_response_messages,
)
from blackbox.core.sessions import (
    AgentRef,
    AgentSession,
    InvocationRef,
    SessionRef,
    SessionStatus,
)
from blackbox.core.state import ProviderState
from blackbox.providers.base import AgentSpec, TaskSpec
from blackbox.runtime import ModelRuntime
from blackbox.runtime.agent_loop import AgentLoop, ApprovalPolicy
from blackbox.tools.runtime import ToolRuntime


@dataclass(slots=True)
class _PendingInvocation:
    id: str
    input: str | list[Any]


@dataclass(slots=True)
class LocalAgentProvider:
    """Agent provider backed by a registered ModelProvider.

    Owns the session lifecycle and delegates the inner tool/model loop to
    :class:`AgentLoop` so the same execution layer is shared with the
    high-level ``AgentRuntime.run(...)`` API.
    """

    models: ModelRuntime
    tools: ToolRuntime | None = None
    approval_policy: ApprovalPolicy | None = None
    max_iterations: int = 8
    provider_id: str = "local"
    _agents: dict[str, AgentSpec] = field(default_factory=dict)
    _sessions: dict[str, AgentSession] = field(default_factory=dict)
    _session_tasks: dict[str, TaskSpec] = field(default_factory=dict)
    _session_states: dict[str, ProviderState] = field(default_factory=dict)
    _pending_invocations: dict[str, deque[_PendingInvocation]] = field(default_factory=dict)
    _loops: dict[str, AgentLoop] = field(default_factory=dict)

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
        self._session_tasks[session.id] = task
        self._enqueue_invocation(session.id, task.prompt)
        return session

    async def stream_events(
        self,
        session: SessionRef | AgentSession,
        *,
        after_event_id: str | None = None,
    ) -> AsyncIterator[AgentEvent]:
        """Run the local agent loop and stream normalized session, model, and tool events."""
        session_obj = self._session(session)
        invocation = self._next_invocation(session_obj.id)
        if invocation is None:
            return

        session_obj.status = "running"
        yield AgentEvent(
            type=EventTypes.SESSION_STARTED,
            provider=self.provider_id,
            session_id=session_obj.id,
            data={"agent_id": session_obj.agent_id, "invocation_id": invocation.id},
        )

        agent_spec = self._agents.get(session_obj.agent_id or "")
        task_spec = self._session_tasks.get(session_obj.id)
        model_ref = (
            session_obj.model
            or (agent_spec.model if agent_spec is not None else None)
            or (task_spec.model if task_spec is not None else None)
            or "echo/echo-mini"
        )
        response_spec = _response_spec(agent_spec, task_spec)
        instructions = (
            _instructions(agent_spec, response_spec)
            if agent_spec is not None
            and _supports_model_control(self.models, model_ref, "instructions")
            else None
        )
        tool_definitions = _tool_payload(
            self.tools,
            list(agent_spec.tools)
            if agent_spec is not None and _supports_function_tools(self.models, model_ref)
            else [],
        )
        hosted_tools = [
            *(agent_spec.hosted_tools if agent_spec is not None else []),
            *(task_spec.hosted_tools if task_spec is not None else []),
        ]

        async def stream_factory(
            *, input: str | list[Any], provider_state: Any
        ) -> AsyncIterator[AgentEvent]:
            async for event in self.models.stream(
                provider=model_ref,
                input=input,
                provider_state=provider_state,
                tools=tool_definitions,
                hosted_tools=hosted_tools,
                instructions=instructions,
            ):
                yield event

        loop = AgentLoop(
            stream_factory=stream_factory,
            tools=self.tools,
            hosted_tools=hosted_tools,
            approval_policy=self.approval_policy,
            max_iterations=self.max_iterations,
        )
        self._loops[session_obj.id] = loop

        terminal_seen: str | None = None
        last_state = self._session_states.get(session_obj.id)
        text_parts: list[str] = []
        async for event in loop.run(
            input=invocation.input,
            provider_state=last_state,
            session_id=session_obj.id,
            provider_id=self.provider_id,
        ):
            maybe_state = event.data.get("provider_state")
            if isinstance(maybe_state, ProviderState):
                last_state = maybe_state
            if event.type == EventTypes.MODEL_TEXT_DELTA:
                delta = event.data.get("delta")
                if isinstance(delta, str):
                    text_parts.append(delta)
            if event.type == EventTypes.APPROVAL_REQUESTED:
                session_obj.status = "waiting"
            elif event.type in {EventTypes.APPROVAL_APPROVED, EventTypes.APPROVAL_DENIED}:
                session_obj.status = "running"
            elif event.type in {EventTypes.SESSION_CANCELLED, EventTypes.SESSION_FAILED}:
                terminal_seen = event.type
            current_status: SessionStatus = session_obj.status
            if current_status == "cancelled":
                loop.cancel()
            yield _with_invocation(event, invocation.id)

        if last_state is not None:
            self._session_states[session_obj.id] = last_state
        if terminal_seen == EventTypes.SESSION_CANCELLED:
            session_obj.status = "cancelled"
            return
        if terminal_seen == EventTypes.SESSION_FAILED:
            session_obj.status = "failed"
            return

        messages = agent_response_messages("".join(text_parts), response_spec)
        if response_spec.mode == "conversational":
            for message in messages:
                yield AgentEvent(
                    type=EventTypes.AGENT_RESPONSE_MESSAGE_CREATED,
                    provider=self.provider_id,
                    session_id=session_obj.id,
                    item_id=f"{invocation.id}_message_{message.index}",
                    data={
                        "invocation_id": invocation.id,
                        "message": message,
                        "index": message.index,
                        "role": message.role,
                        "content": message.content,
                    },
                )

        session_obj.status = "completed"
        yield AgentEvent(
            type=EventTypes.SESSION_COMPLETED,
            provider=self.provider_id,
            session_id=session_obj.id,
            data={
                "agent_id": session_obj.agent_id,
                "invocation_id": invocation.id,
                "message_count": len(messages),
            },
        )

    async def send_message(self, session: SessionRef | AgentSession, message: str) -> InvocationRef:
        session_obj = self._session(session)
        invocation = self._enqueue_invocation(session_obj.id, message)
        return InvocationRef(
            provider=self.provider_id,
            session_id=session_obj.id,
            id=invocation.id,
        )

    async def approve(self, approval_id: str, decision: ApprovalDecision) -> None:
        for loop in self._loops.values():
            if approval_id in loop._approvals:
                await loop.approve(approval_id, decision)
                return
        from blackbox.core.errors import ApprovalError

        raise ApprovalError(f"No pending approval with id '{approval_id}'.")

    async def cancel(self, session: SessionRef | AgentSession) -> None:
        session_obj = self._session(session)
        session_obj.status = "cancelled"
        loop = self._loops.get(session_obj.id)
        if loop is not None:
            loop.cancel()

    async def list_artifacts(
        self,
        session: SessionRef | AgentSession,
        *,
        type: str | None = None,
        after: str | None = None,
        limit: int = 100,
    ) -> ArtifactPage:
        self._session(session)
        return ArtifactPage(items=[], next_cursor=None, has_more=False)

    def _session(self, session: SessionRef | AgentSession) -> AgentSession:
        if isinstance(session, AgentSession):
            return session
        return self._sessions[session.id]

    def _enqueue_invocation(
        self,
        session_id: str,
        input: str | list[Any],
    ) -> _PendingInvocation:
        invocation = _PendingInvocation(id=f"inv_{uuid4().hex}", input=input)
        self._pending_invocations.setdefault(session_id, deque()).append(invocation)
        return invocation

    def _next_invocation(self, session_id: str) -> _PendingInvocation | None:
        queue = self._pending_invocations.get(session_id)
        if not queue:
            return None
        invocation = queue.popleft()
        if not queue:
            self._pending_invocations.pop(session_id, None)
        return invocation

    @property
    def _approvals(self) -> dict[str, Any]:
        """Compat shim for the existing test that reads _approvals directly."""
        merged: dict[str, Any] = {}
        for loop in self._loops.values():
            merged.update(loop._approvals)
        return merged


def _tool_payload(runtime: ToolRuntime | None, names: list[str]) -> list[dict[str, Any]]:
    if runtime is None or not names:
        return []
    wanted = set(names)
    return [tool for tool in runtime.registry.to_provider_tools() if tool.get("name") in wanted]


def _with_invocation(event: AgentEvent, invocation_id: str) -> AgentEvent:
    if event.data.get("invocation_id") == invocation_id:
        return event
    return replace(event, data={**event.data, "invocation_id": invocation_id})


def _supports_model_control(models: ModelRuntime, model_ref: str, control: ControlName) -> bool:
    try:
        detail = models.capabilities(model_ref).controls.get(control)
    except Exception:
        return False
    return detail is not None and detail.status != "unsupported"


def _response_spec(
    agent_spec: AgentSpec | None,
    task_spec: TaskSpec | None,
) -> AgentResponseSpec:
    if task_spec is not None and task_spec.response is not None:
        return task_spec.response
    if agent_spec is not None:
        return agent_spec.response
    return AgentResponseSpec()


def _instructions(
    agent_spec: AgentSpec,
    response_spec: AgentResponseSpec,
) -> str:
    response_instructions = agent_response_instructions(response_spec)
    if response_instructions is None:
        return agent_spec.instructions
    if not agent_spec.instructions:
        return response_instructions
    return f"{agent_spec.instructions}\n\n{response_instructions}"


def _supports_function_tools(models: ModelRuntime, model_ref: str) -> bool:
    try:
        return models.capabilities(model_ref).summary.supports_function_tools
    except Exception:
        return False
