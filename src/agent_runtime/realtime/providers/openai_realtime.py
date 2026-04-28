from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Mapping
from typing import Any

from agent_runtime.core.errors import ProviderNotConfiguredError
from agent_runtime.core.events import AgentEvent, EventTypes
from agent_runtime.core.sessions import InvocationRef
from agent_runtime.realtime.capabilities import (
    RealtimeCapabilities,
    RealtimeCapabilityProfile,
    derive_realtime_profile,
)
from agent_runtime.realtime.events import media_ref_from_base64_delta, realtime_event
from agent_runtime.realtime.provider import (
    RealtimeClientCommand,
    RealtimeConnectRequest,
    RealtimeSessionConfig,
    RealtimeSessionRef,
)


class OpenAIRealtimeProvider:
    """Provider adapter shell for OpenAI Realtime capabilities and event mapping."""

    provider_aliases = ("openai",)

    @property
    def provider_id(self) -> str:
        return "openai-realtime"

    def capabilities(self, model: str | None = None) -> RealtimeCapabilities:
        return RealtimeCapabilities(
            supported_transports=("websocket", "webrtc"),
            input_modalities=("text", "audio", "image"),
            output_modalities=("text", "audio"),
            audio_input_formats=("pcm16", "g711_ulaw", "g711_alaw"),
            audio_output_formats=("pcm16", "g711_ulaw", "g711_alaw"),
            supports_vad=True,
            supports_manual_response=True,
            supports_interruption=True,
            supports_truncation=True,
            supports_input_transcription=True,
            supports_output_transcription=True,
            supports_function_tools=True,
            supports_hosted_tools=True,
            supports_remote_mcp=True,
            supports_usage=True,
            supports_session_resume=True,
            supports_client_tokens=True,
        )

    def capability_profile(self, model: str | None = None) -> RealtimeCapabilityProfile:
        return derive_realtime_profile(
            provider_id=self.provider_id,
            model=model,
            summary=self.capabilities(model),
        )

    async def connect(self, request: RealtimeConnectRequest) -> RealtimeSessionRef:
        raise ProviderNotConfiguredError(
            "OpenAIRealtimeProvider live transport is not configured in this offline slice."
        )

    def stream_events(
        self,
        session: RealtimeSessionRef,
        *,
        after_event_id: str | None = None,
    ) -> AsyncIterator[AgentEvent]:
        raise ProviderNotConfiguredError(
            "OpenAIRealtimeProvider live transport is not configured in this offline slice."
        )

    async def send(
        self,
        session: RealtimeSessionRef,
        command: RealtimeClientCommand,
    ) -> InvocationRef:
        raise ProviderNotConfiguredError(
            "OpenAIRealtimeProvider live transport is not configured in this offline slice."
        )

    async def update_session(
        self,
        session: RealtimeSessionRef,
        config: RealtimeSessionConfig,
    ) -> None:
        raise ProviderNotConfiguredError(
            "OpenAIRealtimeProvider live transport is not configured in this offline slice."
        )

    async def close(self, session: RealtimeSessionRef) -> None:
        return None


