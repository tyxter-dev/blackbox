from __future__ import annotations

from collections.abc import AsyncIterator

from agent_runtime.core.approvals import ApprovalDecision
from agent_runtime.core.artifacts import Artifact
from agent_runtime.core.capabilities import AgentCapabilities
from agent_runtime.core.errors import ProviderNotConfiguredError
from agent_runtime.core.events import AgentEvent
from agent_runtime.core.sessions import AgentRef, AgentSession, SessionRef
from agent_runtime.providers.base import AgentSpec, TaskSpec


class GoogleAgentPlatformProvider:
    """Scaffold for Google Agent Platform / Agents CLI flows.

    This adapter should eventually expose create/eval/deploy/observe operations in
    addition to normal run sessions.
    """

    provider_id = "google-agent"

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
            raise ProviderNotConfiguredError("GoogleAgentPlatformProvider requires a project.")
        raise NotImplementedError("Google Agent Platform adapter scaffold only.")

    async def start_session(self, agent: AgentRef | str, task: TaskSpec) -> AgentSession:
        if not self.project:
            raise ProviderNotConfiguredError("GoogleAgentPlatformProvider requires a project.")
        raise NotImplementedError("Google Agent Platform adapter scaffold only.")

    async def stream_events(self, session: SessionRef | AgentSession) -> AsyncIterator[AgentEvent]:
        raise NotImplementedError("Google Agent Platform adapter scaffold only.")
        yield  # pragma: no cover

    async def send_message(self, session: SessionRef | AgentSession, message: str) -> None:
        raise NotImplementedError("Google Agent Platform adapter scaffold only.")

    async def approve(self, approval_id: str, decision: ApprovalDecision) -> None:
        raise NotImplementedError("Google Agent Platform adapter scaffold only.")

    async def cancel(self, session: SessionRef | AgentSession) -> None:
        raise NotImplementedError("Google Agent Platform adapter scaffold only.")

    async def list_artifacts(self, session: SessionRef | AgentSession) -> list[Artifact]:
        raise NotImplementedError("Google Agent Platform adapter scaffold only.")
