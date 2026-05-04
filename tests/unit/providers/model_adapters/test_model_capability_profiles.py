from __future__ import annotations

from typing import Any, cast

import pytest

from blackbox.core.capabilities import (
    CapabilityDetail,
    CapabilityStatus,
    ModelCapabilities,
    ModelCapabilityProfile,
    capability_profile_from_dict,
    capability_profile_to_dict,
    clear_model_capability_profile_cache,
    derive_profile_from_summary,
    get_model_capability_profile,
)
from blackbox.core.errors import UnsupportedFeatureError
from blackbox.core.results import OutputSpec
from blackbox.hosted_tools import WebSearch, hosted_tool_kind
from blackbox.output.schema import build_output_schema
from blackbox.providers.base import (
    CompactionControl,
    ModelCacheControl,
    ModelRequestControls,
    ToolSearchControl,
    TurnRequest,
)
from blackbox.providers.model_adapters import capability_validation as validation_module
from blackbox.providers.model_adapters.anthropic_messages import AnthropicMessagesProvider
from blackbox.providers.model_adapters.capability_validation import (
    clear_capability_validation_cache,
    requested_controls,
    resolve_output_strategy,
    validate_turn_request_capabilities,
)
from blackbox.providers.model_adapters.echo import EchoModelProvider
from blackbox.providers.model_adapters.gemini_generate_content import (
    GeminiGenerateContentProvider,
)
from blackbox.providers.model_adapters.openai_responses import OpenAIResponsesProvider
from blackbox.providers.model_adapters.xai_responses import XAIResponsesProvider
from blackbox.providers.registry import ProviderRegistry
from blackbox.runtime import ModelRuntime


class CountingEchoModelProvider(EchoModelProvider):
    def __init__(self) -> None:
        super().__init__()
        self.profile_calls = 0

    def capability_profile(self, model: str | None = None) -> ModelCapabilityProfile:
        self.profile_calls += 1
        return super().capability_profile(model)


@pytest.mark.parametrize("status", ["supported", "conditional", "passthrough"])
def test_capability_detail_is_supported(status: str) -> None:
    assert CapabilityDetail(status=cast(CapabilityStatus, status)).is_supported is True


def test_derive_profile_from_summary_is_conservative() -> None:
    profile = derive_profile_from_summary(
        provider_id="legacy",
        model="model",
        summary=ModelCapabilities(
            supports_function_tools=True,
            supports_hosted_tools=True,
            supports_parallel_tool_calls=False,
            supports_structured_output=False,
        ),
    )

    assert profile.hosted_tools["raw"].status == "passthrough"
    assert profile.hosted_tools.get("web_search") is None
    assert profile.output_strategies["finalizer_tool"].status == "supported"
    assert profile.output_strategies["provider_native"].status == "unsupported"
    assert profile.controls["parallel_tool_calls"].status == "unsupported"


def test_hosted_tool_kind_web_search() -> None:
    assert hosted_tool_kind(WebSearch()) == "web_search"


def test_requested_controls_only_includes_explicit_fields() -> None:
    controls = ModelRequestControls(
        temperature=0.2,
        reasoning_effort="high",
        cache=ModelCacheControl(key="tenant", breakpoints=[{"after": 1}]),
        tool_search=ToolSearchControl(max_results=5),
        compaction=CompactionControl(strategy="auto"),
        modalities=["text"],
    )

    assert requested_controls(controls) == [
        "temperature",
        "reasoning_effort",
        "tool_search",
        "compaction",
        "modalities",
        "cache",
        "cache_key",
        "cache_breakpoints",
    ]


def test_resolve_output_strategy_applies_supported_fallback() -> None:
    profile = EchoModelProvider().capability_profile("echo-mini")

    assert (
        resolve_output_strategy(
            profile=profile,
            requested="provider_native",
            fallback="posthoc_parse",
        )
        == "posthoc_parse"
    )
    with pytest.raises(UnsupportedFeatureError):
        resolve_output_strategy(
            profile=profile,
            requested="provider_native",
            fallback="error",
        )


def test_capability_profile_serializes_round_trip() -> None:
    profile = EchoModelProvider().capability_profile("echo-mini")
    restored = capability_profile_from_dict(capability_profile_to_dict(profile))

    assert restored.provider == "echo"
    assert restored.model == "echo-mini"
    assert restored.state_modes["provider_stateful"].status == "supported"
    assert restored.summary.supports_provider_state is True


def test_model_runtime_capabilities_parses_provider_ref() -> None:
    registry = ProviderRegistry()
    registry.register_model(EchoModelProvider())
    runtime = ModelRuntime(registry)

    profile = runtime.capabilities("echo:echo-mini")

    assert profile.provider == "echo"
    assert profile.model == "echo-mini"


