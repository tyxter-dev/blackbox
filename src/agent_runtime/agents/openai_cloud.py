from __future__ import annotations

from collections.abc import AsyncIterator

from agent_runtime.core.approvals import ApprovalDecision
from agent_runtime.core.artifacts import ArtifactPage
from agent_runtime.core.capabilities import AgentCapabilities
from agent_runtime.core.errors import ProviderNotConfiguredError, UnsupportedFeatureError
from agent_runtime.core.events import AgentEvent
from agent_runtime.core.sessions import AgentRef, AgentSession, InvocationRef, SessionRef
from agent_runtime.providers.base import AgentSpec, TaskSpec


class OpenAICloudAgentProvider:
    """Scaffold for OpenAI-hosted/cloud coding agents.

    This should model cloud agents as sessions with events/artifacts, not as local tools.
    """

    provider_id = "openai-agent"

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key

    def capabilities(self) -> AgentCapabilities:
        return AgentCapabilities(supports_sessions=False, supports_streaming_events=False)

    async def create_agent(self, spec: AgentSpec) -> AgentRef:
        if not self.api_key:
            raise ProviderNotConfiguredError("OpenAICloudAgentProvider requires an api_key.")
        raise UnsupportedFeatureError("OpenAI cloud agent adapter scaffold only.")

    async def start_session(self, agent: AgentRef | str, task: TaskSpec) -> AgentSession:
        if not self.api_key:
            raise ProviderNotConfiguredError("OpenAICloudAgentProvider requires an api_key.")
        raise UnsupportedFeatureError("OpenAI cloud agent adapter scaffold only.")

    async def stream_events(
        self,
        session: SessionRef | AgentSession,
        *,
        after_event_id: str | None = None,
    ) -> AsyncIterator[AgentEvent]:
        raise UnsupportedFeatureError("OpenAI cloud agent adapter scaffold only.")
        yield  # pragma: no cover

    async def send_message(
        self, session: SessionRef | AgentSession, message: str
    ) -> InvocationRef:
        raise UnsupportedFeatureError("OpenAI cloud agent adapter scaffold only.")

    async def approve(self, approval_id: str, decision: ApprovalDecision) -> None:
        raise UnsupportedFeatureError("OpenAI cloud agent adapter scaffold only.")

    async def cancel(self, session: SessionRef | AgentSession) -> None:
        raise UnsupportedFeatureError("OpenAI cloud agent adapter scaffold only.")

    async def list_artifacts(
        self,
        session: SessionRef | AgentSession,
        *,
        type: str | None = None,
        after: str | None = None,
        limit: int = 100,
    ) -> ArtifactPage:
        raise UnsupportedFeatureError("OpenAI cloud agent adapter scaffold only.")
