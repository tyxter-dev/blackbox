from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass, field, replace
from uuid import uuid4

from agent_runtime.core.content import AudioPart, ImagePart
from agent_runtime.core.events import AgentEvent, EventTypes
from agent_runtime.core.sessions import InvocationRef
from agent_runtime.realtime.capabilities import (
    RealtimeCapabilities,
    RealtimeCapabilityProfile,
    derive_realtime_profile,
)
from agent_runtime.realtime.provider import (
    RealtimeClientCommand,
    RealtimeConnectRequest,
    RealtimeSessionConfig,
    RealtimeSessionRef,
)


@dataclass(slots=True)
class _FakeSession:
    ref: RealtimeSessionRef
    queue: asyncio.Queue[AgentEvent | None] = field(default_factory=asyncio.Queue)
    closed: bool = False
    response_count: int = 0
    audio_chunks: int = 0
    image_count: int = 0


class FakeRealtimeProvider:
    """Deterministic offline realtime provider for runtime tests."""

    provider_aliases = ("fake",)

    def __init__(
        self,
        *,
        provider_id: str = "fake-realtime",
        scripted_events: Iterable[AgentEvent] | None = None,
        tool_name: str | None = None,
    ) -> None:
        self._provider_id = provider_id
        self._scripted_events = list(scripted_events or [])
        self._tool_name = tool_name
        self._sessions: dict[str, _FakeSession] = {}
        self.sent_commands: list[RealtimeClientCommand] = []

    @property
    def provider_id(self) -> str:
        return self._provider_id

    def capabilities(self, model: str | None = None) -> RealtimeCapabilities:
        return RealtimeCapabilities(
            supported_transports=("websocket", "webrtc"),
            input_modalities=("text", "audio", "image"),
            output_modalities=("text", "audio"),
            audio_input_formats=("pcm16", "pcm"),
            audio_output_formats=("pcm16", "pcm"),
            supports_vad=True,
            supports_manual_response=True,
            supports_interruption=True,
            supports_truncation=True,
            supports_input_transcription=True,
            supports_output_transcription=True,
            supports_function_tools=True,
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
        session_id = f"rt_fake_{uuid4().hex}"
        ref = RealtimeSessionRef(
            provider=self.provider_id,
            id=session_id,
            model=request.model,
            transport=request.transport,
            provider_session_id=f"provider_{session_id}",
            provider_state=request.provider_state,
            metadata={"fake": True, **request.metadata},
        )
        session = _FakeSession(ref=ref)
        self._sessions[ref.id] = session
        await session.queue.put(
            AgentEvent(
                type=EventTypes.REALTIME_SESSION_CONNECTED,
                provider=self.provider_id,
                session_id=ref.id,
                data={
                    "provider_session_id": ref.provider_session_id,
                    "transport": request.transport,
                    "model": request.model,
                },
                raw={"type": "fake.session.connected"},
            )
        )
        return ref

    async def stream_events(
        self,
        session: RealtimeSessionRef,
        *,
        after_event_id: str | None = None,
    ) -> AsyncIterator[AgentEvent]:
        fake_session = self._session(session)
        while True:
            event = await fake_session.queue.get()
            if event is None:
                return
            yield event

    async def send(
        self,
        session: RealtimeSessionRef,
        command: RealtimeClientCommand,
    ) -> InvocationRef:
        """Record a command, enqueue deterministic fake events, and return its invocation."""

        fake_session = self._session(session)
        self.sent_commands.append(command)
        invocation = InvocationRef(
            provider=self.provider_id,
            session_id=session.id,
            id=f"inv_{uuid4().hex}",
            metadata={"command_type": command.type},
        )
        if command.type == "input_text":
            await self._handle_text(fake_session, command)
        elif command.type == "content":
            await self._handle_content(fake_session, command)
        elif command.type == "input_audio":
            await self._handle_audio(fake_session, command)
        elif command.type == "commit_audio":
            await fake_session.queue.put(
                AgentEvent(
                    type=EventTypes.REALTIME_INPUT_AUDIO_COMMITTED,
                    provider=self.provider_id,
                    session_id=session.id,
                    data={"item_id": f"audio_{fake_session.audio_chunks}"},
                )
            )
        elif command.type == "create_response":
            await self._emit_default_response(fake_session, text="ok")
        elif command.type == "cancel_response":
            await fake_session.queue.put(
                AgentEvent(
                    type=EventTypes.REALTIME_RESPONSE_CANCELLED,
                    provider=self.provider_id,
                    session_id=session.id,
                    data={
                        "response_id": command.data.get("response_id"),
                        "reason": "client_cancelled",
                    },
                )
            )
        elif command.type == "truncate_output":
            await fake_session.queue.put(
                AgentEvent(
                    type=EventTypes.REALTIME_OUTPUT_TRUNCATED,
                    provider=self.provider_id,
                    session_id=session.id,
                    item_id=str(command.data.get("item_id") or ""),
                    data=dict(command.data),
                )
            )
        elif command.type == "input_image":
            await self._handle_image(fake_session, command)
        elif command.type == "tool_result":
            await self._emit_default_response(
                fake_session,
                text=f"tool:{command.data.get('output', '')}",
            )
        return invocation

    async def update_session(
        self,
        session: RealtimeSessionRef,
        config: RealtimeSessionConfig,
    ) -> None:
        fake_session = self._session(session)
        await fake_session.queue.put(
            AgentEvent(
                type=EventTypes.REALTIME_SESSION_UPDATED,
                provider=self.provider_id,
                session_id=session.id,
                data={"config_delta": {"metadata": dict(config.metadata)}},
            )
        )

    async def close(self, session: RealtimeSessionRef) -> None:
        fake_session = self._session(session)
        if fake_session.closed:
            return
        fake_session.closed = True
        await fake_session.queue.put(
            AgentEvent(
                type=EventTypes.REALTIME_SESSION_CLOSED,
                provider=self.provider_id,
                session_id=session.id,
                data={"reason": "client_closed"},
            )
        )
        await fake_session.queue.put(None)

    async def _handle_text(
        self,
        session: _FakeSession,
        command: RealtimeClientCommand,
    ) -> None:
        text = str(command.data.get("text") or "")
        item_id = f"msg_{uuid4().hex}"
        await session.queue.put(
            AgentEvent(
                type=EventTypes.REALTIME_INPUT_TEXT_SUBMITTED,
                provider=self.provider_id,
                session_id=session.ref.id,
                item_id=item_id,
                data={"item_id": item_id, "text": text, "role": "user"},
            )
        )
        if self._tool_name is not None:
            await session.queue.put(
                AgentEvent(
                    type=EventTypes.TOOL_CALL_REQUESTED,
                    provider=self.provider_id,
                    session_id=session.ref.id,
                    item_id="call_fake",
                    data={
                        "call_id": "call_fake",
                        "name": self._tool_name,
                        "arguments": {"text": text},
                    },
                )
            )
            return
        await self._emit_script_or_default(session)

    async def _handle_content(
        self,
        session: _FakeSession,
        command: RealtimeClientCommand,
    ) -> None:
        content = command.content
        modalities = [getattr(part, "type", "raw") for part in content.parts] if content else []
        item_id = content.item_id if content is not None and content.item_id else f"item_{uuid4().hex}"
        await session.queue.put(
            AgentEvent(
                type=EventTypes.REALTIME_INPUT_ITEM_CREATED,
                provider=self.provider_id,
                session_id=session.ref.id,
                item_id=item_id,
                data={
                    "item_id": item_id,
                    "role": content.role if content is not None else "user",
                    "modalities": modalities,
                    "content": content,
                },
            )
        )
        await self._emit_script_or_default(session)

    async def _handle_audio(
        self,
        session: _FakeSession,
        command: RealtimeClientCommand,
    ) -> None:
        session.audio_chunks += 1
        audio = command.data.get("audio")
        media = audio.media if isinstance(audio, AudioPart) else None
        await session.queue.put(
            AgentEvent(
                type=EventTypes.REALTIME_INPUT_AUDIO_DELTA,
                provider=self.provider_id,
                session_id=session.ref.id,
                data={
                    "media": media,
                    "chunk_index": session.audio_chunks - 1,
                    "format": getattr(audio, "format", None),
                },
            )
        )

    async def _handle_image(
        self,
        session: _FakeSession,
        command: RealtimeClientCommand,
    ) -> None:
        session.image_count += 1
        image = command.data.get("image")
        media = image.media if isinstance(image, ImagePart) else None
        item_id = f"image_{session.image_count}"
        await session.queue.put(
            AgentEvent(
                type=EventTypes.REALTIME_INPUT_IMAGE_ADDED,
                provider=self.provider_id,
                session_id=session.ref.id,
                item_id=item_id,
                data={"item_id": item_id, "media": media},
            )
        )
        if command.data.get("trigger_response", True):
            await self._emit_script_or_default(session)

    async def _emit_script_or_default(self, session: _FakeSession) -> None:
        if self._scripted_events:
            for event in self._scripted_events:
                await session.queue.put(
                    replace(
                        event,
                        provider=event.provider or self.provider_id,
                        session_id=event.session_id or session.ref.id,
                    )
                )
            return
        await self._emit_default_response(session, text="ok")

    async def _emit_default_response(self, session: _FakeSession, *, text: str) -> None:
        session.response_count += 1
        response_id = f"resp_{session.response_count}"
        item_id = f"out_{session.response_count}"
        for event in (
            AgentEvent(
                type=EventTypes.REALTIME_RESPONSE_CREATED,
                provider=self.provider_id,
                session_id=session.ref.id,
                data={"response_id": response_id},
            ),
            AgentEvent(
                type=EventTypes.REALTIME_OUTPUT_ITEM_CREATED,
                provider=self.provider_id,
                session_id=session.ref.id,
                item_id=item_id,
                data={
                    "response_id": response_id,
                    "item_id": item_id,
                    "item_type": "message",
                },
            ),
            AgentEvent(
                type=EventTypes.REALTIME_OUTPUT_TEXT_DELTA,
                provider=self.provider_id,
                session_id=session.ref.id,
                item_id=item_id,
                data={"response_id": response_id, "item_id": item_id, "delta": text},
            ),
            AgentEvent(
                type=EventTypes.REALTIME_OUTPUT_TEXT_DONE,
                provider=self.provider_id,
                session_id=session.ref.id,
                item_id=item_id,
                data={"response_id": response_id, "item_id": item_id, "text": text},
            ),
            AgentEvent(
                type=EventTypes.REALTIME_RESPONSE_COMPLETED,
                provider=self.provider_id,
                session_id=session.ref.id,
                data={"response_id": response_id, "status": "completed"},
            ),
        ):
            await session.queue.put(event)

    def _session(self, session: RealtimeSessionRef) -> _FakeSession:
        return self._sessions[session.id]
