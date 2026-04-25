from __future__ import annotations

from pydantic import BaseModel

from agent_runtime import AgentResult, AgentRuntime
from agent_runtime.core.capabilities import ModelCapabilities
from agent_runtime.core.errors import UnsupportedFeatureError
from agent_runtime.core.results import OutputSpec
from tests.fixtures.scripted_model import ScriptedModelProvider, text_only_turn


class Decision(BaseModel):
    priority: str
    escalate: bool


class StructuredScriptedModelProvider(ScriptedModelProvider):
    def capabilities(self, model: str | None = None) -> ModelCapabilities:
        return ModelCapabilities(
            supports_streaming_events=True,
            supports_function_tools=True,
            supports_provider_state=True,
            supports_structured_output=True,
        )


async def test_provider_native_output_validates_pydantic_result() -> None:
    runtime = AgentRuntime()
    scripted = StructuredScriptedModelProvider()
    runtime.registry.register_model(scripted)
    scripted.queue(text_only_turn('{"priority": "high", "escalate": true}'))

    result: AgentResult[Decision] = await runtime.run(
        provider="scripted:test",
        input="decide",
        output_spec=OutputSpec(schema=Decision, strategy="provider_native"),
    )

    assert result.output == Decision(priority="high", escalate=True)
    assert scripted.calls[0].output_schema is not None
    assert scripted.calls[0].output_strategy == "provider_native"


async def test_provider_native_dict_schema_returns_dict() -> None:
    runtime = AgentRuntime()
    scripted = StructuredScriptedModelProvider()
    runtime.registry.register_model(scripted)
    scripted.queue(text_only_turn('{"summary": "done"}'))

    result: AgentResult[dict[str, object]] = await runtime.run(
        provider="scripted:test",
        input="summarize",
        output_spec=OutputSpec(
            schema={
                "type": "object",
                "properties": {"summary": {"type": "string"}},
                "required": ["summary"],
            },
            strategy="provider_native",
        ),
    )

    assert result.output == {"summary": "done"}


async def test_provider_native_unsupported_provider_raises() -> None:
    runtime = AgentRuntime()
    scripted = ScriptedModelProvider()
    runtime.registry.register_model(scripted)

    try:
        await runtime.run(
            provider="scripted:test",
            input="decide",
            output_spec=OutputSpec(schema=Decision, strategy="provider_native"),
        )
    except UnsupportedFeatureError as exc:
        assert "provider-native structured output" in str(exc)
    else:
        raise AssertionError("expected provider_native to fail for unsupported provider")
