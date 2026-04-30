from __future__ import annotations

import pytest

from blackbox import AgentRuntime, FileSearch, HostedToolRaw, WebSearch
from blackbox.core.errors import UnsupportedFeatureError
from blackbox.core.results import AgentResult, OutputSpec
from blackbox.output.schema import build_output_schema
from blackbox.providers.model_adapters.anthropic_messages import AnthropicMessagesProvider
from blackbox.providers.model_adapters.echo import EchoModelProvider
from blackbox.providers.model_adapters.gemini_generate_content import (
    GeminiGenerateContentProvider,
)
from tests.fixtures.fake_anthropic_client import FakeAnthropicClient, final_message
from tests.fixtures.fake_gemini_client import FakeGeminiClient, chunk, text_part
from tests.fixtures.scripted_model import ScriptedModelProvider, text_only_turn


async def test_runtime_rejects_unsupported_hosted_tool_before_provider_call() -> None:
    runtime = AgentRuntime()
    scripted = ScriptedModelProvider()
    runtime.registry.register_model(scripted)

    with pytest.raises(UnsupportedFeatureError, match="file_search"):
        async for _ in runtime.models.stream(
            provider="scripted:test",
            input="hi",
            hosted_tools=[FileSearch(vector_store_ids=["vs_1"])],
        ):
            pass
    assert scripted.calls == []


async def test_runtime_rejects_unsupported_control_before_provider_call() -> None:
    runtime = AgentRuntime()
    runtime.registry.register_model(EchoModelProvider())

    with pytest.raises(UnsupportedFeatureError, match="temperature"):
        async for _ in runtime.models.stream(
            provider="echo:echo-mini",
            input="hi",
            temperature=0.2,
        ):
            pass


async def test_runtime_rejects_unsupported_modalities_before_provider_call() -> None:
    runtime = AgentRuntime()
    scripted = ScriptedModelProvider()
    runtime.registry.register_model(scripted)

    with pytest.raises(UnsupportedFeatureError, match="modalities"):
        async for _ in runtime.models.stream(
            provider="scripted:test",
            input="hi",
            modalities=["text"],
        ):
            pass
    assert scripted.calls == []


async def test_runtime_applies_output_fallback() -> None:
    runtime = AgentRuntime()
    runtime.registry.register_model(EchoModelProvider())

    result: AgentResult[dict[str, object]] = await runtime.run(
        provider="echo:echo-mini",
        input='{"name": "ok"}',
        output_spec=OutputSpec(
            schema={
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            },
            strategy="provider_native",
            fallback="posthoc_parse",
        ),
    )

    assert result.output == {"name": "ok"}
    assert result.metadata["provider_native_fallback"] == "posthoc_parse"


async def test_runtime_rejects_unsupported_state_mode() -> None:
    runtime = AgentRuntime()
    runtime.registry.register_model(EchoModelProvider())

    with pytest.raises(UnsupportedFeatureError, match="encrypted_reasoning"):
        async for _ in runtime.models.stream(
            provider="echo:echo-mini",
            input="hi",
            state_mode="encrypted_reasoning",
        ):
            pass


async def test_runtime_allows_passthrough_raw_hosted_tool() -> None:
    runtime = AgentRuntime()
    scripted = ScriptedModelProvider()
    scripted.queue(text_only_turn("ok"))
    runtime.registry.register_model(scripted)

    result = await runtime.models.run(
        provider="scripted:test",
        input="hi",
        hosted_tools=[HostedToolRaw({"type": "future_tool"})],
    )

    assert result.text == "ok"
    assert scripted.calls[0].hosted_tools == [HostedToolRaw({"type": "future_tool"})]


