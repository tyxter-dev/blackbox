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

from agent_runtime.core.events import AgentEvent
from agent_runtime.core.items import RunItem
from agent_runtime.core.raw import RawEnvelope
from agent_runtime.core.state import ProviderState, RunState

_AGENT_EVENT = "AgentEvent"
_RUN_ITEM = "RunItem"
_PROVIDER_STATE = "ProviderState"
_RAW_ENVELOPE = "RawEnvelope"
_RUN_STATE = "RunState"


def event_to_dict(event: AgentEvent, *, keep_raw: bool = False) -> dict[str, Any]:
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
        "data": _safe_value(event.data),
        "raw": _safe_value(event.raw) if keep_raw else None,
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


def _safe_value(value: Any) -> Any:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, RunItem):
        return {
            "_kind": _RUN_ITEM,
            "type": value.type,
            "provider": value.provider,
            "data": _safe_value(value.data),
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
            "native_history": _safe_value(value.native_history),
            "reasoning_state": _safe_value(value.reasoning_state),
            "tool_state": _safe_value(value.tool_state),
            "continuation": _safe_value(value.continuation),
        }
    if isinstance(value, RawEnvelope):
        return {
            "_kind": _RAW_ENVELOPE,
            "provider": value.provider,
            "payload": _safe_value(value.payload),
            "schema_name": value.schema_name,
            "schema_version": value.schema_version,
            "sensitivity": value.sensitivity,
            "redaction_status": value.redaction_status,
            "storage_allowed": value.storage_allowed,
            "hash": value.hash,
            "metadata": _safe_value(value.metadata),
        }
    if isinstance(value, dict):
        return {str(k): _safe_value(v) for k, v in value.items()}
    if isinstance(value, list | tuple | set | frozenset):
        return [_safe_value(item) for item in value]
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
        return {k: _hydrate_value(v) for k, v in value.items() if k != "_kind"}
    if isinstance(value, list):
        return [_hydrate_value(item) for item in value]
    return value
