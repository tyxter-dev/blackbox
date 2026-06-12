"""Non-string inputs through the high-level loop.

Chat-shaped history (the compat on-ramp for platforms migrating off
chat-centric stacks) and typed content items must flow through
``runtime.run`` — including the tool loop — not just ``runtime.models``.
"""
from __future__ import annotations

from typing import Any

from blackbox import AgentRuntime, ChatMessage, ContentItem, EventTypes, ImagePart, TextPart
from blackbox.compat.chat import messages_to_input
from blackbox.core.events import AgentEvent
from blackbox.core.items import ItemTypes, RunItem
from blackbox.core.media import MediaRef
from blackbox.core.state import ProviderState
from blackbox.tools import ToolResult
from tests.fixtures.scripted_model import ScriptedModelProvider, text_only_turn


def _runtime() -> tuple[AgentRuntime, ScriptedModelProvider]:
    runtime = AgentRuntime()
    scripted = ScriptedModelProvider()
    runtime.registry.register_model(scripted)
    return runtime, scripted


async def test_chat_history_flows_through_loop_with_tools() -> None:
    runtime, scripted = _runtime()
    runtime.tools.register(
        lambda order_id: ToolResult(content=f"status:{order_id}:shipped"),
        name="order_status",
        description="Look up an order status.",
    )

    history = messages_to_input([
        ChatMessage(role="system", content="Be concise."),
        ChatMessage(role="user", content="Hi, I ordered a lamp."),
        ChatMessage(role="assistant", content="Welcome back! How can I help?"),
        {"role": "user", "content": "Where is order ord_7?"},
    ])

    def tool_turn(request: Any) -> Any:
        # The chat history reaches the first turn verbatim.
        assert request.input == history
        assert isinstance(request.input, list)
        yield AgentEvent(type=EventTypes.MODEL_REQUEST_STARTED, provider="scripted")
        yield AgentEvent(
            type=EventTypes.TOOL_CALL_REQUESTED,
            provider="scripted",
            item_id="c1",
            data={"call_id": "c1", "name": "order_status", "arguments": {"order_id": "ord_7"}},
        )
        yield AgentEvent(
            type=EventTypes.MODEL_COMPLETED,
            provider="scripted",
            data={"provider_state": ProviderState(provider="scripted")},
        )

    def final_turn(request: Any) -> Any:
        # The continuation turn carries tool results as run items.
        assert isinstance(request.input, list)
        assert all(isinstance(entry, RunItem) for entry in request.input)
        assert request.input[0].type == ItemTypes.FUNCTION_RESULT
        assert request.input[0].data["content"] == "status:ord_7:shipped"
        yield from text_only_turn("Your lamp shipped.")(request)

    scripted.queue(tool_turn)
    scripted.queue(final_turn)

    result = await runtime.run(
        provider="scripted:test",
        input=history,
        tools=["order_status"],
    )
    assert result.output == "Your lamp shipped."
    assert any(
        event.type == EventTypes.TOOL_CALL_COMPLETED
        and event.data.get("name") == "order_status"
        for event in result.events
    )


async def test_content_items_flow_through_loop() -> None:
    runtime, scripted = _runtime()
    message = ContentItem(
        role="user",
        parts=[
            TextPart(text="What is in this photo?"),
            ImagePart(media=MediaRef.from_url("https://cdn.test/p.jpg", mime_type="image/jpeg")),
        ],
    )

    def turn(request: Any) -> Any:
        assert request.input == [message]
        yield from text_only_turn("A lamp on a desk.")(request)

    scripted.queue(turn)
    result = await runtime.run(provider="scripted:test", input=[message])
    assert result.output == "A lamp on a desk."
