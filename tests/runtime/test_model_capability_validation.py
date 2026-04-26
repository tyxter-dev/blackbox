from __future__ import annotations

import pytest

from agent_runtime import AgentRuntime, FileSearch, HostedToolRaw
from agent_runtime.core.errors import UnsupportedFeatureError
from agent_runtime.core.results import AgentResult, OutputSpec
from agent_runtime.models.echo import EchoModelProvider
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
