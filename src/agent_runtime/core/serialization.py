"""JSON serialization helpers for runtime state.

Provides safe ``to_dict`` / ``from_dict`` round-trips for :class:`AgentEvent`,
:class:`RunItem`, :class:`ProviderState`, and :class:`RunState` so persistent
stores (JSONL, SQLite) can write and re-read them without depending on
arbitrary SDK objects.

Sentinels:

- ``_kind`` discriminates serialized dataclasses on read.
- ``raw`` payloads on events default to dropped on persistence; pass
  ``keep_raw=True`` to preserve them. Bare SDK objects fall through ``repr``;
  :class:`RawEnvelope` values round-trip through their dataclass shape.

Anything that does not match a known kind passes through unchanged on read,
so adapter-specific dicts inside ``data`` survive.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from agent_runtime.core.content import (
    AudioPart,
    ContentItem,
    FilePart,
    ImagePart,
    ProviderNativePart,
    TextPart,
    ToolResultPart,
    VideoFramePart,
)
from agent_runtime.core.events import AgentEvent
from agent_runtime.core.items import RunItem
from agent_runtime.core.media import MediaRef
from agent_runtime.core.raw import RawEnvelope
from agent_runtime.core.state import ProviderState, RunState
from agent_runtime.tools.routing import (
    ResolvedToolPlan,
    SelectedTool,
    ToolBudget,
    ToolCandidate,
    ToolRoutingSpec,
    ToolSelectionResult,
)

_AGENT_EVENT = "AgentEvent"
_RUN_ITEM = "RunItem"
_PROVIDER_STATE = "ProviderState"
_RAW_ENVELOPE = "RawEnvelope"
_RUN_STATE = "RunState"
_MEDIA_REF = "MediaRef"
_CONTENT_ITEM = "ContentItem"
_TEXT_PART = "TextPart"
_AUDIO_PART = "AudioPart"
_IMAGE_PART = "ImagePart"
_VIDEO_FRAME_PART = "VideoFramePart"
_FILE_PART = "FilePart"
_TOOL_RESULT_PART = "ToolResultPart"
_PROVIDER_NATIVE_PART = "ProviderNativePart"
_TOOL_BUDGET = "ToolBudget"
_TOOL_ROUTING_SPEC = "ToolRoutingSpec"
_TOOL_CANDIDATE = "ToolCandidate"
_SELECTED_TOOL = "SelectedTool"
_TOOL_SELECTION_RESULT = "ToolSelectionResult"
_RESOLVED_TOOL_PLAN = "ResolvedToolPlan"


def event_to_dict(
    event: AgentEvent,
    *,
    keep_raw: bool = False,
    keep_media: bool = False,
) -> dict[str, Any]:
    return {
        "_kind": _AGENT_EVENT,
        "id": event.id,
        "type": event.type,
        "run_id": event.run_id,
        "sequence": event.sequence,
        "trace_id": event.trace_id,
        "span_id": event.span_id,
        "parent_span_id": event.parent_span_id,
        "span_kind": event.span_kind,
        "session_id": event.session_id,
        "provider": event.provider,
        "item_id": event.item_id,
        "provider_trace_id": event.provider_trace_id,
        "provider_span_id": event.provider_span_id,
        "provider_request_id": event.provider_request_id,
        "data": _safe_value(event.data, keep_media=keep_media),
        "raw": _safe_value(event.raw, keep_media=keep_media) if keep_raw else None,
        "timestamp": event.timestamp.isoformat(),
    }


def event_from_dict(payload: dict[str, Any]) -> AgentEvent:
    timestamp_raw = payload.get("timestamp")
    if isinstance(timestamp_raw, str):
        timestamp = datetime.fromisoformat(timestamp_raw)
    else:
        timestamp = datetime.now().astimezone()
    return AgentEvent(
        type=str(payload["type"]),
        run_id=payload.get("run_id"),
        sequence=payload.get("sequence"),
        trace_id=payload.get("trace_id"),
        span_id=payload.get("span_id"),
        parent_span_id=payload.get("parent_span_id"),
        span_kind=payload.get("span_kind"),
        session_id=payload.get("session_id"),
        provider=payload.get("provider"),
        item_id=payload.get("item_id"),
        provider_trace_id=payload.get("provider_trace_id"),
        provider_span_id=payload.get("provider_span_id"),
        provider_request_id=payload.get("provider_request_id"),
        data=_hydrate_value(payload.get("data") or {}),
        raw=_hydrate_value(payload.get("raw")),
        id=str(payload.get("id") or ""),
        timestamp=timestamp,
    )


def run_state_to_dict(state: RunState) -> dict[str, Any]:
    return {
        "_kind": _RUN_STATE,
        "session_id": state.session_id,
        "provider": state.provider,
        "model": state.model,
        "provider_state": _safe_value(state.provider_state),
        "items": [_safe_value(item) for item in state.items],
        "metadata": _safe_value(state.metadata),
    }


def run_state_from_dict(payload: dict[str, Any]) -> RunState:
    items_raw = payload.get("items") or []
    items: list[RunItem] = []
    for entry in items_raw:
        hydrated = _hydrate_value(entry)
        if isinstance(hydrated, RunItem):
            items.append(hydrated)
    provider_state_raw = payload.get("provider_state")
    provider_state = (
        _hydrate_value(provider_state_raw)
        if isinstance(provider_state_raw, dict)
        else None
    )
    if not isinstance(provider_state, ProviderState):
        provider_state = None
    metadata_raw = payload.get("metadata") or {}
    metadata = _hydrate_value(metadata_raw)
    if not isinstance(metadata, dict):
        metadata = {}
    return RunState(
        session_id=str(payload.get("session_id") or ""),
        provider=payload.get("provider"),
        model=payload.get("model"),
        provider_state=provider_state,
        items=items,
        metadata=metadata,
    )


def _safe_value(value: Any, *, keep_media: bool = False) -> Any:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, RunItem):
        return {
            "_kind": _RUN_ITEM,
            "type": value.type,
            "provider": value.provider,
            "data": _safe_value(value.data, keep_media=keep_media),
            "status": value.status,
            "id": value.id,
            "parent_id": value.parent_id,
        }
    if isinstance(value, ProviderState):
        return {
            "_kind": _PROVIDER_STATE,
            "provider": value.provider,
            "conversation_id": value.conversation_id,
            "previous_response_id": value.previous_response_id,
            "native_history": _safe_value(value.native_history, keep_media=keep_media),
            "reasoning_state": _safe_value(value.reasoning_state, keep_media=keep_media),
            "tool_state": _safe_value(value.tool_state, keep_media=keep_media),
            "continuation": _safe_value(value.continuation, keep_media=keep_media),
        }
    if isinstance(value, RawEnvelope):
        return {
            "_kind": _RAW_ENVELOPE,
            "provider": value.provider,
            "payload": _safe_value(value.payload, keep_media=keep_media),
            "schema_name": value.schema_name,
            "schema_version": value.schema_version,
            "sensitivity": value.sensitivity,
            "redaction_status": value.redaction_status,
            "storage_allowed": value.storage_allowed,
            "hash": value.hash,
            "metadata": _safe_value(value.metadata, keep_media=keep_media),
        }
    if isinstance(value, MediaRef):
        media = value
        redacted = False
        if value.source in {"inline_base64", "bytes"} and (
            not keep_media or not value.storage_allowed
        ):
            media = value.redacted()
            redacted = True
        payload = {
            "_kind": _MEDIA_REF,
            "source": media.source,
            "mime_type": media.mime_type,
            "data_base64": media.data_base64,
            "url": media.url,
            "artifact_id": media.artifact_id,
            "provider_file_id": media.provider_file_id,
            "bytes_count": media.bytes_count,
            "storage_allowed": media.storage_allowed,
            "sensitivity": media.sensitivity,
            "metadata": _safe_value(media.metadata, keep_media=keep_media),
        }
        if redacted:
            payload["redacted"] = True
        return payload
    if isinstance(value, ContentItem):
        return {
            "_kind": _CONTENT_ITEM,
            "role": value.role,
            "parts": [_safe_value(part, keep_media=keep_media) for part in value.parts],
            "item_id": value.item_id,
            "metadata": _safe_value(value.metadata, keep_media=keep_media),
        }
    if isinstance(value, TextPart):
        return {"_kind": _TEXT_PART, "type": value.type, "text": value.text}
    if isinstance(value, AudioPart):
        return {
            "_kind": _AUDIO_PART,
            "type": value.type,
            "media": _safe_value(value.media, keep_media=keep_media),
            "transcript": value.transcript,
            "format": value.format,
            "sample_rate_hz": value.sample_rate_hz,
            "channels": value.channels,
            "duration_ms": value.duration_ms,
        }
    if isinstance(value, ImagePart):
        return {
            "_kind": _IMAGE_PART,
            "type": value.type,
            "media": _safe_value(value.media, keep_media=keep_media),
            "detail": value.detail,
        }
    if isinstance(value, VideoFramePart):
        return {
            "_kind": _VIDEO_FRAME_PART,
            "type": value.type,
            "media": _safe_value(value.media, keep_media=keep_media),
            "timestamp_ms": value.timestamp_ms,
        }
    if isinstance(value, FilePart):
        return {
            "_kind": _FILE_PART,
            "type": value.type,
            "media": _safe_value(value.media, keep_media=keep_media),
            "filename": value.filename,
        }
    if isinstance(value, ToolResultPart):
        return {
            "_kind": _TOOL_RESULT_PART,
            "type": value.type,
            "call_id": value.call_id,
            "content": value.content,
            "payload": _safe_value(value.payload, keep_media=keep_media),
        }
    if isinstance(value, ProviderNativePart):
        return {
            "_kind": _PROVIDER_NATIVE_PART,
            "type": value.type,
            "provider": value.provider,
            "value": _safe_value(value.value, keep_media=keep_media),
            "metadata": _safe_value(value.metadata, keep_media=keep_media),
        }
    if isinstance(value, ToolBudget):
        return {
            "_kind": _TOOL_BUDGET,
            "max_visible_tools": value.max_visible_tools,
            "max_schema_tokens": value.max_schema_tokens,
            "max_mcp_tools": value.max_mcp_tools,
            "max_agent_tools": value.max_agent_tools,
            "max_handoffs": value.max_handoffs,
        }
    if isinstance(value, ToolRoutingSpec):
        return {
            "_kind": _TOOL_ROUTING_SPEC,
            "mode": value.mode,
            "budget": _safe_value(value.budget, keep_media=keep_media),
            "always_include": list(value.always_include),
            "never_include": list(value.never_include),
            "include_categories": list(value.include_categories),
            "include_tags": list(value.include_tags),
            "exclude_tags": list(value.exclude_tags),
            "allow_late_bind": value.allow_late_bind,
            "allow_model_discovery_tools": value.allow_model_discovery_tools,
            "refresh_each_turn": value.refresh_each_turn,
            "min_score": value.min_score,
            "metadata": _safe_value(value.metadata, keep_media=keep_media),
        }
    if isinstance(value, ToolCandidate):
        return {
            "_kind": _TOOL_CANDIDATE,
            "ref": value.ref,
            "name": value.name,
            "kind": value.kind,
            "description": value.description,
            "parameters": _safe_value(value.parameters, keep_media=keep_media),
            "category": value.category,
            "tags": list(value.tags),
            "source": value.source,
            "provider": value.provider,
            "risk": value.risk,
            "estimated_schema_tokens": value.estimated_schema_tokens,
            "requires_approval": value.requires_approval,
            "metadata": _safe_value(value.metadata, keep_media=keep_media),
        }
    if isinstance(value, SelectedTool):
        return {
            "_kind": _SELECTED_TOOL,
            "candidate": _safe_value(value.candidate, keep_media=keep_media),
            "score": value.score,
            "reason": value.reason,
            "selected_by": value.selected_by,
        }
    if isinstance(value, ToolSelectionResult):
        return {
            "_kind": _TOOL_SELECTION_RESULT,
            "selected": [_safe_value(item, keep_media=keep_media) for item in value.selected],
            "discoverable": [
                _safe_value(item, keep_media=keep_media) for item in value.discoverable
            ],
            "blocked": [_safe_value(item, keep_media=keep_media) for item in value.blocked],
            "metadata": _safe_value(value.metadata, keep_media=keep_media),
        }
    if isinstance(value, ResolvedToolPlan):
        return {
            "_kind": _RESOLVED_TOOL_PLAN,
            "selected_refs": list(value.selected_refs),
            "local_tools": list(value.local_tools),
            "workspace_tools": list(value.workspace_tools),
            "mcp_tools": list(value.mcp_tools),
            "hosted_tools": _safe_value(value.hosted_tools, keep_media=keep_media),
            "agent_tools": list(value.agent_tools),
            "handoffs": list(value.handoffs),
            "provider_tools": _safe_value(value.provider_tools, keep_media=keep_media),
            "prompt_fragments": _safe_value(value.prompt_fragments, keep_media=keep_media),
            "fingerprint": value.fingerprint,
            "metadata": _safe_value(value.metadata, keep_media=keep_media),
        }
    if isinstance(value, dict):
        return {str(k): _safe_value(v, keep_media=keep_media) for k, v in value.items()}
    if isinstance(value, list | tuple | set | frozenset):
        return [_safe_value(item, keep_media=keep_media) for item in value]
    return repr(value)


def _hydrate_value(value: Any) -> Any:
    if isinstance(value, dict):
        kind = value.get("_kind")
        if kind == _RUN_ITEM:
            data_raw = value.get("data") or {}
            data = _hydrate_value(data_raw)
            if not isinstance(data, dict):
                data = {}
            return RunItem(
                type=str(value.get("type", "")),
                provider=str(value.get("provider", "")),
                data=data,
                status=value.get("status"),
                id=str(value.get("id", "")),
                parent_id=value.get("parent_id"),
            )
        if kind == _PROVIDER_STATE:
            return ProviderState(
                provider=str(value.get("provider", "")),
                conversation_id=value.get("conversation_id"),
                previous_response_id=value.get("previous_response_id"),
                native_history=list(value.get("native_history") or []),
                reasoning_state=dict(value.get("reasoning_state") or {}),
                tool_state=dict(value.get("tool_state") or {}),
                continuation=dict(value.get("continuation") or {}),
            )
        if kind == _RAW_ENVELOPE:
            return RawEnvelope(
                provider=str(value.get("provider", "")),
                payload=_hydrate_value(value.get("payload")),
                schema_name=value.get("schema_name"),
                schema_version=value.get("schema_version"),
                sensitivity=value.get("sensitivity", "internal"),
                redaction_status=value.get("redaction_status", "raw"),
                storage_allowed=bool(value.get("storage_allowed", True)),
                hash=value.get("hash"),
                metadata=dict(value.get("metadata") or {}),
            )
        if kind == _MEDIA_REF:
            metadata = _hydrate_value(value.get("metadata") or {})
            return MediaRef(
                source=value.get("source", "inline_base64"),
                mime_type=str(value.get("mime_type", "")),
                data_base64=value.get("data_base64"),
                url=value.get("url"),
                artifact_id=value.get("artifact_id"),
                provider_file_id=value.get("provider_file_id"),
                bytes_count=value.get("bytes_count"),
                storage_allowed=bool(value.get("storage_allowed", False)),
                sensitivity=value.get("sensitivity", "sensitive"),
                metadata=metadata if isinstance(metadata, dict) else {},
            )
        if kind == _CONTENT_ITEM:
            parts = _hydrate_value(value.get("parts") or [])
            metadata = _hydrate_value(value.get("metadata") or {})
            return ContentItem(
                role=value.get("role", "user"),
                parts=[part for part in parts if _is_content_part(part)]
                if isinstance(parts, list)
                else [],
                item_id=value.get("item_id"),
                metadata=metadata if isinstance(metadata, dict) else {},
            )
        if kind == _TEXT_PART:
            return TextPart(text=str(value.get("text", "")))
        if kind == _AUDIO_PART:
            media = _hydrate_value(value.get("media"))
            return AudioPart(
                media=media if isinstance(media, MediaRef) else None,
                transcript=value.get("transcript"),
                format=value.get("format"),
                sample_rate_hz=value.get("sample_rate_hz"),
                channels=value.get("channels"),
                duration_ms=value.get("duration_ms"),
            )
        if kind == _IMAGE_PART:
            media = _hydrate_value(value.get("media"))
            return ImagePart(
                media=media if isinstance(media, MediaRef) else None,
                detail=value.get("detail", "auto"),
            )
        if kind == _VIDEO_FRAME_PART:
            media = _hydrate_value(value.get("media"))
            return VideoFramePart(
                media=media if isinstance(media, MediaRef) else None,
                timestamp_ms=value.get("timestamp_ms"),
            )
        if kind == _FILE_PART:
            media = _hydrate_value(value.get("media"))
            return FilePart(
                media=media if isinstance(media, MediaRef) else None,
                filename=value.get("filename"),
            )
        if kind == _TOOL_RESULT_PART:
            return ToolResultPart(
                call_id=str(value.get("call_id", "")),
                content=str(value.get("content", "")),
                payload=_hydrate_value(value.get("payload")),
            )
        if kind == _PROVIDER_NATIVE_PART:
            metadata = _hydrate_value(value.get("metadata") or {})
            return ProviderNativePart(
                provider=str(value.get("provider", "")),
                value=_hydrate_value(value.get("value")),
                metadata=metadata if isinstance(metadata, dict) else {},
            )
        if kind == _TOOL_BUDGET:
            return ToolBudget(
                max_visible_tools=int(value.get("max_visible_tools", 12)),
                max_schema_tokens=int(value.get("max_schema_tokens", 8000)),
                max_mcp_tools=int(value.get("max_mcp_tools", 8)),
                max_agent_tools=int(value.get("max_agent_tools", 4)),
                max_handoffs=int(value.get("max_handoffs", 3)),
            )
        if kind == _TOOL_ROUTING_SPEC:
            budget = _hydrate_value(value.get("budget"))
            metadata = _hydrate_value(value.get("metadata") or {})
            return ToolRoutingSpec(
                mode=value.get("mode", "explicit"),
                budget=budget if isinstance(budget, ToolBudget) else ToolBudget(),
                always_include=tuple(value.get("always_include") or ()),
                never_include=tuple(value.get("never_include") or ()),
                include_categories=tuple(value.get("include_categories") or ()),
                include_tags=tuple(value.get("include_tags") or ()),
                exclude_tags=tuple(value.get("exclude_tags") or ()),
                allow_late_bind=bool(value.get("allow_late_bind", False)),
                allow_model_discovery_tools=bool(
                    value.get("allow_model_discovery_tools", False)
                ),
                refresh_each_turn=bool(value.get("refresh_each_turn", True)),
                min_score=float(value.get("min_score", 0.15)),
                metadata=metadata if isinstance(metadata, dict) else {},
            )
        if kind == _TOOL_CANDIDATE:
            parameters = _hydrate_value(value.get("parameters"))
            metadata = _hydrate_value(value.get("metadata") or {})
            return ToolCandidate(
                ref=str(value.get("ref", "")),
                name=str(value.get("name", "")),
                kind=value.get("kind", "local"),
                description=str(value.get("description", "")),
                parameters=parameters if isinstance(parameters, dict) else None,
                category=value.get("category"),
                tags=tuple(value.get("tags") or ()),
                source=value.get("source"),
                provider=value.get("provider"),
                risk=value.get("risk", "read_only"),
                estimated_schema_tokens=int(value.get("estimated_schema_tokens", 0)),
                requires_approval=bool(value.get("requires_approval", False)),
                metadata=metadata if isinstance(metadata, dict) else {},
            )
        if kind == _SELECTED_TOOL:
            candidate = _hydrate_value(value.get("candidate"))
            return SelectedTool(
                candidate=(
                    candidate
                    if isinstance(candidate, ToolCandidate)
                    else ToolCandidate(ref="", name="", kind="local", description="")
                ),
                score=float(value.get("score", 0.0)),
                reason=str(value.get("reason", "")),
                selected_by=str(value.get("selected_by", "selector")),
            )
        if kind == _TOOL_SELECTION_RESULT:
            selected = _hydrate_value(value.get("selected") or [])
            discoverable = _hydrate_value(value.get("discoverable") or [])
            blocked = _hydrate_value(value.get("blocked") or [])
            metadata = _hydrate_value(value.get("metadata") or {})
            return ToolSelectionResult(
                selected=tuple(item for item in selected if isinstance(item, SelectedTool))
                if isinstance(selected, list)
                else (),
                discoverable=tuple(
                    item for item in discoverable if isinstance(item, ToolCandidate)
                )
                if isinstance(discoverable, list)
                else (),
                blocked=tuple(item for item in blocked if isinstance(item, ToolCandidate))
                if isinstance(blocked, list)
                else (),
                metadata=metadata if isinstance(metadata, dict) else {},
            )
        if kind == _RESOLVED_TOOL_PLAN:
            metadata = _hydrate_value(value.get("metadata") or {})
            provider_tools = _hydrate_value(value.get("provider_tools") or [])
            prompt_fragments = _hydrate_value(value.get("prompt_fragments") or [])
            hosted_tools = _hydrate_value(value.get("hosted_tools") or [])
            return ResolvedToolPlan(
                selected_refs=tuple(value.get("selected_refs") or ()),
                local_tools=tuple(value.get("local_tools") or ()),
                workspace_tools=tuple(value.get("workspace_tools") or ()),
                mcp_tools=tuple(value.get("mcp_tools") or ()),
                hosted_tools=tuple(hosted_tools) if isinstance(hosted_tools, list) else (),
                agent_tools=tuple(value.get("agent_tools") or ()),
                handoffs=tuple(value.get("handoffs") or ()),
                provider_tools=tuple(provider_tools) if isinstance(provider_tools, list) else (),
                prompt_fragments=tuple(prompt_fragments)
                if isinstance(prompt_fragments, list)
                else (),
                fingerprint=value.get("fingerprint"),
                metadata=metadata if isinstance(metadata, dict) else {},
            )
        return {k: _hydrate_value(v) for k, v in value.items() if k != "_kind"}
    if isinstance(value, list):
        return [_hydrate_value(item) for item in value]
    return value


def _is_content_part(value: Any) -> bool:
    return isinstance(
        value,
        (
            TextPart,
            AudioPart,
            ImagePart,
            VideoFramePart,
            FilePart,
            ToolResultPart,
            ProviderNativePart,
        ),
    )
