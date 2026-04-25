from __future__ import annotations

from agent_runtime import AgentRuntime, EventTypes
from agent_runtime.core.errors import ProviderExecutionError, ProviderNotConfiguredError
from agent_runtime.core.items import ItemTypes, RunItem
from agent_runtime.core.state import ProviderState
from agent_runtime.models.anthropic_messages import AnthropicMessagesProvider
from agent_runtime.providers.base import TurnRequest
from tests.fixtures.fake_anthropic_client import (
    FakeAnthropicClient,
    block_delta,
    block_start,
    block_stop,
    final_message,
    hosted_block,
    input_json_delta,
    text_block,
    text_delta,
    thinking_block,
    thinking_delta,
    tool_use_block,
)


def _runtime_with(client: FakeAnthropicClient) -> AgentRuntime:
    runtime = AgentRuntime()
    runtime.registry.register_model(AnthropicMessagesProvider(client=client))
    return runtime


async def test_text_only_stream_produces_text_and_provider_state() -> None:
    client = FakeAnthropicClient()
    msg = text_block(id_="msg_1")
    client.queue(
        events=[
            block_start(0, msg),
            block_delta(0, text_delta("hello ")),
            block_delta(0, text_delta("world")),
            block_stop(0),
        ],
        final_message=final_message(
            id_="msg_resp_1",
            content=[{"type": "text", "text": "hello world"}],
        ),
    )

    runtime = _runtime_with(client)
    result = await runtime.models.run(
        provider="anthropic", model="claude-haiku-4-5-20251001", input="hi"
    )

    assert result.text == "hello world"
    assert result.provider_state is not None
    assert result.provider_state.continuation.get("last_message_id") == "msg_resp_1"
    history = result.provider_state.native_history
    assert history[0] == {"role": "user", "content": "hi"}
    assert history[-1]["role"] == "assistant"
    types = [e.type for e in result.events]
    assert types[0] == EventTypes.MODEL_REQUEST_STARTED
    assert types[-1] == EventTypes.MODEL_COMPLETED
    assert EventTypes.MODEL_ITEM_CREATED in types
    assert EventTypes.MODEL_ITEM_COMPLETED in types


async def test_tool_use_block_emits_tool_call_requested_with_parsed_input() -> None:
    client = FakeAnthropicClient()
    tool = tool_use_block(id_="toolu_abc", name="lookup_weather")
    client.queue(
        events=[
            block_start(0, tool),
            block_delta(0, input_json_delta('{"city":')),
            block_delta(0, input_json_delta(' "Paris"}')),
            block_stop(0),
        ],
        final_message=final_message(
            id_="msg_resp_tool",
            content=[
                {
                    "type": "tool_use",
                    "id": "toolu_abc",
                    "name": "lookup_weather",
                    "input": {"city": "Paris"},
                }
            ],
        ),
    )

    runtime = _runtime_with(client)
    result = await runtime.models.run(
        provider="anthropic", model="claude-haiku-4-5-20251001", input="weather?"
    )

    tool_events = [e for e in result.events if e.type == EventTypes.TOOL_CALL_REQUESTED]
    assert len(tool_events) == 1
    payload = tool_events[0].data
    assert payload["call_id"] == "toolu_abc"
    assert payload["name"] == "lookup_weather"
    assert payload["arguments"] == {"city": "Paris"}


async def test_thinking_block_emits_reasoning_deltas() -> None:
    client = FakeAnthropicClient()
    thinking = thinking_block(id_="th_1")
    client.queue(
        events=[
            block_start(0, thinking),
            block_delta(0, thinking_delta("Let me think...")),
            block_stop(0),
        ],
        final_message=final_message(
            id_="msg_thinking",
            content=[{"type": "thinking", "thinking": "Let me think..."}],
        ),
    )

    runtime = _runtime_with(client)
    result = await runtime.models.run(
        provider="anthropic", model="claude-haiku-4-5-20251001", input="hard q"
    )

    reasoning = [e for e in result.events if e.type == EventTypes.MODEL_REASONING_DELTA]
    assert reasoning and reasoning[0].data["delta"] == "Let me think..."


