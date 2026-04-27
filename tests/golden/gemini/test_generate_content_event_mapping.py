from __future__ import annotations

from types import SimpleNamespace

from agent_runtime import AgentRuntime
from agent_runtime.core.events import EventTypes
from agent_runtime.core.items import ItemTypes, RunItem
from agent_runtime.core.state import ProviderState
from agent_runtime.models.gemini_generate_content import GeminiGenerateContentProvider
from tests.fixtures.fake_gemini_client import (
    FakeGeminiClient,
    chunk,
    function_call_part,
    text_part,
    thought_part,
)


def _runtime_with(client: FakeGeminiClient) -> AgentRuntime:
    runtime = AgentRuntime()
    runtime.registry.register_model(GeminiGenerateContentProvider(client=client))
    return runtime


async def test_text_stream_produces_text_and_provider_state() -> None:
    client = FakeGeminiClient()
    client.queue(
        [
            chunk(response_id="resp_1", parts=[text_part("hel")]),
            chunk(response_id="resp_1", parts=[text_part("lo")]),
        ]
    )
    runtime = _runtime_with(client)

    result = await runtime.models.run(provider="google/gemini-test", input="say hi")

    assert result.text == "hello"
    assert result.provider_state is not None
    assert result.provider_state.provider == "google"
    assert result.provider_state.conversation_id == "resp_1"
    assert result.provider_state.native_history[-1] == {
        "role": "model",
        "parts": [{"text": "hel"}, {"text": "lo"}],
    }
    assert client.calls[0]["contents"] == [
        {"role": "user", "parts": [{"text": "say hi"}]}
    ]


async def test_awaitable_stream_produces_text_and_provider_state() -> None:
    client = FakeGeminiClient(awaitable_stream=True)
    client.queue([chunk(response_id="resp_awaitable", parts=[text_part("pong")])])
    runtime = _runtime_with(client)

    result = await runtime.models.run(provider="google/gemini-test", input="say pong")

    assert result.text == "pong"
    assert result.provider_state is not None
    assert result.provider_state.conversation_id == "resp_awaitable"


async def test_function_call_part_emits_tool_call_requested() -> None:
    client = FakeGeminiClient()
    client.queue(
        [
            chunk(
                response_id="resp_tools",
                parts=[
                    function_call_part(
                        id_="call_1",
                        name="lookup_ticket",
                        args={"ticket_id": "T-1"},
                    )
                ],
            )
        ]
    )
    runtime = _runtime_with(client)

    events = [
        event
        async for event in runtime.models.stream(
            provider="google/gemini-test",
            input="lookup",
            config={
                "tools": [
                    {
                        "type": "function",
                        "name": "lookup_ticket",
                        "description": "Find a ticket.",
                        "parameters": {
                            "type": "object",
                            "properties": {"ticket_id": {"type": "string"}},
                        },
                    }
                ]
            },
        )
    ]

    tool_event = next(event for event in events if event.type == EventTypes.TOOL_CALL_REQUESTED)
    assert tool_event.data["call_id"] == "call_1"
    assert tool_event.data["name"] == "lookup_ticket"
    assert tool_event.data["arguments"] == {"ticket_id": "T-1"}
    assert tool_event.data["item"].type == ItemTypes.FUNCTION_CALL
    assert client.calls[0]["config"]["tools"] == [
        {
            "function_declarations": [
                {
                    "name": "lookup_ticket",
                    "description": "Find a ticket.",
                    "parameters": {
                        "type": "object",
                        "properties": {"ticket_id": {"type": "string"}},
                    },
                }
            ]
        }
    ]


async def test_reasoning_part_preserves_thought_signature() -> None:
    client = FakeGeminiClient()
    client.queue([chunk(response_id="resp_2", parts=[thought_part("thinking")])])
    runtime = _runtime_with(client)

    events = [
        event
        async for event in runtime.models.stream(provider="google/gemini-test", input="think")
    ]

    assert any(event.type == EventTypes.MODEL_REASONING_DELTA for event in events)
    completed = next(event for event in events if event.type == EventTypes.MODEL_COMPLETED)
    provider_state = completed.data["provider_state"]
    assert isinstance(provider_state, ProviderState)
    assert provider_state.reasoning_state == {"thought_signatures": ["sig"]}


async def test_function_result_run_items_become_function_response_parts() -> None:
    client = FakeGeminiClient()
    client.queue([chunk(response_id="resp_3", parts=[text_part("done")])])
    runtime = _runtime_with(client)

    await runtime.models.run(
        provider="google/gemini-test",
        input=[
            RunItem(
                type=ItemTypes.FUNCTION_RESULT,
                provider="local",
                data={
                    "call_id": "call_1",
                    "name": "lookup_ticket",
                    "content": {"status": "open"},
                },
            )
        ],
    )

    assert client.calls[0]["contents"] == [
        {
            "role": "user",
            "parts": [
                {
                    "function_response": {
                        "id": "call_1",
                        "name": "lookup_ticket",
                        "response": {"status": "open"},
                    }
                }
            ],
        }
    ]


async def test_provider_state_preserves_tool_ids_sources_and_file_handles() -> None:
    client = FakeGeminiClient()
    client.queue(
        [
            SimpleNamespace(
                response_id="resp_state",
                candidates=[
                    SimpleNamespace(
                        grounding_metadata={"web_search_queries": ["agent runtime"]},
                        content=SimpleNamespace(
                            parts=[
                                function_call_part(
                                    id_="call_9",
                                    name="lookup_ticket",
                                    args={"ticket_id": "T-9"},
                                ),
                                SimpleNamespace(file_data={"file_uri": "gs://bucket/doc.pdf"}),
                            ]
                        ),
                    )
                ],
            )
        ]
    )
    runtime = _runtime_with(client)

    result = await runtime.models.run(provider="google/gemini-test", input="lookup")

    assert result.provider_state is not None
    assert result.provider_state.tool_state["tool_call_ids"] == ["call_9"]
    assert result.provider_state.tool_state["source_references"] == [
        {
            "field": "grounding_metadata",
            "value": {"web_search_queries": ["agent runtime"]},
        }
    ]
    assert result.provider_state.tool_state["file_handles"] == [
        {"field": "file_data", "value": {"file_uri": "gs://bucket/doc.pdf"}}
    ]