def test_model_capability_profiles_are_cached_by_provider_and_model() -> None:
    clear_model_capability_profile_cache()
    provider = CountingEchoModelProvider()

    first = get_model_capability_profile(provider, "echo-cached")
    second = get_model_capability_profile(provider, "echo-cached")
    other = get_model_capability_profile(provider, "echo-other")

    assert first is second
    assert other is not first
    assert provider.profile_calls == 2
    clear_model_capability_profile_cache()


def test_turn_capability_validation_reuses_successful_results(
    monkeypatch: Any,
) -> None:
    clear_model_capability_profile_cache()
    clear_capability_validation_cache()
    calls = 0
    original = validation_module.validate_capability_request

    def counting_validate(*args: Any, **kwargs: Any) -> None:
        nonlocal calls
        calls += 1
        original(*args, **kwargs)

    monkeypatch.setattr(validation_module, "validate_capability_request", counting_validate)
    provider = CountingEchoModelProvider()
    request = TurnRequest(model="echo-cached-validation", input="hello")

    validate_turn_request_capabilities(provider=provider, request=request)
    validate_turn_request_capabilities(provider=provider, request=request)

    assert calls == 1
    clear_model_capability_profile_cache()
    clear_capability_validation_cache()


def test_openai_profile_exposes_hardened_tool_and_control_surfaces() -> None:
    profile = OpenAIResponsesProvider(api_key="x").capability_profile("gpt-test")

    assert profile.hosted_tools["tool_search"].status == "supported"
    assert profile.hosted_tools["computer_use"].status == "supported"
    assert profile.hosted_tools["image_generation"].status == "supported"
    assert profile.hosted_tools["shell"].status == "supported"
    assert profile.controls["tool_search"].status == "supported"
    assert profile.controls["compaction"].status == "supported"
    assert profile.controls["compaction"].supported_values == ("auto", "disabled")
    assert profile.controls["modalities"].status == "unsupported"


def test_provider_profiles_expose_new_adapter_mappings() -> None:
    anthropic = AnthropicMessagesProvider(api_key="x").capability_profile("claude-test")
    gemini = GeminiGenerateContentProvider(api_key="x").capability_profile("gemini-test")
    xai = XAIResponsesProvider(api_key="x").capability_profile("grok-test")

    assert anthropic.hosted_tools["web_search"].status == "supported"
    assert anthropic.hosted_tools["bash"].status == "supported"
    assert anthropic.controls["reasoning_effort"].status == "supported"
    assert gemini.hosted_tools["code_interpreter"].status == "supported"
    assert gemini.hosted_tools["url_context"].status == "supported"
    assert gemini.controls["tool_choice"].status == "supported"
    assert gemini.controls["reasoning_effort"].status == "supported"
    assert xai.hosted_tools["web_search"].status == "supported"
    assert xai.output_strategies["provider_native"].status == "supported"
    assert xai.controls["instructions"].status == "supported"
    assert xai.controls["instructions"].native_name == "input.role=system"
    assert xai.summary.supports_structured_output is True


def test_anthropic_profile_is_model_version_specific() -> None:
    provider = AnthropicMessagesProvider(api_key="x")

    supported = provider.capability_profile("claude-sonnet-4-6-20260201")
    unsupported = provider.capability_profile("claude-3-5-sonnet-20241022")

    assert supported.summary.supports_structured_output is True
    assert supported.output_strategies["provider_native"].status == "supported"
    assert supported.controls["compaction"].status == "supported"
    assert supported.hosted_tools["memory"].status == "supported"
    assert supported.hosted_tools["text_editor"].status == "supported"
    assert unsupported.summary.supports_structured_output is False
    assert unsupported.output_strategies["provider_native"].status == "unsupported"
    assert unsupported.controls["compaction"].status == "unsupported"


def test_gemini_profile_gates_structured_output_tool_combinations_by_model() -> None:
    provider = GeminiGenerateContentProvider(api_key="x")

    gemini_3 = provider.capability_profile("gemini-3-flash-preview")
    gemini_25 = provider.capability_profile("gemini-2.5-flash")

    assert gemini_3.constraints == ()
    assert any(
        constraint.name == "gemini_structured_output_function_tools_requires_gemini_3"
        for constraint in gemini_25.constraints
    )


def test_build_output_schema_keeps_posthoc_runtime_strategy_available() -> None:
    schema = build_output_schema(OutputSpec(schema={"type": "object"}))

    assert schema is not None
