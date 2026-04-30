from __future__ import annotations

from typing import Any

from blackbox import AgentRuntime, ChatMessage
from blackbox.compat.chat import message_from_assistant_text, messages_to_input
from tests.fixtures.scripted_model import ScriptedModelProvider, text_only_turn


def test_chat_messages_project_to_plain_provider_input() -> None:
    messages: list[ChatMessage | dict[str, Any]] = [
        ChatMessage(role="system", content="Be concise."),
        {"role": "user", "content": "hello"},
    ]

    assert messages_to_input(messages) == [
        {"role": "system", "content": "Be concise."},
        {"role": "user", "content": "hello"},
    ]


def test_assistant_text_projection_returns_chat_message() -> None:
    message = message_from_assistant_text("pong")

    assert message.to_dict() == {"role": "assistant", "content": "pong"}


async def test_chat_runtime_facade_runs_against_model_runtime() -> None:
    runtime = AgentRuntime()
    scripted = ScriptedModelProvider()
    runtime.registry.register_model(scripted)
    scripted.queue(text_only_turn("pong"))

    result = await runtime.chat.run(
        provider="scripted:test",
        messages=[ChatMessage(role="user", content="ping")],
    )

    assert result.text == "pong"
    assert scripted.calls[0].input == [{"role": "user", "content": "ping"}]
