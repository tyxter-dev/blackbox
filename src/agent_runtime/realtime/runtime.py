from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field, replace
from typing import Any, cast
from uuid import uuid4

from agent_runtime.core.approvals import ApprovalDecision
from agent_runtime.core.content import (
    AudioPart,
    ContentItem,
    ContentPart,
    ImagePart,
    TextPart,
    ToolResultPart,
)
from agent_runtime.core.errors import (
    ApprovalError,
    RealtimeMediaError,
    RealtimeUnsupportedFeatureError,
)
from agent_runtime.core.events import AgentEvent, EventTypes
from agent_runtime.core.media import MediaRef
from agent_runtime.core.policy import (
    ApprovalCallbackPolicy,
    Policy,
    PolicyDecision,
    PolicyRequest,
)
from agent_runtime.core.realtime import ToolMode, TransportKind
from agent_runtime.core.sessions import InvocationRef
from agent_runtime.core.state import ProviderState
from agent_runtime.core.stores import EventStore
from agent_runtime.observability.traces import TraceContext
from agent_runtime.providers.registry import ProviderRef, ProviderRegistry
from agent_runtime.realtime.capabilities import RealtimeCapabilityProfile
from agent_runtime.realtime.provider import (
    AudioConfig,
    RealtimeClientCommand,
    RealtimeConnectRequest,
    RealtimeProvider,
    RealtimeSessionConfig,
    RealtimeSessionRef,
    TurnDetectionConfig,
)
from agent_runtime.runtime.config import RuntimeConfig, workflow_policy
from agent_runtime.tools.hosted.specs import HostedToolSpec
from agent_runtime.tools.registry import ToolDefinition, ToolRegistry
from agent_runtime.tools.runtime import ToolRuntime

_MAX_AUDIO_CHUNK_BYTES = 15 * 1024 * 1024


