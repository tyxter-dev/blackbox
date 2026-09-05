from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest

from blackbox.core.errors import UnsupportedFeatureError
from blackbox.core.results import OutputSpec
from blackbox.output.schema import build_output_schema
from blackbox.providers.base import ModelRequestControls, TurnRequest
from blackbox.providers.model_adapters.anthropic_messages import (
    AnthropicMessagesProvider,
    _compose_messages,
)
from blackbox.providers.model_adapters.openai_responses import OpenAIResponsesProvider
from blackbox.providers.model_adapters.xai_responses import XAIResponsesProvider
from blackbox.tools.hosted.specs import TextEditor, WebFetch, WebSearch, to_anthropic_tool
from tests.fixtures.fake_anthropic_client import FakeAnthropicClient, final_message

CLAUDE_MODELS = (
    "claude-fable-5-1",
    "claude-fable-5",
    "claude-opus-5",
    "claude-sonnet-5",
    "claude-opus-4-8",
)


@pytest.mark.parametrize("model", CLAUDE_MODELS)
def test_current_claude_capabilities_and_adaptive_format(model: str) -> None:
    provider = AnthropicMessagesProvider()
    profile = provider.capability_profile(model)
    assert profile.output_strategies["provider_native"].status == "supported"
    assert profile.controls["compaction"].status == "supported"
    assert profile.controls["reasoning_effort"].supported_values == (
        "low",
        "medium",
        "high",
        "xhigh",
        "max",
    )
    request = TurnRequest(
        model=model,
        input="hi",
        controls=ModelRequestControls(reasoning_effort="max"),
        output_schema=build_output_schema(OutputSpec(schema={"type": "object", "properties": {}})),
        output_strategy="provider_native",
        extra={"extra_body": {"output_config": {"effort": "max"}}},
    )
    kwargs = provider._build_request_kwargs(request, _compose_messages(request))
    assert kwargs["thinking"] == {"type": "adaptive"}
    assert kwargs["output_config"]["effort"] == "max"
    assert kwargs["output_config"]["format"]["type"] == "json_schema"
    assert kwargs["extra_body"]["output_config"] == kwargs["output_config"]
    assert to_anthropic_tool(WebSearch(), model=model)["type"] == "web_search_20260209"
    assert to_anthropic_tool(TextEditor(), model=model)["type"] == "text_editor_20250728"


@pytest.mark.parametrize("model", CLAUDE_MODELS)
@pytest.mark.parametrize(
    "raw",
    [
        {"thinking": {"type": "enabled", "budget_tokens": 1024}},
        {"temperature": 0.2},
        {"top_p": 0.9},
        {"top_k": 5},
        {"output_config": {"effort": "minimal"}},
    ],
)
@pytest.mark.parametrize("nested", [False, True])
def test_current_claude_rejects_native_incompatible_controls(
    model: str, raw: dict[str, Any], nested: bool
) -> None:
    request = TurnRequest(model=model, input="hi", extra={"extra_body": raw} if nested else raw)
    with pytest.raises(UnsupportedFeatureError):
        AnthropicMessagesProvider._build_request_kwargs(request, _compose_messages(request))


@pytest.mark.parametrize("model", CLAUDE_MODELS)
def test_current_claude_rejects_typed_sampling_and_effort(model: str) -> None:
    for controls in (
        ModelRequestControls(temperature=0.5),
        ModelRequestControls(top_p=0.9),
        ModelRequestControls(reasoning_effort="none"),
    ):
        request = TurnRequest(model=model, input="hi", controls=controls)
        with pytest.raises(UnsupportedFeatureError):
            AnthropicMessagesProvider._build_request_kwargs(request, _compose_messages(request))


