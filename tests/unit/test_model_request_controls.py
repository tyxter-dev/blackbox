from __future__ import annotations

from agent_runtime.core.results import OutputSpec
from agent_runtime.models.anthropic_messages import AnthropicMessagesProvider
from agent_runtime.models.gemini_generate_content import GeminiGenerateContentProvider
from agent_runtime.models.openai_responses import OpenAIResponsesProvider
from agent_runtime.models.xai_responses import XAIResponsesProvider
from agent_runtime.output.schema import build_output_schema
from agent_runtime.providers.base import ModelRequestControls, TurnRequest


def test_openai_responses_maps_common_controls_to_native_kwargs() -> None:
    request = TurnRequest(
        model="gpt-test",
        input="hi",
        tools=[{"name": "lookup", "parameters": {"type": "object"}}],
        controls=ModelRequestControls(
            instructions="Be brief.",
            temperature=0.2,
            top_p=0.9,
            max_output_tokens=64,
            tool_choice="auto",
            parallel_tool_calls=False,
            reasoning_effort="low",
        ),
    )

    kwargs = OpenAIResponsesProvider._build_request_kwargs(request)

    assert kwargs["instructions"] == "Be brief."
    assert kwargs["temperature"] == 0.2
    assert kwargs["top_p"] == 0.9
    assert kwargs["max_output_tokens"] == 64
    assert kwargs["tool_choice"] == "auto"
    assert kwargs["parallel_tool_calls"] is False
    assert kwargs["reasoning"] == {"effort": "low"}
    assert kwargs["tools"] == [{"name": "lookup", "parameters": {"type": "object"}}]


def test_openai_extra_overrides_common_controls() -> None:
    request = TurnRequest(
        model="gpt-test",
        input="hi",
        controls=ModelRequestControls(max_output_tokens=64),
        extra={"max_output_tokens": 32},
    )

    assert OpenAIResponsesProvider._build_request_kwargs(request)["max_output_tokens"] == 32


def test_xai_drops_unsupported_instructions_control() -> None:
    request = TurnRequest(
        model="grok-test",
        input="hi",
        controls=ModelRequestControls(instructions="Be brief.", temperature=0.2),
    )

    kwargs = XAIResponsesProvider._build_request_kwargs(request)

    assert "instructions" not in kwargs
    assert kwargs["temperature"] == 0.2


def test_anthropic_messages_maps_common_controls_to_native_kwargs() -> None:
    request = TurnRequest(
        model="claude-test",
        input="hi",
        controls=ModelRequestControls(
            instructions="Be brief.",
            temperature=0.2,
            top_p=0.9,
            max_output_tokens=64,
            tool_choice={"type": "auto"},
        ),
    )

    kwargs = AnthropicMessagesProvider._build_request_kwargs(
        request, [{"role": "user", "content": "hi"}]
    )

    assert kwargs["system"] == "Be brief."
    assert kwargs["temperature"] == 0.2
    assert kwargs["top_p"] == 0.9
    assert kwargs["max_tokens"] == 64
    assert kwargs["tool_choice"] == {"type": "auto"}


def test_gemini_generate_content_maps_common_controls_to_config() -> None:
    request = TurnRequest(
        model="gemini-test",
        input="hi",
        controls=ModelRequestControls(
            instructions="Be brief.",
            temperature=0.2,
            top_p=0.9,
            max_output_tokens=64,
        ),
    )

    kwargs = GeminiGenerateContentProvider._build_request_kwargs(request)

    assert kwargs["config"]["system_instruction"] == "Be brief."
    assert kwargs["config"]["temperature"] == 0.2
    assert kwargs["config"]["top_p"] == 0.9
    assert kwargs["config"]["max_output_tokens"] == 64


def test_gemini_config_extra_overrides_common_controls() -> None:
    request = TurnRequest(
        model="gemini-test",
        input="hi",
        controls=ModelRequestControls(max_output_tokens=64),
        extra={"config": {"max_output_tokens": 32}},
    )

    kwargs = GeminiGenerateContentProvider._build_request_kwargs(request)

    assert kwargs["config"]["max_output_tokens"] == 32


def test_openai_responses_maps_provider_native_output_schema_to_text_format() -> None:
    output_schema = build_output_schema(
        OutputSpec(
            schema={
                "type": "object",
                "properties": {"summary": {"type": "string"}},
                "required": ["summary"],
            },
            name="report",
            description="Report output",
        )
    )
    assert output_schema is not None
    request = TurnRequest(
        model="gpt-test",
        input="hi",
        output_schema=output_schema,
        output_strategy="provider_native",
    )

    kwargs = OpenAIResponsesProvider._build_request_kwargs(request)

    assert kwargs["text"]["format"] == {
        "type": "json_schema",
        "name": "report",
        "schema": output_schema.schema,
        "strict": True,
        "description": "Report output",
    }


def test_openai_responses_rejects_conflicting_text_format() -> None:
    output_schema = build_output_schema(OutputSpec(schema={"type": "object"}, name="report"))
    assert output_schema is not None
    request = TurnRequest(
        model="gpt-test",
        input="hi",
        output_schema=output_schema,
        output_strategy="provider_native",
        extra={"text": {"format": {"type": "json_object"}}},
    )

    try:
        OpenAIResponsesProvider._build_request_kwargs(request)
    except ValueError as exc:
        assert "conflicts" in str(exc)
    else:
        raise AssertionError("expected conflicting text format to fail")


def test_gemini_maps_provider_native_output_schema_to_config() -> None:
    output_schema = build_output_schema(OutputSpec(schema={"type": "object"}, name="report"))
    assert output_schema is not None
    request = TurnRequest(
        model="gemini-test",
        input="hi",
        output_schema=output_schema,
        output_strategy="provider_native",
    )

    kwargs = GeminiGenerateContentProvider._build_request_kwargs(request)

    assert kwargs["config"]["response_mime_type"] == "application/json"
    assert kwargs["config"]["response_json_schema"] == {"type": "object"}


def test_gemini_rejects_conflicting_response_schema_config() -> None:
    output_schema = build_output_schema(OutputSpec(schema={"type": "object"}, name="report"))
    assert output_schema is not None
    request = TurnRequest(
        model="gemini-test",
        input="hi",
        output_schema=output_schema,
        output_strategy="provider_native",
        extra={"config": {"response_json_schema": {"type": "array"}}},
    )

    try:
        GeminiGenerateContentProvider._build_request_kwargs(request)
    except ValueError as exc:
        assert "conflicts" in str(exc)
    else:
        raise AssertionError("expected conflicting response schema to fail")