async def test_hosted_block_falls_back_to_generic_item_event() -> None:
    client = FakeAnthropicClient()
    hosted = hosted_block("server_tool_use", id_="srv_1", name="web_search")
    client.queue(
        events=[block_start(0, hosted), block_stop(0)],
        final_message=final_message(id_="msg_hosted"),
    )

    runtime = _runtime_with(client)
    result = await runtime.models.run(
        provider="anthropic", model="claude-haiku-4-5-20251001", input="search"
    )

    fallback = [
        e for e in result.events
        if e.type == EventTypes.MODEL_ITEM_CREATED
        and e.data.get("item_type") == "server_tool_use"
    ]
    assert fallback, "hosted block should map to MODEL_ITEM_CREATED with item_type"
    assert fallback[0].raw is not None


async def test_function_result_run_items_become_tool_result_blocks() -> None:
    client = FakeAnthropicClient()
    msg = text_block(id_="msg_2")
    client.queue(
        events=[
            block_start(0, msg),
            block_delta(0, text_delta("thanks")),
            block_stop(0),
        ],
        final_message=final_message(
            id_="msg_resp_2",
            content=[{"type": "text", "text": "thanks"}],
        ),
    )

    follow_up = [
        RunItem(
            type=ItemTypes.FUNCTION_RESULT,
            provider="local",
            status="completed",
            data={"call_id": "toolu_abc", "name": "lookup_weather", "content": "sunny"},
        )
    ]
    prior = ProviderState(
        provider="anthropic",
        native_history=[
            {"role": "user", "content": "weather?"},
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu_abc",
                        "name": "lookup_weather",
                        "input": {"city": "Paris"},
                    }
                ],
            },
        ],
    )
    runtime = _runtime_with(client)
    await runtime.models.run(
        provider="anthropic",
        model="claude-haiku-4-5-20251001",
        input=follow_up,
        provider_state=prior,
    )

    sent_messages = client.messages.seen_kwargs[0]["messages"]
    assert len(sent_messages) == 3
    assert sent_messages[0] == {"role": "user", "content": "weather?"}
    assert sent_messages[1]["role"] == "assistant"
    assert sent_messages[2] == {
        "role": "user",
        "content": [
            {"type": "tool_result", "tool_use_id": "toolu_abc", "content": "sunny"}
        ],
    }


async def test_tools_are_converted_to_anthropic_input_schema() -> None:
    client = FakeAnthropicClient()
    msg = text_block(id_="msg_tools")
    client.queue(
        events=[block_start(0, msg), block_delta(0, text_delta("ok")), block_stop(0)],
        final_message=final_message(id_="m1", content=[{"type": "text", "text": "ok"}]),
    )

    provider_neutral_tools = [
        {
            "type": "function",
            "name": "lookup_weather",
            "description": "Lookup weather.",
            "parameters": {"type": "object", "properties": {"city": {"type": "string"}}},
        }
    ]
    runtime = _runtime_with(client)
    await runtime.models.run(
        provider="anthropic",
        model="claude-haiku-4-5-20251001",
        input="weather?",
        tools=provider_neutral_tools,
    )

    sent_tools = client.messages.seen_kwargs[0]["tools"]
    assert sent_tools == [
        {
            "name": "lookup_weather",
            "description": "Lookup weather.",
            "input_schema": {"type": "object", "properties": {"city": {"type": "string"}}},
        }
    ]


async def test_missing_credentials_and_client_raises_configuration_error() -> None:
    provider = AnthropicMessagesProvider()
    try:
        async for _ in provider.stream_turn(_dummy_request("hi")):
            pass
    except ProviderNotConfiguredError:
        pass
    else:
        raise AssertionError("expected ProviderNotConfiguredError")


async def test_sdk_failure_wrapped_in_provider_execution_error() -> None:
    class ExplodingMessages:
        def stream(self, **kwargs: object) -> object:
            raise RuntimeError("boom")

    bad_client = type("C", (), {"messages": ExplodingMessages()})()
    provider = AnthropicMessagesProvider(client=bad_client)
    try:
        async for _ in provider.stream_turn(_dummy_request("hi")):
            pass
    except ProviderExecutionError as exc:
        assert "boom" in str(exc)
    else:
        raise AssertionError("expected ProviderExecutionError")


def _dummy_request(text: str) -> TurnRequest:
    return TurnRequest(model="claude-haiku-4-5-20251001", input=text)
