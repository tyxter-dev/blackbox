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

from blackbox.core.approvals import ApprovalDecision
from blackbox.core.artifacts import ArtifactPage
from blackbox.core.capabilities import AgentCapabilities
from blackbox.core.errors import ProviderNotConfiguredError, UnsupportedFeatureError
from blackbox.core.events import AgentEvent
from blackbox.core.sessions import AgentRef, AgentSession, InvocationRef, SessionRef
from blackbox.providers.base import AgentSpec, TaskSpec


class VertexAIAgentEngineProvider:
    """Scaffold for the Vertex AI Agent Engine cloud agent surface."""

    provider_id = "vertex-agent-engine"

    def __init__(self, project: str | None = None) -> None:
        self.project = project

    def capabilities(self) -> AgentCapabilities:
        return AgentCapabilities(supports_sessions=False, supports_streaming_events=False)

    async def create_agent(self, spec: AgentSpec) -> AgentRef:
        if not self.project:
            raise ProviderNotConfiguredError("VertexAIAgentEngineProvider requires a project.")
        raise UnsupportedFeatureError("Vertex AI Agent Engine adapter scaffold only.")

    async def start_session(self, agent: AgentRef | str, task: TaskSpec) -> AgentSession:
        if not self.project:
            raise ProviderNotConfiguredError("VertexAIAgentEngineProvider requires a project.")
        raise UnsupportedFeatureError("Vertex AI Agent Engine adapter scaffold only.")

    async def stream_events(
        self,
        session: SessionRef | AgentSession,
        *,
        after_event_id: str | None = None,
    ) -> AsyncIterator[AgentEvent]:
        raise UnsupportedFeatureError("Vertex AI Agent Engine adapter scaffold only.")
        yield  # pragma: no cover

    async def send_message(
        self, session: SessionRef | AgentSession, message: str
    ) -> InvocationRef:
        raise UnsupportedFeatureError("Vertex AI Agent Engine adapter scaffold only.")

    async def approve(self, approval_id: str, decision: ApprovalDecision) -> None:
        raise UnsupportedFeatureError("Vertex AI Agent Engine adapter scaffold only.")

    async def cancel(self, session: SessionRef | AgentSession) -> None:
        raise UnsupportedFeatureError("Vertex AI Agent Engine adapter scaffold only.")

    async def list_artifacts(
        self,
        session: SessionRef | AgentSession,
        *,
        type: str | None = None,
        after: str | None = None,
        limit: int = 100,
    ) -> ArtifactPage:
        raise UnsupportedFeatureError("Vertex AI Agent Engine adapter scaffold only.")


# Deprecated alias preserved temporarily so existing imports keep working.
GoogleAgentPlatformProvider = VertexAIAgentEngineProvider
