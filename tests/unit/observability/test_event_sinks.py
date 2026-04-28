"""Tests for the observability sink layer.

Covers VALIDATION row 5.8 (raw redaction): RawEnvelope-tagged events with
sensitive payloads are rewritten before they reach a downstream sink.
"""
from __future__ import annotations

from agent_runtime.core.events import AgentEvent, EventTypes
from agent_runtime.core.raw import RawEnvelope
from agent_runtime.observability import (
    MemoryEventSink,
    RedactingEventSink,
    default_redaction_policy,
)


def _event(raw: object) -> AgentEvent:
    return AgentEvent(type=EventTypes.MODEL_TEXT_DELTA, provider="test", raw=raw)


async def test_memory_sink_collects_events_in_order() -> None:
    sink = MemoryEventSink()
    await sink.emit(_event(None))
    await sink.emit(_event("plain"))
    assert len(sink.events) == 2
    assert sink.events[1].raw == "plain"


async def test_redacting_sink_passes_through_when_raw_is_none() -> None:
    inner = MemoryEventSink()
    sink = RedactingEventSink(inner=inner)
    event = _event(None)
    await sink.emit(event)
    assert inner.events == [event]


async def test_redacting_sink_passes_through_non_envelope_payloads() -> None:
    """v0.1 only redacts RawEnvelope-tagged payloads; bare SDK objects pass."""
    inner = MemoryEventSink()
    sink = RedactingEventSink(inner=inner)
    raw_sdk_object = {"type": "response.created", "id": "resp_1"}
    event = _event(raw_sdk_object)
    await sink.emit(event)
    assert inner.events[0].raw is raw_sdk_object


async def test_redacting_sink_passes_through_internal_envelope() -> None:
    inner = MemoryEventSink()
    sink = RedactingEventSink(inner=inner)
    envelope = RawEnvelope(provider="test", payload={"prompt": "hello"})
    await sink.emit(_event(envelope))
    assert inner.events[0].raw is envelope


async def test_redacting_sink_redacts_sensitive_envelope() -> None:
    inner = MemoryEventSink()
    sink = RedactingEventSink(inner=inner)
    envelope = RawEnvelope(
        provider="test",
        payload={"api_key": "sk-secret"},
        sensitivity="secret",
    )
    await sink.emit(_event(envelope))

    forwarded = inner.events[0].raw
    assert isinstance(forwarded, RawEnvelope)
    assert forwarded.payload == "<redacted>"
    assert forwarded.redaction_status == "redacted"
    # Original envelope is untouched (frozen dataclass + replace semantics).
    assert envelope.payload == {"api_key": "sk-secret"}


async def test_redacting_sink_redacts_when_storage_disallowed() -> None:
    inner = MemoryEventSink()
    sink = RedactingEventSink(inner=inner)
    envelope = RawEnvelope(
        provider="test",
        payload={"customer_pii": "..."},
        sensitivity="internal",
        storage_allowed=False,
    )
    await sink.emit(_event(envelope))
    forwarded = inner.events[0].raw
    assert isinstance(forwarded, RawEnvelope)
    assert forwarded.payload == "<redacted>"


async def test_redacting_sink_honors_custom_marker() -> None:
    inner = MemoryEventSink()
    sink = RedactingEventSink(inner=inner, marker="[REDACTED]")
    envelope = RawEnvelope(
        provider="test", payload={"x": 1}, sensitivity="sensitive"
    )
    await sink.emit(_event(envelope))
    assert inner.events[0].raw.payload == "[REDACTED]"  # type: ignore[union-attr]


async def test_redacting_sink_honors_custom_policy() -> None:
    inner = MemoryEventSink()
    # Custom policy: redact only when provider is "anthropic".
    sink = RedactingEventSink(
        inner=inner,
        policy=lambda env: env.provider == "anthropic",
    )
    openai_env = RawEnvelope(provider="openai", payload="visible", sensitivity="secret")
    anthropic_env = RawEnvelope(provider="anthropic", payload="hidden", sensitivity="public")
    await sink.emit(_event(openai_env))
    await sink.emit(_event(anthropic_env))

    assert inner.events[0].raw is openai_env  # not redacted
    forwarded = inner.events[1].raw
    assert isinstance(forwarded, RawEnvelope)
    assert forwarded.payload == "<redacted>"


def test_default_policy_marks_sensitive_and_blocked_storage() -> None:
    assert default_redaction_policy(
        RawEnvelope(provider="t", payload="x", sensitivity="sensitive")
    )
    assert default_redaction_policy(
        RawEnvelope(provider="t", payload="x", sensitivity="secret")
    )
    assert default_redaction_policy(
        RawEnvelope(provider="t", payload="x", storage_allowed=False)
    )
    assert not default_redaction_policy(
        RawEnvelope(provider="t", payload="x", sensitivity="internal")
    )
    assert not default_redaction_policy(
        RawEnvelope(provider="t", payload="x", sensitivity="public")
    )
