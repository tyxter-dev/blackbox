"""Vertex AI Agent Engine provider (scaffold).

Targets the Vertex AI Agent Engine Sessions surface (sessions, events, state,
memory, evaluation, monitoring, deployment) and Google ADK runtime concepts
(event loop, session/artifact/memory services). The earlier name
``GoogleAgentPlatformProvider`` was vague — Google currently exposes two
distinct surfaces (ADK locally and Vertex AI Agent Engine in the cloud), so
this adapter targets the cloud-managed one. A deprecated alias is kept below.
"""
from __future__ import annotations

from collections.abc import AsyncIterator

from agent_runtime.core.approvals import ApprovalDecision
from agent_runtime.core.artifacts import ArtifactPage
from agent_runtime.core.capabilities import AgentCapabilities
from agent_runtime.core.errors import ProviderNotConfiguredError
from agent_runtime.core.events import AgentEvent
from agent_runtime.core.sessions import AgentRef, AgentSession, InvocationRef, SessionRef
from agent_runtime.providers.base import AgentSpec, TaskSpec


class VertexAIAgentEngineProvider:
    """Scaffold for the Vertex AI Agent Engine cloud agent surface."""

    provider_id = "vertex-agent-engine"

    def __init__(self, project: str | None = None) -> None:
        self.project = project

    def capabilities(self) -> AgentCapabilities:
        return AgentCapabilities(
            supports_sessions=True,
            supports_streaming_events=True,
            supports_artifacts=True,
            supports_workspace=True,
            supports_approvals=True,
            supports_mcp=True,
            supports_deployments=True,
            supports_evals=True,
            supports_cancellation=True,
            supports_resume=True,
        )

    async def create_agent(self, spec: AgentSpec) -> AgentRef:
        if not self.project:
            raise ProviderNotConfiguredError("VertexAIAgentEngineProvider requires a project.")
        raise NotImplementedError("Vertex AI Agent Engine adapter scaffold only.")

    async def start_session(self, agent: AgentRef | str, task: TaskSpec) -> AgentSession:
        if not self.project:
            raise ProviderNotConfiguredError("VertexAIAgentEngineProvider requires a project.")
        raise NotImplementedError("Vertex AI Agent Engine adapter scaffold only.")

    async def stream_events(
        self,
        session: SessionRef | AgentSession,
        *,
        after_event_id: str | None = None,
    ) -> AsyncIterator[AgentEvent]:
        raise NotImplementedError("Vertex AI Agent Engine adapter scaffold only.")
        yield  # pragma: no cover

    async def send_message(
        self, session: SessionRef | AgentSession, message: str
    ) -> InvocationRef:
        raise NotImplementedError("Vertex AI Agent Engine adapter scaffold only.")

    async def approve(self, approval_id: str, decision: ApprovalDecision) -> None:
        raise NotImplementedError("Vertex AI Agent Engine adapter scaffold only.")

    async def cancel(self, session: SessionRef | AgentSession) -> None:
        raise NotImplementedError("Vertex AI Agent Engine adapter scaffold only.")

    async def list_artifacts(
        self,
        session: SessionRef | AgentSession,
        *,
        type: str | None = None,
        after: str | None = None,
        limit: int = 100,
    ) -> ArtifactPage:
        raise NotImplementedError("Vertex AI Agent Engine adapter scaffold only.")


# Deprecated alias preserved temporarily so existing imports keep working.
GoogleAgentPlatformProvider = VertexAIAgentEngineProvider
