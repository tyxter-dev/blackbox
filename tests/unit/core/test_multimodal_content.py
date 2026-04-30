from __future__ import annotations

import json

from blackbox.core.content import (
    AudioPart,
    ContentItem,
    FilePart,
    ImagePart,
    ProviderNativePart,
    TextPart,
    ToolResultPart,
)
from blackbox.core.events import AgentEvent, EventTypes
from blackbox.core.media import MediaRef
from blackbox.core.serialization import event_from_dict, event_to_dict


def test_content_parts_serialize_and_round_trip() -> None:
    media = MediaRef.from_url("https://example.test/image.png", mime_type="image/png")
    item = ContentItem(
        role="user",
        parts=[
            TextPart(text="look"),
            ImagePart(media=media),
            FilePart(media=MediaRef.from_artifact("artifact_1", mime_type="text/plain")),
            ToolResultPart(call_id="call_1", content="ok", payload={"ok": True}),
            ProviderNativePart(provider="x", value={"native": True}),
        ],
    )
    event = AgentEvent(type=EventTypes.REALTIME_INPUT_ITEM_CREATED, data={"content": item})

    serialized = json.loads(json.dumps(event_to_dict(event)))
    rehydrated = event_from_dict(serialized)

    assert isinstance(rehydrated.data["content"], ContentItem)
    assert [part.type for part in rehydrated.data["content"].parts] == [
        "text",
        "image",
        "file",
        "tool_result",
        "raw",
    ]


def test_inline_media_redacts_by_default() -> None:
    media = MediaRef.from_bytes(b"abc", mime_type="audio/pcm")
    event = AgentEvent(
        type=EventTypes.REALTIME_INPUT_AUDIO_DELTA,
        data={"media": media, "audio": AudioPart(media=media)},
    )

    payload = event_to_dict(event)

    assert payload["data"]["media"]["data_base64"] is None
    assert payload["data"]["media"]["redacted"] is True
    assert payload["data"]["media"]["bytes_count"] == 3
    assert payload["data"]["audio"]["media"]["data_base64"] is None


def test_inline_media_preserves_only_when_allowed_and_requested() -> None:
    media = MediaRef.from_bytes(b"abc", mime_type="audio/pcm", storage_allowed=True)
    event = AgentEvent(type=EventTypes.REALTIME_INPUT_AUDIO_DELTA, data={"media": media})

    default_payload = event_to_dict(event)
    kept_payload = event_to_dict(event, keep_media=True)

    assert default_payload["data"]["media"]["data_base64"] is None
    assert kept_payload["data"]["media"]["data_base64"] == "YWJj"


def test_disallowed_inline_media_redacts_even_when_requested() -> None:
    media = MediaRef.from_bytes(b"abc", mime_type="audio/pcm", storage_allowed=False)
    event = AgentEvent(type=EventTypes.REALTIME_INPUT_AUDIO_DELTA, data={"media": media})

    payload = event_to_dict(event, keep_media=True)

    assert payload["data"]["media"]["data_base64"] is None
    assert payload["data"]["media"]["redacted"] is True
