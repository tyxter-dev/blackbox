from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from typing import Any

from agent_runtime.core.errors import ProviderNotConfiguredError
from agent_runtime.core.events import AgentEvent, EventTypes
from agent_runtime.core.sessions import InvocationRef
from agent_runtime.core.state import ProviderState
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


class GeminiLiveProvider:
    """Provider adapter shell for Gemini Live capabilities and event mapping."""

    provider_aliases = ("gemini",)

    @property
    def provider_id(self) -> str:
        return "gemini-live"

    def capabilities(self, model: str | None = None) -> RealtimeCapabilities:
        return RealtimeCapabilities(
            supported_transports=("websocket",),
            input_modalities=("text", "audio", "image", "video"),
            output_modalities=("text", "audio"),
            audio_input_formats=("pcm16",),
            audio_output_formats=("pcm16",),
            supports_vad=True,
            supports_manual_response=True,
            supports_interruption=True,
            supports_input_transcription=True,
            supports_output_transcription=True,
            supports_function_tools=True,
            supports_usage=True,
            supports_session_resume=True,
        )

    def capability_profile(self, model: str | None = None) -> RealtimeCapabilityProfile:
        return derive_realtime_profile(
            provider_id=self.provider_id,
            model=model,
            summary=self.capabilities(model),
        )

    async def connect(self, request: RealtimeConnectRequest) -> RealtimeSessionRef:
        raise ProviderNotConfiguredError(
            "GeminiLiveProvider live transport is not configured in this offline slice."
        )

    def stream_events(
        self,
        session: RealtimeSessionRef,
        *,
        after_event_id: str | None = None,
    ) -> AsyncIterator[AgentEvent]:
        raise ProviderNotConfiguredError(
            "GeminiLiveProvider live transport is not configured in this offline slice."
        )

    async def send(
        self,
        session: RealtimeSessionRef,
        command: RealtimeClientCommand,
    ) -> InvocationRef:
        raise ProviderNotConfiguredError(
            "GeminiLiveProvider live transport is not configured in this offline slice."
        )

    async def update_session(
        self,
        session: RealtimeSessionRef,
        config: RealtimeSessionConfig,
    ) -> None:
        raise ProviderNotConfiguredError(
            "GeminiLiveProvider live transport is not configured in this offline slice."
        )

    async def close(self, session: RealtimeSessionRef) -> None:
        return None


def map_gemini_live_message(
    raw_message: Mapping[str, Any],
    *,
    provider: str = "gemini-live",
    session_id: str | None = None,
) -> list[AgentEvent]:
    """Map a Gemini Live server message into canonical runtime events."""

    message_type = _message_type(raw_message)
    raw_schema = f"gemini.live.{message_type}" if message_type else None
    data_base = {"provider_event_type": message_type}

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
            raw=dict(raw_message),
            raw_schema_name=raw_schema,
        )

    if "setupComplete" in raw_message:
        return [
            event(
                EventTypes.REALTIME_SESSION_CONNECTED,
                {"provider_session_id": raw_message.get("sessionId"), "transport": "websocket"},
            )
        ]
    if "serverContent" in raw_message:
        return _map_server_content(event, _mapping(raw_message.get("serverContent")))
    if "toolCall" in raw_message:
        tool_call = _mapping(raw_message.get("toolCall"))
        events: list[AgentEvent] = []
        for function_call in _sequence(tool_call.get("functionCalls")):
            call = _mapping(function_call)
            call_id = _string(call.get("id")) or _string(call.get("callId"))
            events.append(
                event(
                    EventTypes.TOOL_CALL_REQUESTED,
                    {
                        "call_id": call_id,
                        "name": call.get("name"),
                        "arguments": dict(_mapping(call.get("args"))),
                    },
                    item_id=call_id,
                )
            )
        return events
    if "toolCallCancellation" in raw_message:
        cancellation = _mapping(raw_message.get("toolCallCancellation"))
        ids = _sequence(cancellation.get("ids")) or _sequence(cancellation.get("functionIds"))
        return [
            event(
                EventTypes.TOOL_CALL_FAILED,
                {"call_id": str(call_id), "error": "cancelled", "reason": "provider_cancelled"},
                item_id=str(call_id),
            )
            for call_id in ids
        ]
    if "inputTranscription" in raw_message:
        transcription = _mapping(raw_message.get("inputTranscription"))
        text = transcription.get("text")
        done = bool(transcription.get("finished") or transcription.get("final"))
        return [
            event(
                EventTypes.REALTIME_INPUT_TRANSCRIPT_COMPLETED
                if done
                else EventTypes.REALTIME_INPUT_TRANSCRIPT_DELTA,
                {"text": text, "delta": text, "item_id": transcription.get("itemId")},
                item_id=_string(transcription.get("itemId")),
            )
        ]
    if "outputTranscription" in raw_message:
        transcription = _mapping(raw_message.get("outputTranscription"))
        text = transcription.get("text")
        done = bool(transcription.get("finished") or transcription.get("final"))
        return [
            event(
                EventTypes.REALTIME_OUTPUT_AUDIO_TRANSCRIPT_DONE
                if done
                else EventTypes.REALTIME_OUTPUT_AUDIO_TRANSCRIPT_DELTA,
                {"text": text, "delta": text, "item_id": transcription.get("itemId")},
                item_id=_string(transcription.get("itemId")),
            )
        ]
    if "usageMetadata" in raw_message:
        return [
            event(
                EventTypes.REALTIME_USAGE_REPORTED,
                {"usage": dict(_mapping(raw_message.get("usageMetadata")))},
            )
        ]
    if "sessionResumptionUpdate" in raw_message:
        update = dict(_mapping(raw_message.get("sessionResumptionUpdate")))
        return [
            event(
                EventTypes.REALTIME_HISTORY_UPDATED,
                {
                    "operation": "session_resumption_update",
                    "item_ids": [],
                    "provider_state": ProviderState(
                        provider=provider,
                        continuation=update,
                    ),
                },
            )
        ]
    if "goAway" in raw_message:
        go_away = _mapping(raw_message.get("goAway"))
        return [
            event(
                EventTypes.REALTIME_TRANSPORT_RECONNECTING,
                {"attempt": 1, "reason": go_away.get("reason") or "go_away"},
            )
        ]
    return [
        event(
            EventTypes.REALTIME_SESSION_FAILED,
            {"error": "unmapped_gemini_live_message", "provider_event_type": message_type},
        )
    ]


