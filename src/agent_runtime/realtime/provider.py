from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from agent_runtime.core.content import ContentItem
from agent_runtime.core.events import AgentEvent
from agent_runtime.core.realtime import ToolMode, TransportKind
from agent_runtime.core.sessions import InvocationRef, SessionRef
from agent_runtime.core.state import ProviderState
from agent_runtime.hosted_tools import HostedToolSpec
from agent_runtime.realtime.capabilities import (
    RealtimeCapabilities,
    RealtimeCapabilityProfile,
)
from agent_runtime.tools.registry import ToolDefinition


@dataclass(slots=True, frozen=True)
class AudioConfig:
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
    type: str = "server_vad"
    create_response: bool = True
    interrupt_response: bool = True
    silence_duration_ms: int | None = None
    threshold: float | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class RealtimeSessionConfig:
    instructions: str | None = None
    input_modalities: list[str] = field(default_factory=lambda: ["text", "audio"])
    output_modalities: list[str] = field(default_factory=lambda: ["text"])
    audio: AudioConfig | None = None
    turn_detection: TurnDetectionConfig | None = field(default_factory=TurnDetectionConfig)
    metadata: dict[str, Any] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class RealtimeConnectRequest:
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
    model: str | None = None
    transport: TransportKind = "websocket"
    provider_session_id: str | None = None
    provider_state: ProviderState | None = None


@dataclass(slots=True, frozen=True)
class RealtimeClientCommand:
    type: str
    data: dict[str, Any] = field(default_factory=dict)
    content: ContentItem | None = None
    raw: Any | None = None


@runtime_checkable
class RealtimeProvider(Protocol):
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
