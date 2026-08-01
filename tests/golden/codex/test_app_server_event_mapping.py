from __future__ import annotations

from blackbox.core.events import EventTypes
from blackbox.providers.agent_adapters.codex import _coerce_event


def test_codex_app_server_maps_reviewed_events_and_preserves_raw_payloads() -> None:
    raw = {
        "id": "evt_delta",
        "method": "item/agentMessage/delta",
        "params": {
            "delta": "hello",
            "itemId": "item_1",
            "threadId": "thread_1",
            "turnId": "turn_1",
        },
    }

    event = _coerce_event(raw, provider="codex", session_id="thread_1")

    assert event.type == EventTypes.MODEL_TEXT_DELTA
    assert event.id == "evt_delta"
    assert event.item_id == "item_1"
    assert event.data["delta"] == "hello"
    assert event.raw is raw


def test_codex_unknown_notification_is_a_non_authority_log_projection() -> None:
    raw = {
        "id": "evt_future",
        "method": "item/futureProgress",
        "params": {"threadId": "thread_1", "turnId": "turn_1"},
    }

    event = _coerce_event(raw, provider="codex", session_id="thread_1")

    assert event.type == EventTypes.CLOUD_AGENT_LOG
    assert event.raw is raw
