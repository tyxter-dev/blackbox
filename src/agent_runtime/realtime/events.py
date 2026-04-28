from __future__ import annotations

import base64
from collections.abc import Mapping
from typing import Any

from agent_runtime.core.events import AgentEvent
from agent_runtime.core.media import MediaRef
from agent_runtime.core.raw import RawEnvelope, Sensitivity


def realtime_event(
    event_type: str,
    *,
    provider: str,
    data: Mapping[str, Any] | None = None,
    session_id: str | None = None,
    item_id: str | None = None,
    raw: Any | None = None,
    raw_schema_name: str | None = None,
) -> AgentEvent:
    """Build a normalized realtime event with optional raw provider payload."""

    return AgentEvent(
        type=event_type,
        provider=provider,
        session_id=session_id,
        item_id=item_id,
        data=dict(data or {}),
        raw=raw_envelope(provider, raw, schema_name=raw_schema_name)
        if raw is not None
        else None,
    )


def raw_envelope(
    provider: str,
    payload: Any,
    *,
    schema_name: str | None = None,
    sensitivity: Sensitivity = "internal",
    storage_allowed: bool = True,
) -> RawEnvelope:
    """Wrap a provider-native realtime payload with raw-envelope metadata."""

    return RawEnvelope(
        provider=provider,
        payload=payload,
        schema_name=schema_name,
        sensitivity=sensitivity,
        storage_allowed=storage_allowed,
    )


def media_ref_from_base64_delta(
    data_base64: str,
    *,
    mime_type: str,
    storage_allowed: bool = False,
    metadata: dict[str, Any] | None = None,
) -> MediaRef:
    return MediaRef.from_base64(
        data_base64,
        mime_type=mime_type,
        bytes_count=base64_size(data_base64),
        storage_allowed=storage_allowed,
        metadata=metadata,
    )


def base64_size(value: str) -> int | None:
    try:
        return len(base64.b64decode(value, validate=True))
    except Exception:
        return None