def test_claude_disabled_thinking_and_hosted_exclusions() -> None:
    provider = AnthropicMessagesProvider()
    for model in CLAUDE_MODELS:
        request = TurnRequest(model=model, input="hi", extra={"thinking": {"type": "disabled"}})
        if model.startswith("claude-fable"):
            with pytest.raises(UnsupportedFeatureError):
                provider._build_request_kwargs(request, _compose_messages(request))
        else:
            assert provider._build_request_kwargs(request, _compose_messages(request))[
                "thinking"
            ] == {"type": "disabled"}
    assert (
        provider.capability_profile("claude-opus-5").hosted_tools["web_fetch"].status
        == "unsupported"
    )
    for extra, hosted in (
        ({}, [WebFetch()]),
        ({"extra_body": {"tools": [{"type": "web_fetch_20260209"}]}}, []),
    ):
        request = TurnRequest(model="claude-opus-5", input="hi", extra=extra, hosted_tools=hosted)
        with pytest.raises(UnsupportedFeatureError, match="WebFetch"):
            provider._build_request_kwargs(request, _compose_messages(request))
    assert (
        provider.capability_profile("claude-fable-5-1").output_strategies["finalizer_tool"].status
        == "unsupported"
    )
    for choice in ("any", {"type": "tool", "name": "answer"}):
        request = TurnRequest(
            model="claude-fable-5-1", input="hi", extra={"extra_body": {"tool_choice": choice}}
        )
        with pytest.raises(UnsupportedFeatureError, match="auto/none"):
            provider._build_request_kwargs(request, _compose_messages(request))


@pytest.mark.parametrize(
    "provider,model,good,bad",
    [
        (OpenAIResponsesProvider, "gpt-6-astra", "max", "none"),
        (OpenAIResponsesProvider, "gpt-5.6-sol", "none", "minimal"),
        (OpenAIResponsesProvider, "gpt-5.6", "max", "minimal"),
        (OpenAIResponsesProvider, "gpt-5.6-terra", "max", "minimal"),
        (OpenAIResponsesProvider, "gpt-5.6-luna", "max", "minimal"),
        (XAIResponsesProvider, "grok-4.6", "xhigh", "max"),
    ],
)
def test_current_responses_effort_controls(provider: Any, model: str, good: str, bad: str) -> None:
    assert (
        good in provider().capability_profile(model).controls["reasoning_effort"].supported_values
    )
    assert (
        bad
        not in provider().capability_profile(model).controls["reasoning_effort"].supported_values
    )
    request = TurnRequest(
        model=model, input="hi", controls=ModelRequestControls(reasoning_effort=good)
    )
    assert provider._build_request_kwargs(request)["reasoning"]["effort"] == good
    for extra, controls in (
        ({}, ModelRequestControls(reasoning_effort=bad)),
        ({"reasoning": {"effort": bad}}, ModelRequestControls()),
        ({"extra_body": {"reasoning": {"effort": bad}}}, ModelRequestControls()),
    ):
        with pytest.raises(UnsupportedFeatureError):
            provider._build_request_kwargs(
                TurnRequest(model=model, input="hi", extra=extra, controls=controls)
            )


async def test_fable_replay_preserves_empty_thinking_and_rejects_changed_prefix_before_sdk() -> (
    None
):
    client = FakeAnthropicClient()
    native = [
        {"type": "thinking", "thinking": "", "signature": "opaque"},
        {"type": "text", "text": "done"},
    ]
    client.queue([], final_message(id_="one", content=native))
    provider = AnthropicMessagesProvider(client=client)
    first = TurnRequest(
        model="claude-fable-5-1", input="hi", controls=ModelRequestControls(instructions="stable")
    )
    events = [event async for event in provider.stream_turn(first)]
    state = events[-1].data["provider_state"]
    assert state.native_history[-1]["content"] == native
    assert state.tool_state["fable_5_1_prefix"]["model"] == "claude-fable-5-1"
    second = TurnRequest(
        model=first.model, input="next", provider_state=state, controls=first.controls
    )
    client.queue([], final_message(id_="two", content=native))
    second_events = [event async for event in provider.stream_turn(second)]
    assert len(client.messages.seen_kwargs) == 2
    assert second_events[-1].data["provider_state"].native_history[1]["content"] == native
    variants = [deepcopy(second) for _ in range(5)]
    variants[0].extra = {"extra_body": {"system": "changed"}}
    variants[1].tools = [{"name": "new", "input_schema": {"type": "object"}}]
    assert variants[2].provider_state is not None
    variants[2].provider_state.native_history[0]["content"] = "changed"
    variants[3].model = "claude-opus-4-6"
    assert variants[4].provider_state is not None
    variants[4].provider_state.tool_state.clear()
    for request in variants:
        with pytest.raises(UnsupportedFeatureError):
            _ = [event async for event in provider.stream_turn(request)]
    assert len(client.messages.seen_kwargs) == 2