async def test_anthropic_rejects_native_structured_output_before_provider_call() -> None:
    runtime = AgentRuntime()
    client = FakeAnthropicClient()
    runtime.registry.register_model(AnthropicMessagesProvider(client=client))
    schema = build_output_schema(
        OutputSpec(
            schema={
                "type": "object",
                "properties": {"summary": {"type": "string"}},
                "required": ["summary"],
            },
            strategy="provider_native",
        )
    )
    assert schema is not None

    with pytest.raises(UnsupportedFeatureError, match="provider-native structured output"):
        async for _ in runtime.models.stream(
            provider="anthropic",
            model="claude-3-5-sonnet-20241022",
            input="summarize",
            output_schema=schema,
            output_strategy="provider_native",
        ):
            pass
    assert client.messages.seen_kwargs == []


async def test_anthropic_native_structured_output_can_be_sent_with_tools() -> None:
    runtime = AgentRuntime()
    client = FakeAnthropicClient()
    client.queue([], final_message=final_message(id_="msg_1", content=[]))
    runtime.registry.register_model(AnthropicMessagesProvider(client=client))
    schema = build_output_schema(
        OutputSpec(
            schema={
                "type": "object",
                "properties": {"summary": {"type": "string"}},
                "required": ["summary"],
            },
            strategy="provider_native",
        )
    )
    assert schema is not None

    await runtime.models.run(
        provider="anthropic",
        model="claude-sonnet-4-6-20260201",
        input="summarize",
        tools=[
            {
                "type": "function",
                "name": "lookup",
                "description": "Lookup data.",
                "parameters": {"type": "object", "properties": {}},
                "strict": True,
            }
        ],
        hosted_tools=[WebSearch()],
        output_schema=schema,
        output_strategy="provider_native",
    )

    sent = client.messages.seen_kwargs[0]
    assert sent["output_config"] == {
        "format": {"type": "json_schema", "schema": schema.schema}
    }
    assert {"type": "web_search_20260209", "name": "web_search"} in sent["tools"]
    assert any(tool.get("name") == "lookup" and tool.get("strict") is True for tool in sent["tools"])


async def test_gemini_rejects_structured_output_with_tools_for_non_gemini3() -> None:
    runtime = AgentRuntime()
    client = FakeGeminiClient()
    runtime.registry.register_model(GeminiGenerateContentProvider(client=client))
    schema = build_output_schema(
        OutputSpec(
            schema={
                "type": "object",
                "properties": {"summary": {"type": "string"}},
                "required": ["summary"],
            },
            strategy="provider_native",
        )
    )
    assert schema is not None

    with pytest.raises(UnsupportedFeatureError, match="Gemini structured output"):
        async for _ in runtime.models.stream(
            provider="google",
            model="gemini-2.5-flash",
            input="summarize",
            hosted_tools=[WebSearch()],
            output_schema=schema,
            output_strategy="provider_native",
        ):
            pass
    assert client.calls == []


async def test_gemini3_allows_structured_output_with_builtin_and_function_tools() -> None:
    runtime = AgentRuntime()
    client = FakeGeminiClient()
    client.queue([chunk(response_id="resp_1", parts=[text_part('{"summary":"ok"}')])])
    runtime.registry.register_model(GeminiGenerateContentProvider(client=client))
    schema = build_output_schema(
        OutputSpec(
            schema={
                "type": "object",
                "properties": {"summary": {"type": "string"}},
                "required": ["summary"],
            },
            strategy="provider_native",
        )
    )
    assert schema is not None

    await runtime.models.run(
        provider="google",
        model="gemini-3-flash-preview",
        input="summarize",
        tools=[
            {
                "type": "function",
                "name": "lookup",
                "description": "Lookup data.",
                "parameters": {"type": "object", "properties": {}},
            }
        ],
        hosted_tools=[WebSearch()],
        output_schema=schema,
        output_strategy="provider_native",
    )

    config = client.calls[0]["config"]
    assert config["response_mime_type"] == "application/json"
    assert config["response_json_schema"] == schema.schema
    assert config["include_server_side_tool_invocations"] is True
    assert {"google_search": {}} in config["tools"]
    assert any("function_declarations" in tool for tool in config["tools"])