@dataclass(slots=True)
class RealtimeRuntime:
    """Coordinates realtime sessions, validation, event storage, and local tools."""

    registry: ProviderRegistry
    event_store: EventStore
    tool_registry: ToolRegistry
    _active_sessions: dict[str, ManagedRealtimeSession] = field(default_factory=dict)

    def capabilities(
        self,
        provider: str,
        *,
        model: str | None = None,
    ) -> RealtimeCapabilityProfile:
        provider_ref = ProviderRef.parse(provider)
        model_name = model or provider_ref.resource
        adapter = self.registry.get_realtime(provider_ref.provider_key)
        return adapter.capability_profile(model_name)

    async def connect(
        self,
        *,
        provider: str | None = None,
        model: str | None = None,
        runtime_config: RuntimeConfig | None = None,
        config: RealtimeSessionConfig | None = None,
        transport: TransportKind = "websocket",
        tools: list[str | ToolDefinition] | None = None,
        hosted_tools: list[HostedToolSpec] | None = None,
        tool_mode: ToolMode = "manual",
        tool_execution_context: dict[str, Any] | None = None,
        approval_policy: Any | None = None,
        policy: Policy | None = None,
        run_id: str | None = None,
        provider_state: ProviderState | None = None,
        **kwargs: object,
    ) -> ManagedRealtimeSession:
        """Open a managed realtime session after resolving tools and capabilities."""

        config_values = (
            runtime_config.to_kwargs(surface="realtime")
            if runtime_config is not None
            else {}
        )
        provider = cast(
            str | None,
            _consume_config_value(config_values, "provider", provider),
        )
        model = cast(str | None, _consume_config_value(config_values, "model", model))
        config = _coerce_realtime_session_config(
            _consume_config_value(
                config_values,
                "realtime_session",
                _consume_config_value(config_values, "config", config),
            )
        )
        transport = cast(
            TransportKind,
            _consume_config_value(config_values, "transport", transport),
        )
        tools = cast(
            list[str | ToolDefinition] | None,
            _consume_config_value(config_values, "tools", tools),
        )
        hosted_tools = cast(
            list[HostedToolSpec] | None,
            _consume_config_value(config_values, "hosted_tools", hosted_tools),
        )
        tool_mode = cast(
            ToolMode,
            _consume_config_value(config_values, "tool_mode", tool_mode),
        )
        tool_execution_context = cast(
            dict[str, Any] | None,
            _consume_config_value(
                config_values, "tool_execution_context", tool_execution_context
            ),
        )
        approval_policy = _consume_config_value(
            config_values, "approval_policy", approval_policy
        )
        policy = workflow_policy(_consume_config_value(config_values, "policy", policy))
        if isinstance(approval_policy, str):
            if policy is None:
                policy = workflow_policy(approval_policy)
            approval_policy = None
        run_id = cast(str | None, _consume_config_value(config_values, "run_id", run_id))
        provider_state = cast(
            ProviderState | None,
            _consume_config_value(config_values, "provider_state", provider_state),
        )
        kwargs = {**config_values, **dict(kwargs)}
        if provider is None:
            raise ValueError("provider must be provided explicitly or through runtime_config.")
        provider_ref = ProviderRef.parse(provider)
        model_name = model or provider_ref.resource
        if not model_name:
            raise ValueError("model must be provided explicitly or as 'provider/model'.")
        adapter = self.registry.get_realtime(provider_ref.provider_key)
        session_registry, tool_definitions = _resolve_realtime_tools(
            self.tool_registry,
            tools or [],
        )
        if tool_mode == "disabled" and tool_definitions:
            raise RealtimeUnsupportedFeatureError(
                "tool_mode='disabled' cannot be used with local realtime tools."
            )
        request = RealtimeConnectRequest(
            model=model_name,
            config=config or RealtimeSessionConfig(),
            transport=transport,
            tools=tool_definitions,
            hosted_tools=list(hosted_tools or []),
            tool_mode=tool_mode,
            provider_state=provider_state,
            run_id=run_id,
            extra=dict(kwargs),
        )
        _validate_realtime_request(adapter, request)
        ref = await adapter.connect(request)
        session = ManagedRealtimeSession(
            provider=adapter,
            ref=ref,
            config=request.config,
            event_store=self.event_store,
            run_id=run_id or f"run_{uuid4().hex}",
            tool_mode=tool_mode,
            tool_runtime=ToolRuntime(
                session_registry,
                context=tool_execution_context or {},
            )
            if tool_mode == "auto" and tool_definitions
            else None,
            policy=policy,
            approval_policy=approval_policy,
            on_close=self._forget_session,
        )
        self._active_sessions[ref.id] = session
        return session

    async def approve(self, approval_id: str, decision: ApprovalDecision) -> None:
        for session in list(self._active_sessions.values()):
            if approval_id in session._approvals:
                await session.approve(approval_id, decision)
                return
        raise ApprovalError(f"No pending approval with id '{approval_id}'.")

    def _forget_session(self, session_id: str) -> None:
        self._active_sessions.pop(session_id, None)


