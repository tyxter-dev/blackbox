from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from agent_runtime.core.approvals import ApprovalDecision
from agent_runtime.core.artifacts import ArtifactPage
from agent_runtime.core.capabilities import AgentCapabilities
from agent_runtime.core.events import AgentEvent, EventTypes
from agent_runtime.core.sessions import (
    AgentRef,
    AgentSession,
    InvocationRef,
    SessionRef,
    SessionStatus,
)
from agent_runtime.loop import AgentLoop, ApprovalPolicy
from agent_runtime.providers.base import AgentSpec, TaskSpec
from agent_runtime.runtime import ModelRuntime
from agent_runtime.tools.runtime import ToolRuntime


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
        return session

    async def stream_events(
        self,
        session: SessionRef | AgentSession,
        *,
        after_event_id: str | None = None,
    ) -> AsyncIterator[AgentEvent]:
        session_obj = self._session(session)
        session_obj.status = "running"
        yield AgentEvent(
            type=EventTypes.SESSION_STARTED,
            provider=self.provider_id,
            session_id=session_obj.id,
            data={"agent_id": session_obj.agent_id},
        )

        model_ref = session_obj.model or "echo/echo-mini"

        async def stream_factory(
            *, input: str | list[Any], provider_state: Any
        ) -> AsyncIterator[AgentEvent]:
            async for event in self.models.stream(
                provider=model_ref, input=input, provider_state=provider_state,
            ):
                yield event

        loop = AgentLoop(
            stream_factory=stream_factory,
            tools=self.tools,
            approval_policy=self.approval_policy,
            max_iterations=self.max_iterations,
        )
        self._loops[session_obj.id] = loop

        terminal_seen: str | None = None
        async for event in loop.run(
            input=session_obj.task,
            session_id=session_obj.id,
            provider_id=self.provider_id,
        ):
            if event.type == EventTypes.APPROVAL_REQUESTED:
                session_obj.status = "waiting"
            elif event.type in {EventTypes.APPROVAL_APPROVED, EventTypes.APPROVAL_DENIED}:
                session_obj.status = "running"
            elif event.type in {EventTypes.SESSION_CANCELLED, EventTypes.SESSION_FAILED}:
                terminal_seen = event.type
            current_status: SessionStatus = session_obj.status
            if current_status == "cancelled":
                loop.cancel()
            yield event

        if terminal_seen == EventTypes.SESSION_CANCELLED:
            session_obj.status = "cancelled"
            return
        if terminal_seen == EventTypes.SESSION_FAILED:
            session_obj.status = "failed"
            return

        session_obj.status = "completed"
        yield AgentEvent(
            type=EventTypes.SESSION_COMPLETED,
            provider=self.provider_id,
            session_id=session_obj.id,
            data={"agent_id": session_obj.agent_id},
        )

    async def send_message(
        self, session: SessionRef | AgentSession, message: str
    ) -> InvocationRef:
        session_obj = self._session(session)
        session_obj.task = f"{session_obj.task}\n\nUser: {message}"
        return InvocationRef(
            provider=self.provider_id,
            session_id=session_obj.id,
            id=f"inv_{uuid4().hex}",
        )

    async def approve(self, approval_id: str, decision: ApprovalDecision) -> None:
        for loop in self._loops.values():
            if approval_id in loop._approvals:
                await loop.approve(approval_id, decision)
                return
        from agent_runtime.core.errors import ApprovalError
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

    @property
    def _approvals(self) -> dict[str, Any]:
        """Compat shim for the existing test that reads _approvals directly."""
        merged: dict[str, Any] = {}
        for loop in self._loops.values():
            merged.update(loop._approvals)
        return merged
