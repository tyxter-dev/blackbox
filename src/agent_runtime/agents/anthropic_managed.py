from __future__ import annotations

from collections.abc import AsyncIterator

from agent_runtime.core.approvals import ApprovalDecision
from agent_runtime.core.artifacts import Artifact
from agent_runtime.core.capabilities import AgentCapabilities
from agent_runtime.core.errors import ProviderNotConfiguredError
from agent_runtime.core.events import AgentEvent
from agent_runtime.core.sessions import AgentRef, AgentSession, SessionRef
from agent_runtime.providers.base import AgentSpec, TaskSpec


class AnthropicManagedAgentProvider:
    """Scaffold for Anthropic managed-agent sessions."""

    provider_id = "anthropic-agent"

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key

    def capabilities(self) -> AgentCapabilities:
        return AgentCapabilities(
            supports_sessions=True,
            supports_streaming_events=True,
            supports_artifacts=True,
            supports_workspace=True,
            supports_approvals=True,
            supports_mcp=True,
            supports_cancellation=True,
            supports_resume=True,
        )

    async def create_agent(self, spec: AgentSpec) -> AgentRef:
        if not self.api_key:
            raise ProviderNotConfiguredError("AnthropicManagedAgentProvider requires an api_key.")
        raise NotImplementedError("Anthropic managed-agent adapter scaffold only.")

    async def start_session(self, agent: AgentRef | str, task: TaskSpec) -> AgentSession:
        if not self.api_key:
            raise ProviderNotConfiguredError("AnthropicManagedAgentProvider requires an api_key.")
        raise NotImplementedError("Anthropic managed-agent adapter scaffold only.")

    async def stream_events(self, session: SessionRef | AgentSession) -> AsyncIterator[AgentEvent]:
        raise NotImplementedError("Anthropic managed-agent adapter scaffold only.")
        yield  # pragma: no cover

    async def send_message(self, session: SessionRef | AgentSession, message: str) -> None:
        raise NotImplementedError("Anthropic managed-agent adapter scaffold only.")

    async def approve(self, approval_id: str, decision: ApprovalDecision) -> None:
        raise NotImplementedError("Anthropic managed-agent adapter scaffold only.")

    async def cancel(self, session: SessionRef | AgentSession) -> None:
        raise NotImplementedError("Anthropic managed-agent adapter scaffold only.")

    async def list_artifacts(self, session: SessionRef | AgentSession) -> list[Artifact]:
        raise NotImplementedError("Anthropic managed-agent adapter scaffold only.")