@dataclass(slots=True)
class ManagedRealtimeSession:
    """Handle for commands, events, and auto tool calls in a realtime session."""

    provider: RealtimeProvider
    ref: RealtimeSessionRef
    config: RealtimeSessionConfig
    event_store: EventStore
    run_id: str
    tool_mode: ToolMode = "manual"
    tool_runtime: ToolRuntime | None = None
    policy: Policy | None = None
    approval_policy: Any | None = None
    on_close: Any | None = None
    _trace_context: TraceContext = field(init=False)
    _sequence: int = 0
    _closed: bool = False
    _approvals: dict[str, asyncio.Future[ApprovalDecision]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self._trace_context = TraceContext.for_run(self.run_id)

    async def __aenter__(self) -> ManagedRealtimeSession:
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        await self.close()

    def events(self) -> AsyncIterator[AgentEvent]:
        return self._events()

    async def _events(self) -> AsyncIterator[AgentEvent]:
        provider_events = self.provider.stream_events(self.ref)
        try:
            async for event in provider_events:
                stamped = await self._stamp_and_store(event)
                yield stamped
                if self.tool_mode == "auto" and stamped.type == EventTypes.TOOL_CALL_REQUESTED:
                    async for tool_event in self._execute_tool_call(stamped):
                        yield tool_event
        finally:
            close = getattr(provider_events, "aclose", None)
            if callable(close):
                await close()

    async def send_text(
        self,
        text: str,
        *,
        trigger_response: bool = True,
    ) -> InvocationRef:
        self._require_input_modality("text")
        item = ContentItem(role="user", parts=[TextPart(text=text)])
        return await self.provider.send(
            self.ref,
            RealtimeClientCommand(
                type="input_text",
                data={"text": text, "trigger_response": trigger_response},
                content=item,
            ),
        )

    async def send_content(
        self,
        parts: list[ContentPart],
        *,
        role: str = "user",
        trigger_response: bool = True,
    ) -> InvocationRef:
        for part in parts:
            self._require_part_modality(part)
        item = ContentItem(
            role=cast(Any, role),
            parts=parts,
        )
        return await self.provider.send(
            self.ref,
            RealtimeClientCommand(
                type="content",
                data={"trigger_response": trigger_response},
                content=item,
            ),
        )

    async def send_audio(self, chunk: bytes | str | AudioPart) -> InvocationRef:
        self._require_input_modality("audio")
        audio = self._coerce_audio_part(chunk)
        return await self.provider.send(
            self.ref,
            RealtimeClientCommand(
                type="input_audio",
                data={"audio": audio},
                content=ContentItem(role="user", parts=[audio]),
            ),
        )

    async def commit_audio(self) -> InvocationRef:
        self._require_input_modality("audio")
        return await self.provider.send(self.ref, RealtimeClientCommand(type="commit_audio"))

    async def create_response(self, **kwargs: object) -> InvocationRef:
        if not self.provider.capabilities(self.ref.model).supports_manual_response:
            raise RealtimeUnsupportedFeatureError(
                f"Realtime provider '{self.ref.provider}' does not support manual responses."
            )
        return await self.provider.send(
            self.ref,
            RealtimeClientCommand(type="create_response", data=dict(kwargs)),
        )

    async def cancel_response(self, response_id: str | None = None) -> InvocationRef:
        if not self.provider.capabilities(self.ref.model).supports_interruption:
            raise RealtimeUnsupportedFeatureError(
                f"Realtime provider '{self.ref.provider}' does not support response cancellation."
            )
        return await self.provider.send(
            self.ref,
            RealtimeClientCommand(
                type="cancel_response",
                data={"response_id": response_id},
            ),
        )

    async def truncate_output(
        self,
        *,
        item_id: str,
        content_index: int = 0,
        audio_end_ms: int,
    ) -> InvocationRef:
        if not self.provider.capabilities(self.ref.model).supports_truncation:
            raise RealtimeUnsupportedFeatureError(
                f"Realtime provider '{self.ref.provider}' does not support truncation."
            )
        return await self.provider.send(
            self.ref,
            RealtimeClientCommand(
                type="truncate_output",
                data={
                    "item_id": item_id,
                    "content_index": content_index,
                    "audio_end_ms": audio_end_ms,
                },
            ),
        )

    async def interrupt(
        self,
        *,
        response_id: str | None = None,
        item_id: str,
        audio_end_ms: int,
        content_index: int = 0,
    ) -> None:
        await self.cancel_response(response_id)
        await self.truncate_output(
            item_id=item_id,
            content_index=content_index,
            audio_end_ms=audio_end_ms,
        )

    async def add_image(
        self,
        image: ImagePart,
        *,
        trigger_response: bool = True,
    ) -> InvocationRef:
        self._require_input_modality("image")
        return await self.provider.send(
            self.ref,
            RealtimeClientCommand(
                type="input_image",
                data={"image": image, "trigger_response": trigger_response},
                content=ContentItem(role="user", parts=[image]),
            ),
        )

    async def send_tool_result(
        self,
        *,
        call_id: str,
        output: str,
        trigger_response: bool = True,
    ) -> InvocationRef:
        return await self.provider.send(
            self.ref,
            RealtimeClientCommand(
                type="tool_result",
                data={
                    "call_id": call_id,
                    "output": output,
                    "trigger_response": trigger_response,
                },
                content=ContentItem(
                    role="tool",
                    parts=[ToolResultPart(call_id=call_id, content=output)],
                ),
            ),
        )

    async def update(self, config: RealtimeSessionConfig) -> None:
        _validate_realtime_config(self.provider, self.ref.model, self.ref.transport, config)
        await self.provider.update_session(self.ref, config)
        self.config = config

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self.provider.close(self.ref)
        if self.on_close is not None:
            self.on_close(self.ref.id)

    async def approve(self, approval_id: str, decision: ApprovalDecision) -> None:
        future = self._approvals.pop(approval_id, None)
        if future is None or future.done():
            raise ApprovalError(f"No pending approval with id '{approval_id}'.")
        future.set_result(decision)

    async def _execute_tool_call(self, event: AgentEvent) -> AsyncIterator[AgentEvent]:
        """Run a provider-requested tool call through policy, approval, and runtime handling."""

        call_id = _event_string(event, "call_id") or event.item_id or f"call_{uuid4().hex}"
        name = _event_string(event, "name")
        if not name:
            yield await self._stamp_and_store(
                AgentEvent(
                    type=EventTypes.TOOL_CALL_FAILED,
                    provider=self.ref.provider,
                    session_id=self.ref.id,
                    item_id=call_id,
                    data={"call_id": call_id, "error": "missing_tool_name"},
                )
            )
            return
        arguments_raw = event.data.get("arguments")
        arguments = dict(arguments_raw) if isinstance(arguments_raw, dict) else {}
        policy_decision = await self._check_tool_policy(name, arguments)
        if policy_decision.verdict == "deny":
            async for denied_event in self._emit_denied_tool(
                call_id,
                name,
                "denied_by_policy",
                policy_decision.reason,
            ):
                yield denied_event
            return
        if policy_decision.verdict == "require_approval":
            approval_id = f"approval_{call_id}"
            loop = asyncio.get_running_loop()
            future: asyncio.Future[ApprovalDecision] = loop.create_future()
            self._approvals[approval_id] = future
            yield await self._stamp_and_store(
                AgentEvent(
                    type=EventTypes.APPROVAL_REQUESTED,
                    provider=self.ref.provider,
                    session_id=self.ref.id,
                    item_id=approval_id,
                    data={
                        "approval_id": approval_id,
                        "action": name,
                        "arguments": arguments,
                    },
                )
            )
            decision = await future
            yield await self._stamp_and_store(
                AgentEvent(
                    type=EventTypes.APPROVAL_APPROVED
                    if decision.approved
                    else EventTypes.APPROVAL_DENIED,
                    provider=self.ref.provider,
                    session_id=self.ref.id,
                    item_id=approval_id,
                    data={"approval_id": approval_id, "reason": decision.reason},
                )
            )
            if not decision.approved:
                async for denied_event in self._emit_denied_tool(
                    call_id,
                    name,
                    "denied_by_approval",
                    decision.reason,
                ):
                    yield denied_event
                return
        if self.tool_runtime is None:
            yield await self._stamp_and_store(
                AgentEvent(
                    type=EventTypes.TOOL_CALL_FAILED,
                    provider=self.ref.provider,
                    session_id=self.ref.id,
                    item_id=call_id,
                    data={
                        "call_id": call_id,
                        "name": name,
                        "error": "no_tool_runtime",
                    },
                )
            )
            return
        yield await self._stamp_and_store(
            AgentEvent(
                type=EventTypes.TOOL_CALL_STARTED,
                provider=self.ref.provider,
                session_id=self.ref.id,
                item_id=call_id,
                data={"call_id": call_id, "name": name},
            )
        )
        try:
            result = await self.tool_runtime.call(name, arguments)
        except Exception as exc:
            yield await self._stamp_and_store(
                AgentEvent(
                    type=EventTypes.TOOL_CALL_FAILED,
                    provider=self.ref.provider,
                    session_id=self.ref.id,
                    item_id=call_id,
                    data={"call_id": call_id, "name": name, "error": str(exc)},
                )
            )
            await self.send_tool_result(
                call_id=call_id,
                output=json.dumps({"error": str(exc)}),
            )
            return
        data: dict[str, Any] = {
            "call_id": call_id,
            "name": name,
            "content": result.content,
        }
        if result.payload is not None:
            data["payload"] = result.payload
        if result.metadata:
            data["metadata"] = dict(result.metadata)
        yield await self._stamp_and_store(
            AgentEvent(
                type=EventTypes.TOOL_CALL_COMPLETED,
                provider=self.ref.provider,
                session_id=self.ref.id,
                item_id=call_id,
                data=data,
            )
        )
        await self.send_tool_result(call_id=call_id, output=result.content)

    async def _emit_denied_tool(
        self,
        call_id: str,
        name: str,
        error: str,
        reason: str | None,
    ) -> AsyncIterator[AgentEvent]:
        data = {"call_id": call_id, "name": name, "error": error, "reason": reason}
        yield await self._stamp_and_store(
            AgentEvent(
                type=EventTypes.TOOL_CALL_FAILED,
                provider=self.ref.provider,
                session_id=self.ref.id,
                item_id=call_id,
                data=data,
            )
        )
        await self.send_tool_result(call_id=call_id, output=json.dumps(data))

    async def _check_tool_policy(
        self,
        name: str,
        arguments: dict[str, Any],
    ) -> PolicyDecision:
        policy = self.policy
        if policy is None and self.approval_policy is not None:
            policy = ApprovalCallbackPolicy(self.approval_policy)
        if policy is None:
            return PolicyDecision.allow()
        return await policy.check(
            PolicyRequest(
                checkpoint="before_tool_call",
                action=name,
                arguments=arguments,
            )
        )

    async def _stamp_and_store(self, event: AgentEvent) -> AgentEvent:
        enriched_data = dict(event.data)
        enriched_data.setdefault("provider_session_id", self.ref.provider_session_id)
        if self.ref.model is not None:
            enriched_data.setdefault("model", self.ref.model)
        normalized = replace(
            event,
            provider=event.provider or self.ref.provider,
            session_id=event.session_id or self.ref.id,
            data=enriched_data,
        )
        stamped = self._trace_context.stamp(
            normalized,
            run_id=self.run_id,
            sequence=self._sequence,
            preserve_sequence=False,
        )
        self._sequence += 1
        await self.event_store.append(stamped)
        return stamped

    def _require_part_modality(self, part: ContentPart) -> None:
        if isinstance(part, TextPart):
            self._require_input_modality("text")
        elif isinstance(part, AudioPart):
            self._require_input_modality("audio")
        elif isinstance(part, ImagePart):
            self._require_input_modality("image")
        else:
            return

    def _require_input_modality(self, modality: str) -> None:
        if modality not in self.provider.capabilities(self.ref.model).input_modalities:
            raise RealtimeUnsupportedFeatureError(
                f"Realtime provider '{self.ref.provider}' does not support input modality "
                f"'{modality}'."
            )

    def _coerce_audio_part(self, chunk: bytes | str | AudioPart) -> AudioPart:
        if isinstance(chunk, AudioPart):
            bytes_count = chunk.media.bytes_count if chunk.media is not None else None
            if bytes_count is not None and bytes_count > _MAX_AUDIO_CHUNK_BYTES:
                raise RealtimeMediaError("Audio chunk exceeds 15 MB.")
            return chunk
        if isinstance(chunk, bytes):
            if len(chunk) > _MAX_AUDIO_CHUNK_BYTES:
                raise RealtimeMediaError("Audio chunk exceeds 15 MB.")
            audio = self.config.audio or AudioConfig()
            return AudioPart.from_bytes(
                chunk,
                mime_type=f"audio/{audio.input_format or 'pcm'}",
                format=audio.input_format,
                sample_rate_hz=audio.input_sample_rate_hz,
                channels=audio.input_channels,
            )
        bytes_count = len(base64.b64decode(chunk, validate=False))
        if bytes_count > _MAX_AUDIO_CHUNK_BYTES:
            raise RealtimeMediaError("Audio chunk exceeds 15 MB.")
        audio = self.config.audio or AudioConfig()
        return AudioPart(
            media=MediaRef.from_base64(
                chunk,
                mime_type=f"audio/{audio.input_format or 'pcm'}",
                bytes_count=bytes_count,
            ),
            format=audio.input_format,
            sample_rate_hz=audio.input_sample_rate_hz,
            channels=audio.input_channels,
        )


def _validate_realtime_request(
    provider: RealtimeProvider,
    request: RealtimeConnectRequest,
) -> None:
    _validate_realtime_config(provider, request.model, request.transport, request.config)
    capabilities = provider.capabilities(request.model)
    if request.tools and not capabilities.supports_function_tools:
        raise RealtimeUnsupportedFeatureError(
            f"Realtime provider '{provider.provider_id}' does not support function tools."
        )
    if request.hosted_tools and not capabilities.supports_hosted_tools:
        raise RealtimeUnsupportedFeatureError(
            f"Realtime provider '{provider.provider_id}' does not support hosted tools."
        )
    if request.tool_mode == "auto" and request.tools and not capabilities.supports_function_tools:
        raise RealtimeUnsupportedFeatureError(
            f"Realtime provider '{provider.provider_id}' does not support auto tool execution."
        )
    if request.provider_state is not None and not capabilities.supports_provider_state:
        raise RealtimeUnsupportedFeatureError(
            f"Realtime provider '{provider.provider_id}' does not support provider state."
        )


def _validate_realtime_config(
    provider: RealtimeProvider,
    model: str | None,
    transport: TransportKind,
    config: RealtimeSessionConfig,
) -> None:
    capabilities = provider.capabilities(model)
    if transport not in capabilities.supported_transports:
        raise RealtimeUnsupportedFeatureError(
            f"Realtime provider '{provider.provider_id}' does not support transport "
            f"'{transport}'."
        )
    unsupported_inputs = sorted(set(config.input_modalities) - set(capabilities.input_modalities))
    if unsupported_inputs:
        raise RealtimeUnsupportedFeatureError(
            f"Realtime provider '{provider.provider_id}' does not support input modalities: "
            f"{', '.join(unsupported_inputs)}."
        )
    unsupported_outputs = sorted(
        set(config.output_modalities) - set(capabilities.output_modalities)
    )
    if unsupported_outputs:
        raise RealtimeUnsupportedFeatureError(
            f"Realtime provider '{provider.provider_id}' does not support output modalities: "
            f"{', '.join(unsupported_outputs)}."
        )
    if config.turn_detection is not None and not capabilities.supports_vad:
        raise RealtimeUnsupportedFeatureError(
            f"Realtime provider '{provider.provider_id}' does not support turn detection."
        )
    if config.turn_detection is None and not capabilities.supports_manual_response:
        raise RealtimeUnsupportedFeatureError(
            f"Realtime provider '{provider.provider_id}' does not support manual turn control."
        )
    if config.audio is not None:
        if (
            config.audio.input_format is not None
            and config.audio.input_format not in capabilities.audio_input_formats
        ):
            raise RealtimeUnsupportedFeatureError(
                f"Realtime provider '{provider.provider_id}' does not support audio input "
                f"format '{config.audio.input_format}'."
            )
        if (
            config.audio.output_format is not None
            and config.audio.output_format not in capabilities.audio_output_formats
        ):
            raise RealtimeUnsupportedFeatureError(
                f"Realtime provider '{provider.provider_id}' does not support audio output "
                f"format '{config.audio.output_format}'."
            )


def _consume_config_value(
    values: dict[str, Any],
    name: str,
    explicit: Any,
) -> Any:
    configured = values.pop(name, None)
    return explicit if explicit is not None else configured


def _coerce_realtime_session_config(value: Any) -> RealtimeSessionConfig | None:
    if value is None or isinstance(value, RealtimeSessionConfig):
        return value
    if isinstance(value, Mapping):
        data = dict(value)
        audio = data.get("audio")
        if isinstance(audio, Mapping):
            data["audio"] = AudioConfig(**dict(audio))
        turn_detection = data.get("turn_detection")
        if isinstance(turn_detection, Mapping):
            data["turn_detection"] = TurnDetectionConfig(**dict(turn_detection))
        return RealtimeSessionConfig(**data)
    raise RealtimeUnsupportedFeatureError(f"Invalid realtime session config: {value!r}.")


def _resolve_realtime_tools(
    base_registry: ToolRegistry,
    tools: list[str | ToolDefinition],
) -> tuple[ToolRegistry, list[ToolDefinition]]:
    registry = base_registry.clone()
    definitions: list[ToolDefinition] = []
    for tool in tools:
        if isinstance(tool, str):
            definition = registry.get(tool)
        else:
            definition = tool
            registry.add(definition)
        definitions.append(definition)
    return registry, definitions


def _event_string(event: AgentEvent, key: str) -> str | None:
    value = event.data.get(key)
    if isinstance(value, str) and value:
        return value
    return None
