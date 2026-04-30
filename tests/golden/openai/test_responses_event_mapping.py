from __future__ import annotations

import pytest

from blackbox import AgentRuntime, EventTypes
from blackbox.core.errors import ProviderExecutionError, ProviderNotConfiguredError
from blackbox.core.items import ItemTypes, RunItem
from blackbox.core.state import ProviderState
from blackbox.providers.base import TurnRequest
from blackbox.providers.model_adapters.openai_responses import OpenAIResponsesProvider
from blackbox.providers.model_adapters.xai_responses import XAIResponsesProvider
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
    assert len(tool_events) == 1
    assert result.events[1].type == EventTypes.MODEL_ITEM_CREATED
    payload = tool_events[-1].data
    assert payload["call_id"] == "call_abc"
    assert payload["name"] == "lookup_weather"
    assert payload["arguments"] == {"city": "Paris"}


async def test_hosted_tool_maps_to_typed_run_item() -> None:
    client = FakeOpenAIClient()
    hosted = item("web_search_call", id_="ws_1", query="agent runtime", status="completed")
    client.queue(
        events=[
            evt("response.output_item.added", item=hosted),
            evt("response.output_item.done", item=hosted),
        ],
        final_response=final_response(id_="resp_3", output=[hosted]),
    )

    runtime = _runtime_with(client)
    result = await runtime.models.run(provider="openai/gpt-5.4", input="search")

    hosted_events = [
        e
        for e in result.events
        if e.data.get("hosted_tool_type") == "web_search"
    ]
    assert [event.type for event in hosted_events] == [
        EventTypes.MODEL_ITEM_CREATED,
        EventTypes.MODEL_ITEM_COMPLETED,
    ]
    run_item = hosted_events[0].data["item"]
    assert run_item.type == ItemTypes.HOSTED_TOOL_CALL
    assert run_item.data["query"] == "agent runtime"
    assert hosted_events[0].raw is not None  # raw payload preserved
    assert result.metadata["hosted_tools"] == {
        "calls": [
            {
                "type": "web_search",
                "item_type": "web_search_call",
                "item_id": "ws_1",
                "status": "completed",
                "query": "agent runtime",
            }
        ],
        "completed": [
            {
                "type": "web_search",
                "item_type": "web_search_call",
                "item_id": "ws_1",
                "status": "completed",
                "query": "agent runtime",
            }
        ],
        "types": ["web_search"],
    }
    assert result.metadata["trace"]["spans"][0]["attributes"]["hosted_tools"][
        "types"
    ] == ["web_search"]


async def test_incomplete_terminal_response_can_finalize_stream() -> None:
    client = FakeOpenAIClient()
    msg = item("message", id_="msg_partial")
    partial_response = final_response(
        id_="resp_partial",
        output=[msg],
        status="incomplete",
        incomplete_details={"reason": "max_output_tokens"},
    )
    client.queue(
        events=[
            evt("response.output_item.added", item=msg),
            evt("response.output_text.delta", delta="partial answer", item_id="msg_partial"),
            evt("response.output_item.done", item=msg),
            evt("response.incomplete", response=partial_response),
        ],
        final_error=RuntimeError("Didn't receive a `response.completed` event."),
    )

    runtime = _runtime_with(client)
    result = await runtime.models.run(provider="openai/gpt-5.4", input="search")

    assert result.text == "partial answer"
    assert result.provider_state is not None
    assert result.provider_state.previous_response_id == "resp_partial"
    completed = result.events[-1]
    assert completed.type == EventTypes.MODEL_COMPLETED
    assert completed.data["status"] == "incomplete"
    assert completed.data["incomplete_details"] == {"reason": "max_output_tokens"}


async def test_unknown_hosted_tool_falls_back_to_generic_item_event() -> None:
    client = FakeOpenAIClient()
    hosted = item("browser_widget_call", id_="bw_1", prompt="inspect")
    client.queue(
        events=[evt("response.output_item.added", item=hosted)],
        final_response=final_response(id_="resp_3b", output=[hosted]),
    )

    runtime = _runtime_with(client)
    result = await runtime.models.run(provider="openai/gpt-5.4", input="inspect")

    fallback = [
        e
        for e in result.events
        if e.type == EventTypes.MODEL_ITEM_CREATED
        and e.data.get("item_type") == "browser_widget_call"
    ]
    assert fallback, "unknown hosted tool should keep the generic item path"
    assert fallback[0].data["item"].type == "browser_widget_call"
    assert fallback[0].raw is not None


