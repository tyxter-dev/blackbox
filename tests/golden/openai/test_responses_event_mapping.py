from __future__ import annotations

from agent_runtime import AgentRuntime, EventTypes
from agent_runtime.core.errors import ProviderExecutionError, ProviderNotConfiguredError
from agent_runtime.core.items import ItemTypes, RunItem
from agent_runtime.core.state import ProviderState
from agent_runtime.models.openai_responses import OpenAIResponsesProvider
from tests.fixtures.fake_openai_client import (
    FakeOpenAIClient,
    evt,
    final_response,
    item,
)


def _runtime_with(client: FakeOpenAIClient) -> AgentRuntime:
    runtime = AgentRuntime()
    runtime.registry.register_model(OpenAIResponsesProvider(client=client))
    return runtime


async def test_text_only_stream_produces_text_and_provider_state() -> None:
    client = FakeOpenAIClient()
    msg = item("message", id_="msg_1")
    client.queue(
        events=[
            evt("response.created"),
            evt("response.output_item.added", item=msg),
            evt("response.output_text.delta", delta="hello ", item_id="msg_1"),
            evt("response.output_text.delta", delta="world", item_id="msg_1"),
            evt("response.output_item.done", item=msg),
            evt("response.completed"),
        ],
        final_response=final_response(id_="resp_1", output=[msg]),
    )

    runtime = _runtime_with(client)
    result = await runtime.models.run(provider="openai/gpt-5.4", input="hi")

    assert result.text == "hello world"
    assert result.provider_state is not None
    assert result.provider_state.previous_response_id == "resp_1"
    types = [e.type for e in result.events]
    assert types[0] == EventTypes.MODEL_REQUEST_STARTED
    assert types[-1] == EventTypes.MODEL_COMPLETED
    assert EventTypes.MODEL_ITEM_CREATED in types
    assert EventTypes.MODEL_ITEM_COMPLETED in types


async def test_function_call_emits_tool_call_requested_with_parsed_arguments() -> None:
    client = FakeOpenAIClient()
    fn_item = item(
        "function_call",
        id_="fc_1",
        call_id="call_abc",
        name="lookup_weather",
        arguments='{"city": "Paris"}',
    )
    client.queue(
        events=[
            evt("response.output_item.added", item=fn_item),
            evt("response.output_item.done", item=fn_item),
        ],
        final_response=final_response(id_="resp_2", output=[fn_item]),
    )

    runtime = _runtime_with(client)
    result = await runtime.models.run(provider="openai/gpt-5.4", input="weather?")

    tool_events = [e for e in result.events if e.type == EventTypes.TOOL_CALL_REQUESTED]
    assert len(tool_events) >= 1
    payload = tool_events[-1].data
    assert payload["call_id"] == "call_abc"
    assert payload["name"] == "lookup_weather"
    assert payload["arguments"] == {"city": "Paris"}


async def test_hosted_tool_falls_back_to_generic_item_event() -> None:
    client = FakeOpenAIClient()
    hosted = item("web_search_call", id_="ws_1", query="agent runtime")
    client.queue(
        events=[evt("response.output_item.added", item=hosted)],
        final_response=final_response(id_="resp_3", output=[hosted]),
    )

    runtime = _runtime_with(client)
    result = await runtime.models.run(provider="openai/gpt-5.4", input="search")

    fallback = [
        e for e in result.events
        if e.type == EventTypes.MODEL_ITEM_CREATED and e.data.get("item_type") == "web_search_call"
    ]
    assert fallback, "hosted tool should map to MODEL_ITEM_CREATED with item_type"
    assert fallback[0].raw is not None  # raw payload preserved


async def test_provider_state_drives_previous_response_id() -> None:
    client = FakeOpenAIClient()
    msg = item("message", id_="msg_2")
    client.queue(
        events=[
            evt("response.output_item.added", item=msg),
            evt("response.output_text.delta", delta="ok", item_id="msg_2"),
            evt("response.output_item.done", item=msg),
        ],
        final_response=final_response(id_="resp_continue", output=[msg]),
    )

    prior = ProviderState(provider="openai", previous_response_id="resp_prev")
    runtime = _runtime_with(client)
    await runtime.models.run(
        provider="openai/gpt-5.4", input="continue", provider_state=prior,
    )

    assert client.responses.seen_kwargs[0]["previous_response_id"] == "resp_prev"


async def test_function_result_run_items_become_function_call_outputs() -> None:
    client = FakeOpenAIClient()
    msg = item("message", id_="msg_3")
    client.queue(
        events=[
            evt("response.output_item.added", item=msg),
            evt("response.output_text.delta", delta="thanks", item_id="msg_3"),
            evt("response.output_item.done", item=msg),
        ],
        final_response=final_response(id_="resp_4", output=[msg]),
    )

    follow_up = [
        RunItem(
            type=ItemTypes.FUNCTION_RESULT,
            provider="local",
            status="completed",
            data={"call_id": "call_abc", "name": "lookup_weather", "content": "sunny"},
        )
    ]
    runtime = _runtime_with(client)
    await runtime.models.run(provider="openai/gpt-5.4", input=follow_up)

    sent_input = client.responses.seen_kwargs[0]["input"]
    assert isinstance(sent_input, list) and len(sent_input) == 1
    assert sent_input[0] == {
        "type": "function_call_output",
        "call_id": "call_abc",
        "output": "sunny",
    }


async def test_missing_credentials_and_client_raises_configuration_error() -> None:
    provider = OpenAIResponsesProvider()
    try:
        async for _ in provider.stream_turn(_dummy_request("hi")):
            pass
    except ProviderNotConfiguredError:
        pass
    else:
        raise AssertionError("expected ProviderNotConfiguredError")


async def test_sdk_failure_wrapped_in_provider_execution_error() -> None:
    class ExplodingResponses:
        def stream(self, **kwargs):
            raise RuntimeError("boom")

    bad_client = type("C", (), {"responses": ExplodingResponses()})()
    provider = OpenAIResponsesProvider(client=bad_client)
    try:
        async for _ in provider.stream_turn(_dummy_request("hi")):
            pass
    except ProviderExecutionError as exc:
        assert "boom" in str(exc)
    else:
        raise AssertionError("expected ProviderExecutionError")


def _dummy_request(text: str):
    from agent_runtime.providers.base import TurnRequest
    return TurnRequest(model="gpt-5.4", input=text)