def _map_server_content(event_builder: Any, content: Mapping[str, Any]) -> list[AgentEvent]:
    """Translate Gemini serverContent payloads into output and turn events."""

    events: list[AgentEvent] = []
    if content.get("interrupted") is True:
        events.append(
            event_builder(
                EventTypes.REALTIME_INTERRUPTION_DETECTED,
                {
                    "response_id": content.get("responseId"),
                    "item_id": content.get("itemId"),
                    "audio_end_ms": content.get("audioEndMs"),
                },
                item_id=_string(content.get("itemId")),
            )
        )
        events.append(
            event_builder(
                EventTypes.REALTIME_RESPONSE_CANCELLED,
                {"response_id": content.get("responseId"), "reason": "interrupted"},
            )
        )
        return events
    model_turn = _mapping(content.get("modelTurn"))
    for index, part in enumerate(_sequence(model_turn.get("parts"))):
        part_map = _mapping(part)
        if isinstance(part_map.get("text"), str):
            events.append(
                event_builder(
                    EventTypes.REALTIME_OUTPUT_TEXT_DELTA,
                    {
                        "response_id": content.get("responseId"),
                        "item_id": content.get("itemId"),
                        "delta": part_map.get("text"),
                        "content_index": index,
                    },
                    item_id=_string(content.get("itemId")),
                )
            )
        inline_data = _mapping(part_map.get("inlineData") or part_map.get("inline_data"))
        mime_type = _string(inline_data.get("mimeType")) or _string(inline_data.get("mime_type"))
        data = _string(inline_data.get("data"))
        if data is not None and mime_type is not None and mime_type.startswith("audio/"):
            media = media_ref_from_base64_delta(data, mime_type=mime_type)
            events.append(
                event_builder(
                    EventTypes.REALTIME_OUTPUT_AUDIO_DELTA,
                    {
                        "response_id": content.get("responseId"),
                        "item_id": content.get("itemId"),
                        "media": media,
                        "chunk_index": index,
                        "bytes_count": media.bytes_count,
                    },
                    item_id=_string(content.get("itemId")),
                )
            )
    if content.get("turnComplete") is True:
        turn_id = _string(content.get("turnId")) or "turn"
        events.append(
            event_builder(
                EventTypes.REALTIME_TURN_COMPLETED,
                {"turn_id": turn_id, "reason": "turn_complete"},
            )
        )
        events.append(
            event_builder(
                EventTypes.REALTIME_RESPONSE_COMPLETED,
                {
                    "response_id": content.get("responseId") or turn_id,
                    "status": "completed",
                },
            )
        )
    return events


def _message_type(raw_message: Mapping[str, Any]) -> str:
    for key in (
        "setupComplete",
        "serverContent",
        "toolCall",
        "toolCallCancellation",
        "usageMetadata",
        "goAway",
        "sessionResumptionUpdate",
        "inputTranscription",
        "outputTranscription",
    ):
        if key in raw_message:
            return key
    return ""


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _sequence(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list | tuple) else []


def _string(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None
