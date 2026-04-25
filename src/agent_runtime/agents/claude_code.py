"""Claude Code SDK-backed agent provider (scaffold).

Targets the public Claude Code / Agent SDK surface (headless mode, file
operations, code execution, web search, MCP extensibility, permissions,
session management). The earlier name ``AnthropicManagedAgentProvider`` was
speculative — there is no public "Anthropic Managed Agents" API matching the
agent/environment/session/events abstraction that name implied. A deprecated
alias is preserved at the bottom of this module for now.
"""
from __future__ import annotations

from collections.abc import AsyncIterator

from agent_runtime.core.approvals import ApprovalDecision
from agent_runtime.core.artifacts import ArtifactPage
from agent_runtime.core.capabilities import AgentCapabilities
from agent_runtime.core.errors import ProviderNotConfiguredError, UnsupportedFeatureError
from agent_runtime.core.events import AgentEvent
from agent_runtime.core.sessions import AgentRef, AgentSession, InvocationRef, SessionRef
from agent_runtime.providers.base import AgentSpec, TaskSpec


class ClaudeCodeAgentProvider:
    """Scaffold for an Agent SDK-backed Claude Code provider."""

    provider_id = "claude-code"

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key

    def capabilities(self) -> AgentCapabilities:
        return AgentCapabilities(supports_sessions=False, supports_streaming_events=False)

    async def create_agent(self, spec: AgentSpec) -> AgentRef:
        if not self.api_key:
            raise ProviderNotConfiguredError("ClaudeCodeAgentProvider requires an api_key.")
        raise UnsupportedFeatureError("Claude Code agent adapter scaffold only.")

    async def start_session(self, agent: AgentRef | str, task: TaskSpec) -> AgentSession:
        if not self.api_key:
            raise ProviderNotConfiguredError("ClaudeCodeAgentProvider requires an api_key.")
        raise UnsupportedFeatureError("Claude Code agent adapter scaffold only.")

    async def stream_events(
        self,
        session: SessionRef | AgentSession,
        *,
        after_event_id: str | None = None,
    ) -> AsyncIterator[AgentEvent]:
        raise UnsupportedFeatureError("Claude Code agent adapter scaffold only.")
        yield  # pragma: no cover

    async def send_message(
        self, session: SessionRef | AgentSession, message: str
    ) -> InvocationRef:
        raise UnsupportedFeatureError("Claude Code agent adapter scaffold only.")

    async def approve(self, approval_id: str, decision: ApprovalDecision) -> None:
        raise UnsupportedFeatureError("Claude Code agent adapter scaffold only.")

    async def cancel(self, session: SessionRef | AgentSession) -> None:
        raise UnsupportedFeatureError("Claude Code agent adapter scaffold only.")

    async def list_artifacts(
        self,
        session: SessionRef | AgentSession,
        *,
        type: str | None = None,
        after: str | None = None,
        limit: int = 100,
    ) -> ArtifactPage:
        raise UnsupportedFeatureError("Claude Code agent adapter scaffold only.")


# Deprecated alias preserved temporarily so existing imports keep working.
AnthropicManagedAgentProvider = ClaudeCodeAgentProvider
