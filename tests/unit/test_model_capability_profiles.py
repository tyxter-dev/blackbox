from __future__ import annotations

from typing import cast

import pytest

from agent_runtime.core.capabilities import (
    CapabilityDetail,
    CapabilityStatus,
    ModelCapabilities,
    capability_profile_from_dict,
    capability_profile_to_dict,
    derive_profile_from_summary,
)
from agent_runtime.core.errors import UnsupportedFeatureError
from agent_runtime.core.results import OutputSpec
from agent_runtime.hosted_tools import WebSearch, hosted_tool_kind
from agent_runtime.models.capability_validation import (
    requested_controls,
    resolve_output_strategy,
)
from agent_runtime.models.echo import EchoModelProvider
from agent_runtime.output.schema import build_output_schema
from agent_runtime.providers.base import ModelCacheControl, ModelRequestControls
from agent_runtime.providers.registry import ProviderRegistry
from agent_runtime.runtime import ModelRuntime


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
    )

    assert requested_controls(controls) == [
        "temperature",
        "reasoning_effort",
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


def test_build_output_schema_keeps_posthoc_runtime_strategy_available() -> None:
    schema = build_output_schema(OutputSpec(schema={"type": "object"}))

    assert schema is not None