def map_openai_realtime_event(
    raw_event: Mapping[str, Any],
    *,
    provider: str = "openai-realtime",
    session_id: str | None = None,
) -> list[AgentEvent]:
    """Map an OpenAI Realtime server event into canonical runtime events."""

    provider_event_type = str(raw_event.get("type") or "")
    data_base = _common_data(raw_event, provider_event_type)
    raw_schema = f"openai.realtime.{provider_event_type}" if provider_event_type else None

    def event(
        event_type: str,
        data: Mapping[str, Any],
        item_id: str | None = None,
    ) -> AgentEvent:
        return realtime_event(
            event_type,
            provider=provider,
            session_id=session_id,
            item_id=item_id,
            data={**data_base, **data},
            raw=dict(raw_event),
            raw_schema_name=raw_schema,
        )

    if provider_event_type == "session.created":
        session = _mapping(raw_event.get("session"))
        provider_session_id = _string(session.get("id")) or _string(raw_event.get("session_id"))
        return [
            event(
                EventTypes.REALTIME_SESSION_CONNECTED,
                {
                    "provider_session_id": provider_session_id,
                    "transport": "websocket",
                    "model": session.get("model"),
                },
            )
        ]
    if provider_event_type == "session.updated":
        return [
            event(
                EventTypes.REALTIME_SESSION_UPDATED,
                {"config_delta": dict(_mapping(raw_event.get("session")))},
            )
        ]
    if provider_event_type == "conversation.item.added":
        item = _mapping(raw_event.get("item"))
        item_id = _string(item.get("id")) or _string(raw_event.get("item_id"))
        role = _string(item.get("role")) or "user"
        item_type = _string(item.get("type")) or "message"
        modalities = _modalities_from_openai_item(item)
        canonical = (
            EventTypes.REALTIME_OUTPUT_ITEM_CREATED
            if role == "assistant" or (item_type in {"function_call", "message"}
            and raw_event.get("response_id"))
            else EventTypes.REALTIME_INPUT_ITEM_CREATED
        )
        return [
            event(
                canonical,
                {
                    "item_id": item_id,
                    "role": role,
                    "item_type": item_type,
                    "modalities": modalities,
                    "response_id": raw_event.get("response_id"),
                },
                item_id=item_id,
            )
        ]
    if provider_event_type == "input_audio_buffer.speech_started":
        return [
            event(
                EventTypes.REALTIME_INPUT_SPEECH_STARTED,
                {"audio_start_ms": raw_event.get("audio_start_ms")},
            )
        ]
    if provider_event_type == "input_audio_buffer.speech_stopped":
        return [
            event(
                EventTypes.REALTIME_INPUT_SPEECH_STOPPED,
                {"audio_end_ms": raw_event.get("audio_end_ms")},
            )
        ]
    if provider_event_type == "input_audio_buffer.committed":
        item_id = _string(raw_event.get("item_id"))
        return [
            event(
                EventTypes.REALTIME_INPUT_AUDIO_COMMITTED,
                {"item_id": item_id},
                item_id=item_id,
            )
        ]
    if provider_event_type == "response.created":
        response = _mapping(raw_event.get("response"))
        response_id = _string(response.get("id")) or _string(raw_event.get("response_id"))
        return [event(EventTypes.REALTIME_RESPONSE_CREATED, {"response_id": response_id})]
    if provider_event_type == "response.output_item.created":
        item = _mapping(raw_event.get("item"))
        item_id = _string(item.get("id")) or _string(raw_event.get("item_id"))
        return [
            event(
                EventTypes.REALTIME_OUTPUT_ITEM_CREATED,
                {
                    "response_id": raw_event.get("response_id"),
                    "item_id": item_id,
                    "item_type": item.get("type"),
                },
                item_id=item_id,
            )
        ]
    if provider_event_type == "response.output_text.delta":
        return [_text_event(event, raw_event, EventTypes.REALTIME_OUTPUT_TEXT_DELTA, "delta")]
    if provider_event_type == "response.output_text.done":
        return [_text_event(event, raw_event, EventTypes.REALTIME_OUTPUT_TEXT_DONE, "text")]
    if provider_event_type in {"response.output_audio.delta", "response.audio.delta"}:
        delta = str(raw_event.get("delta") or "")
        media = media_ref_from_base64_delta(
            delta,
            mime_type="audio/pcm",
            metadata={"provider_event_type": provider_event_type},
        )
        return [
            event(
                EventTypes.REALTIME_OUTPUT_AUDIO_DELTA,
                {
                    "response_id": raw_event.get("response_id"),
                    "item_id": raw_event.get("item_id"),
                    "media": media,
                    "chunk_index": raw_event.get("content_index") or raw_event.get("chunk_index"),
                    "bytes_count": media.bytes_count,
                },
                item_id=_string(raw_event.get("item_id")),
            )
        ]
    if provider_event_type == "response.output_audio.done":
        return [
            event(
                EventTypes.REALTIME_OUTPUT_AUDIO_DONE,
                {
                    "response_id": raw_event.get("response_id"),
                    "item_id": raw_event.get("item_id"),
                },
                item_id=_string(raw_event.get("item_id")),
            )
        ]
    if provider_event_type == "response.output_audio_transcript.delta":
        return [
            event(
                EventTypes.REALTIME_OUTPUT_AUDIO_TRANSCRIPT_DELTA,
                {
                    "response_id": raw_event.get("response_id"),
                    "item_id": raw_event.get("item_id"),
                    "delta": raw_event.get("delta"),
                },
                item_id=_string(raw_event.get("item_id")),
            )
        ]
    if provider_event_type == "response.output_audio_transcript.done":
        return [
            event(
                EventTypes.REALTIME_OUTPUT_AUDIO_TRANSCRIPT_DONE,
                {
                    "response_id": raw_event.get("response_id"),
                    "item_id": raw_event.get("item_id"),
                    "text": raw_event.get("transcript") or raw_event.get("text"),
                },
                item_id=_string(raw_event.get("item_id")),
            )
        ]
    if provider_event_type == "response.function_call_arguments.delta":
        return [
            event(
                EventTypes.REALTIME_TOOL_ARGUMENTS_DELTA,
                {
                    "response_id": raw_event.get("response_id"),
                    "item_id": raw_event.get("item_id"),
                    "call_id": raw_event.get("call_id"),
                    "delta": raw_event.get("delta"),
                },
                item_id=_string(raw_event.get("item_id")),
            )
        ]
    if provider_event_type == "response.done":
        response = _mapping(raw_event.get("response"))
        response_id = _string(response.get("id")) or _string(raw_event.get("response_id"))
        events: list[AgentEvent] = []
        for item in _sequence(response.get("output")):
            item_map = _mapping(item)
            if item_map.get("type") != "function_call":
                continue
            call_id = _string(item_map.get("call_id")) or _string(item_map.get("id"))
            events.append(
                event(
                    EventTypes.TOOL_CALL_REQUESTED,
                    {
                        "response_id": response_id,
                        "item_id": item_map.get("id"),
                        "call_id": call_id,
                        "name": item_map.get("name"),
                        "arguments": _parse_arguments(item_map.get("arguments")),
                    },
                    item_id=call_id,
                )
            )
        events.append(
            event(
                EventTypes.REALTIME_RESPONSE_COMPLETED,
                {
                    "response_id": response_id,
                    "status": response.get("status") or raw_event.get("status") or "completed",
                    "usage": response.get("usage"),
                },
            )
        )
        return events
    if provider_event_type == "response.cancelled":
        return [
            event(
                EventTypes.REALTIME_RESPONSE_CANCELLED,
                {
                    "response_id": raw_event.get("response_id"),
                    "reason": raw_event.get("reason"),
                },
            )
        ]
    if provider_event_type == "conversation.item.truncated":
        item_id = _string(raw_event.get("item_id"))
        return [
            event(
                EventTypes.REALTIME_OUTPUT_TRUNCATED,
                {
                    "item_id": item_id,
                    "content_index": raw_event.get("content_index"),
                    "audio_end_ms": raw_event.get("audio_end_ms"),
                },
                item_id=item_id,
            )
        ]
    if provider_event_type == "error":
        error = raw_event.get("error")
        return [
            event(
                EventTypes.REALTIME_SESSION_FAILED,
                {
                    "error": dict(error) if isinstance(error, Mapping) else error,
                    "provider_event_type": provider_event_type,
                },
            )
        ]
    return [
        event(
            EventTypes.REALTIME_SESSION_FAILED,
            {
                "error": "unmapped_openai_realtime_event",
                "provider_event_type": provider_event_type,
            },
        )
    ]


