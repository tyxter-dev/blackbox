"""Outbound media: an agent sends an image/document through a tool payload.

Mirrors the ``send_media`` pattern from ``docs/USE_CASE_VALIDATION.md``
(580/610 production agents send media to customers): the model calls a
``send_media`` tool, the tool resolves a ``MediaRef`` and hands it to the
application through the deferred-payload channel, and the application — not
the model — delivers the bytes over its messaging channel.

For the *inbound* direction, pass ``ContentItem`` entries through
``runtime.run(input=[...])`` — e.g.
``ContentItem(role="user", parts=[TextPart(text="what is this?"),
ImagePart.from_url("https://...", mime_type="image/jpeg")])`` — and the
OpenAI Responses, Anthropic Messages, and Gemini adapters map the parts to
their native multimodal input shapes.

Run::

    python examples/media_messages.py
"""
from __future__ import annotations

# ruff: noqa: E402
import asyncio
from collections.abc import AsyncIterator
from typing import Any

from _bootstrap import bootstrap

bootstrap()

from blackbox import AgentRuntime, EventTypes, MediaRef
from blackbox.core.capabilities import ModelCapabilities
from blackbox.core.events import AgentEvent
from blackbox.core.state import ProviderState
from blackbox.providers.base import TurnRequest
from blackbox.tools import ToolResult

# The media library the agent can send from (catalog id -> MediaRef + caption
# defaults). In a real application these point at object storage or a CMS.
MEDIA_LIBRARY: dict[str, dict[str, Any]] = {
    "menu-2026": {
        "media": MediaRef.from_url(
            "https://cdn.example.test/bella-cucina/menu-2026.pdf",
            mime_type="application/pdf",
        ),
        "kind": "document",
        "description": "Current menu (PDF)",
    },
    "tiramisu-photo": {
        "media": MediaRef.from_url(
            "https://cdn.example.test/bella-cucina/tiramisu.jpg",
            mime_type="image/jpeg",
        ),
        "kind": "image",
        "description": "Tiramisu of the day",
    },
}


def send_media(media_id: str, caption: str) -> ToolResult:
    entry = MEDIA_LIBRARY.get(media_id)
    if entry is None:
        return ToolResult(
            content=f"Unknown media id {media_id!r}. Available: {sorted(MEDIA_LIBRARY)}",
            metadata={"error": "unknown_media"},
        )
    media: MediaRef = entry["media"]
    return ToolResult(
        # The model only needs confirmation; bytes never enter the context.
        content=f"Media {media_id!r} queued for delivery with caption {caption!r}.",
        # The application gets the typed reference to actually deliver.
        payload={
            "deliver": {
                "media_id": media_id,
                "kind": entry["kind"],
                "mime_type": media.mime_type,
                "media": media,
                "caption": caption,
            }
        },
    )


class ScriptedModel:
    provider_id = "scripted"

    def __init__(self) -> None:
        self._turns: list[Any] = []

    def queue(self, turn: Any) -> None:
        self._turns.append(turn)

    def capabilities(self, model: str | None = None) -> ModelCapabilities:
        return ModelCapabilities(supports_streaming_events=True, supports_function_tools=True)

    async def stream_turn(self, request: TurnRequest) -> AsyncIterator[AgentEvent]:
        turn = self._turns.pop(0)
        for event in turn(request):
            yield event


def _media_calls():
    def turn(request: TurnRequest):
        yield AgentEvent(type=EventTypes.MODEL_REQUEST_STARTED, provider="scripted")
        yield AgentEvent(
            type=EventTypes.TOOL_CALL_REQUESTED,
            provider="scripted",
            item_id="m1",
            data={"call_id": "m1", "name": "send_media",
                  "arguments": {"media_id": "tiramisu-photo",
                                "caption": "Today's tiramisu - fresh from the kitchen!"}},
        )
        yield AgentEvent(
            type=EventTypes.TOOL_CALL_REQUESTED,
            provider="scripted",
            item_id="m2",
            data={"call_id": "m2", "name": "send_media",
                  "arguments": {"media_id": "menu-2026",
                                "caption": "And here is our full menu."}},
        )
        yield AgentEvent(
            type=EventTypes.MODEL_COMPLETED,
            provider="scripted",
            data={"provider_state": ProviderState(provider="scripted")},
        )

    return turn


def _final_text(text: str):
    def turn(request: TurnRequest):
        yield AgentEvent(type=EventTypes.MODEL_REQUEST_STARTED, provider="scripted")
        yield AgentEvent(
            type=EventTypes.MODEL_TEXT_DELTA, provider="scripted", data={"delta": text}
        )
        yield AgentEvent(
            type=EventTypes.MODEL_COMPLETED,
            provider="scripted",
            data={"provider_state": ProviderState(provider="scripted")},
        )

    return turn


async def main() -> None:
    scripted = ScriptedModel()
    scripted.queue(_media_calls())
    scripted.queue(_final_text("Sent you a photo of today's tiramisu and our menu - anything else?"))

    runtime = AgentRuntime()
    runtime.registry.register_model(scripted)
    runtime.tools.register(
        send_media,
        name="send_media",
        description="Send an image or document from the media library to the customer.",
    )

    result = await runtime.run(
        provider="scripted:host",
        input="Can you show me a dessert and send the menu?",
        tools=["send_media"],
    )

    print("deliveries collected for the messaging channel:")
    for payload in result.payloads:
        deliver = (payload.payload or {}).get("deliver")
        if deliver:
            media: MediaRef = deliver["media"]
            print(f"  {deliver['kind']:8} {deliver['mime_type']:16} "
                  f"{media.url}  caption={deliver['caption']!r}")

    print(f"\nagent reply: {result.output}")


if __name__ == "__main__":
    asyncio.run(main())