def test_unknown_claude_and_explicit_hosted_versions_are_unchanged() -> None:
    provider = AnthropicMessagesProvider()
    assert (
        provider.capability_profile("claude-fable-99").output_strategies["provider_native"].status
        == "unsupported"
    )
    assert to_anthropic_tool(WebSearch(), model="claude-fable-99")["type"] == "web_search_20250305"
    assert (
        to_anthropic_tool(WebSearch(version="custom"), model="claude-fable-5")["type"] == "custom"
    )


@pytest.mark.parametrize("model", ["claude-opus-4-6", "claude-sonnet-5"])
async def test_non_fable_raw_messages_keep_original_replay_behavior(model: str) -> None:
    client = FakeAnthropicClient()
    client.queue([], final_message(id_="legacy", content=[{"type": "text", "text": "ok"}]))
    provider = AnthropicMessagesProvider(client=client)
    native_override = [{"role": "user", "content": "native"}]
    request = TurnRequest(
        model=model, input="original", extra={"extra_body": {"messages": native_override}}
    )
    events = [event async for event in provider.stream_turn(request)]
    assert client.messages.seen_kwargs[0]["extra_body"]["messages"] == native_override
    assert events[-1].data["provider_state"].native_history[0] == {
        "role": "user",
        "content": "original",
    }


@pytest.mark.parametrize(
    "parameter,value",
    [
        ("temperature", 1),
        ("top_p", 1),
        ("top_logprobs", 1),
        ("include", ["message.output_text.logprobs"]),
    ],
)
@pytest.mark.parametrize("surface", ["typed", "extra", "extra_body", "model_override"])
async def test_astra_restricted_parameters_fail_before_sdk(
    parameter: str, value: Any, surface: str
) -> None:
    from tests.fixtures.fake_openai_client import FakeOpenAIClient

    client = FakeOpenAIClient()
    provider = OpenAIResponsesProvider(client=client)
    request = TurnRequest(model="gpt-6-astra", input="hi")
    if surface == "typed" and parameter != "top_logprobs":
        setattr(request.controls, parameter, value)
    elif surface in {"extra_body", "model_override"}:
        request.extra = {"extra_body": {parameter: value}}
        if surface == "model_override":
            request.model = "gpt-5.4"
            request.extra["extra_body"]["model"] = "gpt-6-astra"
    else:
        request.extra = {parameter: value}
    with pytest.raises(UnsupportedFeatureError):
        _ = [event async for event in provider.stream_turn(request)]
    assert client.responses.seen_kwargs == []


async def test_astra_no_sampling_and_current_cache_reach_sdk() -> None:
    from blackbox.providers.base import ModelCacheControl
    from tests.fixtures.fake_openai_client import FakeOpenAIClient, final_response

    client = FakeOpenAIClient()
    client.queue([], final_response(id_="astra"))
    provider = OpenAIResponsesProvider(client=client)
    request = TurnRequest(
        model="gpt-6-astra",
        input="hi",
        controls=ModelRequestControls(cache=ModelCacheControl(ttl="30m")),
    )
    _ = [event async for event in provider.stream_turn(request)]
    assert client.responses.seen_kwargs[0]["prompt_cache_options"] == {"ttl": "30m"}
    assert "prompt_cache_retention" not in client.responses.seen_kwargs[0]
    profile = provider.capability_profile(request.model)
    assert profile.controls["temperature"].status == "unsupported"
    assert profile.controls["top_p"].status == "unsupported"


