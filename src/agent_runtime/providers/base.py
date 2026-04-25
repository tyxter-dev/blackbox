from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from agent_runtime.core.approvals import ApprovalDecision
from agent_runtime.core.artifacts import Artifact, ArtifactPage, ArtifactRef
from agent_runtime.core.capabilities import AgentCapabilities, ModelCapabilities
from agent_runtime.core.events import AgentEvent
from agent_runtime.core.sessions import AgentRef, AgentSession, InvocationRef, SessionRef
from agent_runtime.core.state import ProviderState


@dataclass(slots=True)
class ModelRequestControls:
    """Provider-native controls for a single model turn.

    Adapters map these fields to their native API shape when supported. The
    lower-level ``extra`` request payload remains available for provider
    fields that are not part of this common surface.
    """

    instructions: str | None = None
    temperature: float | None = None
    top_p: float | None = None
    max_output_tokens: int | None = None
    tool_choice: Any | None = None
    parallel_tool_calls: bool | None = None
    reasoning_effort: str | None = None


@dataclass(slots=True)
class TurnRequest:
    """Request for a single model-provider turn."""

    model: str
    input: str | list[Any]
    provider_state: ProviderState | None = None
    tools: list[Any] = field(default_factory=list)
    controls: ModelRequestControls = field(default_factory=ModelRequestControls)
    artifacts: list[ArtifactRef] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class TurnResult:
    """Collected result of a model-provider turn."""

    text: str
    events: list[AgentEvent]
    provider_state: ProviderState | None = None
    artifacts: list[Artifact] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class AgentSpec:
    """Definition of an agent that can be local or hosted/cloud-managed."""

    name: str
    instructions: str = ""
    model: str | None = None
    tools: list[str] = field(default_factory=list)
    mcp_servers: list[Any] = field(default_factory=list)
    environment: dict[str, Any] = field(default_factory=dict)
    permissions: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class TaskSpec:
    """A task submitted to an agent provider."""

    prompt: str
    model: str | None = None
    workspace: Any | None = None
    inputs: list[ArtifactRef] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class ModelProvider(Protocol):
    """Provider that runs model turns.

    Examples: OpenAI Responses, Anthropic Messages, Gemini GenerateContent.
    """

    @property
    def provider_id(self) -> str:
        ...

    def capabilities(self, model: str | None = None) -> ModelCapabilities:
        ...

    def stream_turn(self, request: TurnRequest) -> AsyncIterator[AgentEvent]:
        ...


@runtime_checkable
class AgentProvider(Protocol):
    """Provider that runs agent sessions.

    Examples: OpenAI cloud coding agents, Anthropic managed agents,
    Google Agent Platform, or local model-backed agents.
    """

    @property
    def provider_id(self) -> str:
        ...

    def capabilities(self) -> AgentCapabilities:
        ...

    async def create_agent(self, spec: AgentSpec) -> AgentRef:
        ...

    async def start_session(self, agent: AgentRef | str, task: TaskSpec) -> AgentSession:
        ...

    def stream_events(
        self,
        session: SessionRef | AgentSession,
        *,
        after_event_id: str | None = None,
    ) -> AsyncIterator[AgentEvent]:
        ...

    async def send_message(
        self, session: SessionRef | AgentSession, message: str
    ) -> InvocationRef:
        ...

    async def approve(self, approval_id: str, decision: ApprovalDecision) -> None:
        ...

    async def cancel(self, session: SessionRef | AgentSession) -> None:
        ...

    async def list_artifacts(
        self,
        session: SessionRef | AgentSession,
        *,
        type: str | None = None,
        after: str | None = None,
        limit: int = 100,
    ) -> ArtifactPage:
        ...
