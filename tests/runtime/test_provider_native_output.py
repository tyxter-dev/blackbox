from __future__ import annotations

from pydantic import BaseModel

from agent_runtime import AgentResult, AgentRuntime
from agent_runtime.core.capabilities import ModelCapabilities
from agent_runtime.core.errors import UnsupportedFeatureError
from agent_runtime.core.events import EventTypes
from agent_runtime.core.items import ItemTypes
from agent_runtime.core.results import OutputSpec
from tests.fixtures.scripted_model import ScriptedModelProvider, text_only_turn, tool_call_turn


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


async def test_finalizer_tool_strategy_returns_validated_output() -> None:
    runtime = AgentRuntime()
    scripted = ScriptedModelProvider()
    runtime.registry.register_model(scripted)
    scripted.queue(
        tool_call_turn(
            call_id="final_1",
            name="submit_final_output",
            arguments={"priority": "low", "escalate": False},
        )
    )

    result: AgentResult[Decision] = await runtime.run(
        provider="scripted:test",
        input="decide",
        output_spec=OutputSpec(schema=Decision, strategy="finalizer_tool"),
    )

    assert result.output == Decision(priority="low", escalate=False)
    assert "submit_final_output" not in [tool.name for tool in runtime.tools.all_tools()]
    finalizer_tool = scripted.calls[0].tools[0]
    assert finalizer_tool["name"] == "submit_final_output"
    assert finalizer_tool["metadata"] == {
        "agent_runtime_hidden": True,
        "purpose": "final_output",
        "schema_name": "Decision",
    }
    completed = [event for event in result.events if event.type == EventTypes.TOOL_CALL_COMPLETED]
    assert completed[-1].data["metadata"]["purpose"] == "final_output"
    assert result.items[-1].type == ItemTypes.FUNCTION_RESULT


async def test_provider_native_can_fallback_to_finalizer_tool() -> None:
    runtime = AgentRuntime()
    scripted = ScriptedModelProvider()
    runtime.registry.register_model(scripted)
    scripted.queue(
        tool_call_turn(
            call_id="final_1",
            name="submit_final_output",
            arguments={"summary": "done"},
        )
    )

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
            fallback="finalizer_tool",
        ),
    )

    assert result.output == {"summary": "done"}
    assert scripted.calls[0].output_strategy == "finalizer_tool"