@pytest.mark.parametrize(
    "model", ["gpt-6-astra", "gpt-5.6-sol", "gpt-5.6", "gpt-5.6-terra", "gpt-5.6-luna"]
)
def test_current_cache_ttl_mapping_and_raw_precedence(model: str) -> None:
    from blackbox.providers.base import ModelCacheControl

    provider = OpenAIResponsesProvider()
    request = TurnRequest(
        model=model,
        input="hi",
        controls=ModelRequestControls(cache=ModelCacheControl(ttl="30m")),
        extra={"extra_body": {"prompt_cache_options": {"custom": True}}},
    )
    kwargs = provider._build_request_kwargs(request)
    assert kwargs["extra_body"]["prompt_cache_options"] == {"ttl": "30m", "custom": True}
    assert "prompt_cache_retention" not in kwargs
    profile = provider.capability_profile(model)
    assert profile.controls["cache_ttl"].native_name == "prompt_cache_options.ttl"
    assert profile.controls["cache_ttl"].supported_values == ("30m",)
    request.controls.cache = ModelCacheControl(ttl="24h")
    request.extra = {"prompt_cache_options": {"ttl": "30m", "custom": True}}
    assert provider._build_request_kwargs(request)["prompt_cache_options"] == {
        "ttl": "30m",
        "custom": True,
    }


@pytest.mark.parametrize("surface", ["typed", "extra", "extra_body", "retention", "model_override"])
async def test_current_invalid_cache_fails_before_sdk(surface: str) -> None:
    from blackbox.providers.base import ModelCacheControl
    from tests.fixtures.fake_openai_client import FakeOpenAIClient

    client = FakeOpenAIClient()
    provider = OpenAIResponsesProvider(client=client)
    request = TurnRequest(model="gpt-5.6-sol", input="hi")
    if surface == "typed":
        request.controls.cache = ModelCacheControl(ttl="24h")
    elif surface == "extra":
        request.extra = {"prompt_cache_options": {"ttl": "24h"}}
    elif surface == "retention":
        request.extra = {"extra_body": {"prompt_cache_retention": "30m"}}
    else:
        request.extra = {"extra_body": {"prompt_cache_options": {"ttl": "24h"}}}
        if surface == "model_override":
            request.model = "gpt-5.4"
            request.extra["extra_body"]["model"] = "gpt-6-astra"
    with pytest.raises(UnsupportedFeatureError):
        _ = [event async for event in provider.stream_turn(request)]
    assert client.responses.seen_kwargs == []


def test_cache_mapping_uses_effective_model_and_preserves_legacy() -> None:
    from blackbox.providers.base import ModelCacheControl

    provider = OpenAIResponsesProvider()
    request = TurnRequest(
        model="gpt-5.4",
        input="hi",
        controls=ModelRequestControls(cache=ModelCacheControl(ttl="24h")),
    )
    assert provider._build_request_kwargs(request)["prompt_cache_retention"] == "24h"
    request.model = "gpt-6-astra"
    request.extra = {"model": "gpt-5.4", "temperature": 0.5}
    kwargs = provider._build_request_kwargs(request)
    assert kwargs["prompt_cache_retention"] == "24h"
    assert kwargs["temperature"] == 0.5
    request.controls.cache = ModelCacheControl(ttl="30m")
    request.extra = {"model": "gpt-5.6-sol"}
    assert provider._build_request_kwargs(request)["prompt_cache_options"] == {"ttl": "30m"}
