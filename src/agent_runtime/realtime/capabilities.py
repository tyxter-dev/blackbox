from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agent_runtime.core.capabilities import CapabilityDetail
from agent_runtime.core.realtime import TransportKind


@dataclass(slots=True, frozen=True)
class RealtimeCapabilities:
    """Summary of realtime features supported by a provider and model."""

    supports_sessions: bool = True
    supported_transports: tuple[TransportKind, ...] = ("websocket",)
    input_modalities: tuple[str, ...] = ("text",)
    output_modalities: tuple[str, ...] = ("text",)
    audio_input_formats: tuple[str, ...] = ()
    audio_output_formats: tuple[str, ...] = ()
    supports_vad: bool = False
    supports_manual_response: bool = False
    supports_interruption: bool = False
    supports_truncation: bool = False
    supports_input_transcription: bool = False
    supports_output_transcription: bool = False
    supports_function_tools: bool = False
    supports_hosted_tools: bool = False
    supports_remote_mcp: bool = False
    supports_tool_cancellation: bool = False
    supports_guardrails: bool = False
    supports_handoffs: bool = False
    supports_history_updates: bool = False
    supports_usage: bool = False
    supports_session_resume: bool = False
    supports_client_tokens: bool = False
    supports_provider_state: bool = True


@dataclass(slots=True, frozen=True)
class RealtimeCapabilityProfile:
    """Detailed realtime capability metadata for a provider and optional model."""

    provider: str
    model: str | None = None
    summary: RealtimeCapabilities = field(default_factory=RealtimeCapabilities)
    transports: dict[str, CapabilityDetail] = field(default_factory=dict)
    input_modalities: dict[str, CapabilityDetail] = field(default_factory=dict)
    output_modalities: dict[str, CapabilityDetail] = field(default_factory=dict)
    audio_formats: dict[str, CapabilityDetail] = field(default_factory=dict)
    controls: dict[str, CapabilityDetail] = field(default_factory=dict)
    tools: dict[str, CapabilityDetail] = field(default_factory=dict)
    state_modes: dict[str, CapabilityDetail] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


def derive_realtime_profile(
    *,
    provider_id: str,
    model: str | None,
    summary: RealtimeCapabilities,
) -> RealtimeCapabilityProfile:
    """Build a detailed realtime profile from summarized capability flags."""

    return RealtimeCapabilityProfile(
        provider=provider_id,
        model=model,
        summary=summary,
        transports={
            transport: CapabilityDetail(status="supported", source="derived")
            for transport in summary.supported_transports
        },
        input_modalities={
            modality: CapabilityDetail(status="supported", source="derived")
            for modality in summary.input_modalities
        },
        output_modalities={
            modality: CapabilityDetail(status="supported", source="derived")
            for modality in summary.output_modalities
        },
        audio_formats={
            f"input:{format_name}": CapabilityDetail(status="supported", source="derived")
            for format_name in summary.audio_input_formats
        }
        | {
            f"output:{format_name}": CapabilityDetail(status="supported", source="derived")
            for format_name in summary.audio_output_formats
        },
        controls={
            "vad": CapabilityDetail(
                status="supported" if summary.supports_vad else "unsupported",
                source="derived",
            ),
            "manual_response": CapabilityDetail(
                status="supported" if summary.supports_manual_response else "unsupported",
                source="derived",
            ),
            "interruption": CapabilityDetail(
                status="supported" if summary.supports_interruption else "unsupported",
                source="derived",
            ),
            "truncation": CapabilityDetail(
                status="supported" if summary.supports_truncation else "unsupported",
                source="derived",
            ),
        },
        tools={
            "function": CapabilityDetail(
                status="supported" if summary.supports_function_tools else "unsupported",
                source="derived",
            ),
            "hosted": CapabilityDetail(
                status="supported" if summary.supports_hosted_tools else "unsupported",
                source="derived",
            ),
            "remote_mcp": CapabilityDetail(
                status="supported" if summary.supports_remote_mcp else "unsupported",
                source="derived",
            ),
        },
        state_modes={
            "provider_state": CapabilityDetail(
                status="supported" if summary.supports_provider_state else "unsupported",
                source="derived",
            ),
            "session_resume": CapabilityDetail(
                status="supported" if summary.supports_session_resume else "unsupported",
                source="derived",
            ),
            "client_tokens": CapabilityDetail(
                status="supported" if summary.supports_client_tokens else "unsupported",
                source="derived",
            ),
        },
    )
