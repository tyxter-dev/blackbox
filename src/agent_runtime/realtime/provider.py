from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from agent_runtime.core.content import ContentItem
from agent_runtime.core.events import AgentEvent
from agent_runtime.core.realtime import ToolMode, TransportKind
from agent_runtime.core.sessions import InvocationRef, SessionRef
from agent_runtime.core.state import ProviderState
from agent_runtime.realtime.capabilities import (
    RealtimeCapabilities,
    RealtimeCapabilityProfile,
)
from agent_runtime.tools.hosted.specs import HostedToolSpec
from agent_runtime.tools.registry import ToolDefinition


@dataclass(slots=True, frozen=True)
class AudioConfig:
    """Audio formats, transcription, and voice settings for realtime sessions."""

    input_format: str | None = None
    output_format: str | None = None
    input_sample_rate_hz: int | None = None
    output_sample_rate_hz: int | None = None
    input_channels: int = 1
    output_channels: int = 1
    transcription_model: str | None = None
    voice: str | None = None


@dataclass(slots=True, frozen=True)
class TurnDetectionConfig:
    """Server or provider VAD settings for realtime turn boundaries."""

    type: str = "server_vad"
    create_response: bool = True
    interrupt_response: bool = True
    silence_duration_ms: int | None = None
    threshold: float | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class RealtimeSessionConfig:
    """Mutable provider session configuration.

    Providers use this at connect time and may accept later updates through
    ``RealtimeProvider.update_session``. ``extra`` carries provider-native
    session options that the common runtime surface does not model yet.
    """

    instructions: str | None = None
    input_modalities: list[str] = field(default_factory=lambda: ["text", "audio"])
    output_modalities: list[str] = field(default_factory=lambda: ["text"])
    audio: AudioConfig | None = None
    turn_detection: TurnDetectionConfig | None = field(default_factory=TurnDetectionConfig)
    metadata: dict[str, Any] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class RealtimeConnectRequest:
    """Request to open a realtime model session.

    The runtime resolves tools and hosted tools before connect, chooses whether
    tool calls are manually handled or provider-managed via ``tool_mode``, and
    passes any resumable ``provider_state`` through to the adapter.
    """

    model: str
    config: RealtimeSessionConfig = field(default_factory=RealtimeSessionConfig)
    transport: TransportKind = "websocket"
    tools: list[ToolDefinition] = field(default_factory=list)
    hosted_tools: list[HostedToolSpec] = field(default_factory=list)
    tool_mode: ToolMode = "manual"
    provider_state: ProviderState | None = None
    run_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class RealtimeSessionRef(SessionRef):
    """Stable handle for an opened realtime session."""

    model: str | None = None
    transport: TransportKind = "websocket"
    provider_session_id: str | None = None
    provider_state: ProviderState | None = None


@dataclass(slots=True, frozen=True)
class RealtimeClientCommand:
    """Client-to-provider command sent into an open realtime session."""

    type: str
    data: dict[str, Any] = field(default_factory=dict)
    content: ContentItem | None = None
    raw: Any | None = None


@runtime_checkable
class RealtimeProvider(Protocol):
    """Provider adapter contract for bidirectional realtime sessions.

    Adapters connect sessions, stream provider events, accept client commands,
    apply session updates, and close provider resources. The protocol mirrors
    the normal model-provider contract but keeps session transport and
    low-latency command flow explicit.
    """

    @property
    def provider_id(self) -> str:
        ...

    def capabilities(self, model: str | None = None) -> RealtimeCapabilities:
        ...

    def capability_profile(self, model: str | None = None) -> RealtimeCapabilityProfile:
        ...

    async def connect(self, request: RealtimeConnectRequest) -> RealtimeSessionRef:
        ...

    def stream_events(
        self,
        session: RealtimeSessionRef,
        *,
        after_event_id: str | None = None,
    ) -> AsyncIterator[AgentEvent]:
        ...

    async def send(
        self,
        session: RealtimeSessionRef,
        command: RealtimeClientCommand,
    ) -> InvocationRef:
        ...

    async def update_session(
        self,
        session: RealtimeSessionRef,
        config: RealtimeSessionConfig,
    ) -> None:
        ...

    async def close(self, session: RealtimeSessionRef) -> None:
        ...
