from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field

from agent_runtime.core.approvals import ApprovalDecision
from agent_runtime.core.artifacts import Artifact
from agent_runtime.core.capabilities import AgentCapabilities
from agent_runtime.core.events import AgentEvent, EventTypes
from agent_runtime.core.sessions import AgentRef, AgentSession, SessionRef
from agent_runtime.providers.base import AgentSpec, TaskSpec
from agent_runtime.runtime import ModelRuntime


@dataclass(slots=True)
class LocalAgentProvider:
    """Agent provider backed by a registered ModelProvider.

    This is the bridge between model-level and agent-level APIs. It is useful for
    local agents, tests, and as the fallback when no cloud agent is required.
    """

    models: ModelRuntime
    provider_id: str = "local"
    _agents: dict[str, AgentSpec] = field(default_factory=dict)
    _sessions: dict[str, AgentSession] = field(default_factory=dict)

    def capabilities(self) -> AgentCapabilities:
        return AgentCapabilities(
            supports_sessions=True,
            supports_streaming_events=True,
            supports_artifacts=False,
            supports_workspace=False,
            supports_approvals=False,
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
        async for event in self.models.stream(provider=model_ref, input=session_obj.task):
            yield AgentEvent(
                type=event.type,
                provider=event.provider,
                session_id=session_obj.id,
                item_id=event.item_id,
                data=event.data,
                raw=event.raw,
            )

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
        raise NotImplementedError("LocalAgentProvider does not support approvals yet.")

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