def _text_event(
    event_builder: Callable[..., AgentEvent],
    raw_event: Mapping[str, Any],
    event_type: str,
    text_key: str,
) -> AgentEvent:
    return event_builder(
        event_type,
        {
            "response_id": raw_event.get("response_id"),
            "item_id": raw_event.get("item_id"),
            text_key: raw_event.get(text_key) or raw_event.get("delta"),
        },
        item_id=_string(raw_event.get("item_id")),
    )


def _common_data(raw_event: Mapping[str, Any], provider_event_type: str) -> dict[str, Any]:
    return {
        "provider_event_type": provider_event_type,
        "provider_session_id": raw_event.get("session_id"),
        "response_id": raw_event.get("response_id"),
        "item_id": raw_event.get("item_id"),
        "content_index": raw_event.get("content_index"),
    }


def _modalities_from_openai_item(item: Mapping[str, Any]) -> list[str]:
    modalities: list[str] = []
    for part in _sequence(item.get("content")):
        part_map = _mapping(part)
        part_type = str(part_map.get("type") or "")
        if "text" in part_type:
            modalities.append("text")
        elif "audio" in part_type:
            modalities.append("audio")
        elif "image" in part_type:
            modalities.append("image")
    return modalities


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _sequence(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list | tuple) else []


def _string(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _parse_arguments(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if not isinstance(value, str) or not value:
        return {}
    import json

    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {"raw": value}
    return dict(parsed) if isinstance(parsed, dict) else {"value": parsed}