async def test_image_generation_item_extracts_artifact() -> None:
    client = FakeOpenAIClient()
    generated = item(
        "image_generation_call",
        id_="img_1",
        call_id="img_1",
        url="https://cdn.example.com/image.png",
    )
    client.queue(
        events=[evt("response.output_item.done", item=generated)],
        final_response=final_response(id_="resp_img", output=[generated]),
    )

    runtime = _runtime_with(client)
    result = await runtime.models.run(provider="openai/gpt-5.4", input="draw")

    completed = next(e for e in result.events if e.item_id == "img_1")
    assert completed.data["hosted_tool_type"] == "image_generation"
    assert completed.data["artifact"].type == "image"
    assert completed.data["artifact"].uri == "https://cdn.example.com/image.png"
    assert result.artifacts == [completed.data["artifact"]]


async def test_mcp_items_include_typed_run_item_data() -> None:
    client = FakeOpenAIClient()
    list_tools = item(
        "mcp_list_tools",
        id_="mcpl_1",
        server_label="github",
        tools=[{"name": "list_issues"}],
    )
    mcp_call = item(
        "mcp_call",
        id_="mcp_1",
        server_label="github",
        name="list_issues",
        arguments='{"state": "open"}',
        output='{"count": 2}',
    )
    approval = item(
        "mcp_approval_request",
        id_="mcpr_1",
        server_label="github",
        name="delete_repo",
        arguments='{"repo": "danger"}',
    )
    client.queue(
        events=[
            evt("response.output_item.added", item=list_tools),
            evt("response.output_item.added", item=mcp_call),
            evt("response.output_item.done", item=mcp_call),
            evt("response.output_item.added", item=approval),
        ],
        final_response=final_response(id_="resp_mcp", output=[list_tools, mcp_call, approval]),
    )

    runtime = _runtime_with(client)
    result = await runtime.models.run(provider="openai/gpt-5.4", input="use mcp")

    list_event = next(e for e in result.events if e.item_id == "mcpl_1")
    assert list_event.type == EventTypes.MCP_LIST_TOOLS_COMPLETED
    assert list_event.data["server_label"] == "github"
    assert list_event.data["server"] == "github"
    assert list_event.data["route_mode"] == "provider_native"
    assert list_event.data["item"].type == ItemTypes.MCP_LIST_TOOLS

    completed = next(
        e
        for e in result.events
        if e.type == EventTypes.MCP_CALL_COMPLETED and e.item_id == "mcp_1"
    )
    assert completed.data["name"] == "list_issues"
    assert completed.data["server"] == "github"
    assert completed.data["tool"] == "list_issues"
    assert completed.data["ref"] == "mcp:github.list_issues"
    assert completed.data["route_mode"] == "provider_native"
    assert completed.data["arguments"] == {"state": "open"}
    assert completed.data["output"] == '{"count": 2}'
    assert completed.data["item"].type == ItemTypes.MCP_CALL
    assert completed.data["item"].status == "completed"

    approval_event = next(e for e in result.events if e.item_id == "mcpr_1")
    assert approval_event.type == EventTypes.MCP_APPROVAL_REQUIRED
    assert approval_event.data["ref"] == "mcp:github.delete_repo"
    assert approval_event.data["item"].type == ItemTypes.MCP_APPROVAL_REQUEST
    assert approval_event.data["item"].status == "requires_action"


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


async def test_openai_compatible_subclass_preserves_provider_identity() -> None:
    client = FakeOpenAIClient()
    msg = item("message", id_="msg_xai")
    client.queue(
        events=[
            evt("response.output_item.added", item=msg),
            evt("response.output_text.delta", delta="pong", item_id="msg_xai"),
            evt("response.output_item.done", item=msg),
        ],
        final_response=final_response(id_="resp_xai", output=[msg]),
    )

    runtime = AgentRuntime()
    runtime.registry.register_model(XAIResponsesProvider(client=client))
    result = await runtime.models.run(provider="xai/grok-4", input="ping")

    assert result.text == "pong"
    assert result.provider_state is not None
    assert result.provider_state.provider == "xai"
    assert {event.provider for event in result.events} == {"xai"}


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


async def test_missing_credentials_and_client_raises_configuration_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
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
        def stream(self, **kwargs: object) -> object:
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


def _dummy_request(text: str) -> TurnRequest:
    return TurnRequest(model="gpt-5.4", input=text)
