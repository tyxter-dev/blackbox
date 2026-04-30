from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

from blackbox.core.capabilities import (
    CapabilityConstraint,
    ControlName,
    HostedToolKind,
    ModelCapabilityProfile,
    OutputStrategyKind,
    StateMode,
    get_model_capability_profile,
)
from blackbox.core.errors import UnsupportedFeatureError
from blackbox.core.results import OutputFallback
from blackbox.providers.base import ModelProvider, ModelRequestControls, TurnRequest
from blackbox.tools.hosted.specs import hosted_tool_kind


@dataclass(slots=True, frozen=True)
class ModelCapabilityRequest:
    model: str
    hosted_tools: tuple[HostedToolKind, ...] = ()
    output_strategy: OutputStrategyKind | None = None
    controls: tuple[ControlName, ...] = ()
    state_mode: StateMode | None = None
    has_function_tools: bool = False
    control_values: dict[ControlName, object] | None = None


_VALIDATED_CAPABILITY_REQUESTS: set[tuple[int, tuple[Any, ...], OutputFallback | None]] = set()


def validate_turn_request_capabilities(
    *,
    provider: ModelProvider,
    request: TurnRequest,
    output_fallback: OutputFallback | None = None,
) -> None:
    profile = get_model_capability_profile(provider, request.model)
    requirement = capability_request_from_turn(request)
    cache_key = (id(profile), _request_cache_key(requirement), output_fallback)
    if cache_key in _VALIDATED_CAPABILITY_REQUESTS:
        return
    validate_capability_request(profile, requirement, output_fallback=output_fallback)
    _VALIDATED_CAPABILITY_REQUESTS.add(cache_key)


def clear_capability_validation_cache() -> None:
    """Clear successful capability validation cache entries."""

    _VALIDATED_CAPABILITY_REQUESTS.clear()


def capability_request_from_turn(request: TurnRequest) -> ModelCapabilityRequest:
    controls = requested_controls(request.controls)
    if request.extra:
        controls.append("extra")
    return ModelCapabilityRequest(
        model=request.model,
        hosted_tools=tuple(
            cast(HostedToolKind, hosted_tool_kind(spec)) for spec in request.hosted_tools
        ),
        output_strategy=request.output_strategy,
        controls=tuple(controls),
        state_mode=request.controls.state_mode,
        has_function_tools=bool(request.tools),
        control_values=_control_values(request.controls),
    )


def requested_controls(controls: ModelRequestControls) -> list[ControlName]:
    """Return the capability control names explicitly requested by runtime controls."""
    requested: list[ControlName] = []

    if controls.instructions is not None:
        requested.append("instructions")
    if controls.temperature is not None:
        requested.append("temperature")
    if controls.top_p is not None:
        requested.append("top_p")
    if controls.max_output_tokens is not None:
        requested.append("max_output_tokens")
    if controls.tool_choice is not None:
        requested.append("tool_choice")
    if controls.parallel_tool_calls is not None:
        requested.append("parallel_tool_calls")
    if controls.reasoning_effort is not None:
        requested.append("reasoning_effort")
    if controls.reasoning_summary is not None:
        requested.append("reasoning_summary")
    if controls.verbosity is not None:
        requested.append("verbosity")
    if controls.background is not None:
        requested.append("background")
    if controls.store is not None:
        requested.append("store")
    if controls.include is not None:
        requested.append("include")
    if controls.tool_search is not None:
        requested.append("tool_search")
    if controls.compaction is not None:
        requested.append("compaction")
    if controls.modalities is not None:
        requested.append("modalities")
    if controls.cache is not None:
        requested.append("cache")
        if controls.cache.key is not None:
            requested.append("cache_key")
        if controls.cache.ttl is not None:
            requested.append("cache_ttl")
        if controls.cache.cached_content is not None:
            requested.append("cached_content")
        if controls.cache.breakpoints:
            requested.append("cache_breakpoints")

    return requested


def validate_capability_request(
    profile: ModelCapabilityProfile,
    requirement: ModelCapabilityRequest,
    *,
    output_fallback: OutputFallback | None = None,
) -> None:
    """Raise when a requested model capability is unsupported by the profile."""
    for kind in requirement.hosted_tools:
        detail = profile.hosted_tools.get(kind)
        if detail is None or detail.status == "unsupported":
            raise UnsupportedFeatureError(
                f"{profile.provider}:{requirement.model} does not support hosted tool {kind}."
            )
        if detail.status == "passthrough" and kind != "raw":
            raise UnsupportedFeatureError(
                f"{kind} is not typed for {profile.provider}; use HostedToolRaw for explicit "
                "passthrough."
            )

    if requirement.output_strategy is not None:
        resolve_output_strategy(
            profile=profile,
            requested=requirement.output_strategy,
            fallback=output_fallback or "error",
        )

    for control in requirement.controls:
        detail = profile.controls.get(control)
        if detail is None or detail.status == "unsupported":
            raise UnsupportedFeatureError(
                f"{profile.provider}:{requirement.model} does not support {control}."
            )
        if detail.status == "passthrough" and control != "extra":
            raise UnsupportedFeatureError(
                f"{control} is only available as provider-native passthrough for "
                f"{profile.provider}."
            )
        value = (requirement.control_values or {}).get(control)
        if detail.supported_values and value not in detail.supported_values:
            raise UnsupportedFeatureError(
                f"Unsupported {control}={value!r}; supported values: "
                f"{detail.supported_values!r}."
            )

    if requirement.state_mode is not None:
        detail = profile.state_modes.get(requirement.state_mode)
        if detail is None or detail.status == "unsupported":
            raise UnsupportedFeatureError(
                f"{profile.provider}:{requirement.model} does not support state mode "
                f"{requirement.state_mode}."
            )

    for constraint in profile.constraints:
        if _constraint_matches(constraint, requirement) and constraint.status == "unsupported":
            raise UnsupportedFeatureError(constraint.reason)


def resolve_output_strategy(
    *,
    profile: ModelCapabilityProfile,
    requested: OutputStrategyKind,
    fallback: OutputFallback,
) -> OutputStrategyKind:
    """Return the requested output strategy or a supported fallback."""
    detail = profile.output_strategies.get(requested)
    if detail is not None and detail.is_supported:
        return requested

    if fallback == "error":
        if requested == "provider_native":
            raise UnsupportedFeatureError(
                f"{profile.provider}:{profile.model or '<default>'} does not support "
                "provider-native structured output."
            )
        raise UnsupportedFeatureError(
            f"{profile.provider}:{profile.model or '<default>'} does not support output "
            f"strategy {requested}."
        )

    fallback_detail = profile.output_strategies.get(fallback)
    if fallback_detail is not None and fallback_detail.is_supported:
        return cast(OutputStrategyKind, fallback)

    raise UnsupportedFeatureError(
        f"{profile.provider}:{profile.model or '<default>'} does not support output strategy "
        f"{requested}, and fallback {fallback} is unavailable."
    )


def _control_values(controls: ModelRequestControls) -> dict[ControlName, object]:
    values: dict[ControlName, object] = {}
    for name in requested_controls(controls):
        if name == "cache":
            values[name] = controls.cache
        elif name == "cache_key":
            values[name] = controls.cache.key if controls.cache else None
        elif name == "cache_ttl":
            values[name] = controls.cache.ttl if controls.cache else None
        elif name == "cached_content":
            values[name] = controls.cache.cached_content if controls.cache else None
        elif name == "cache_breakpoints":
            values[name] = tuple(controls.cache.breakpoints) if controls.cache else ()
        elif name == "compaction":
            values[name] = controls.compaction.strategy if controls.compaction else None
        else:
            values[name] = getattr(controls, name)
    return values


def _constraint_matches(
    constraint: CapabilityConstraint,
    requirement: ModelCapabilityRequest,
) -> bool:
    if constraint.hosted_tools_any and not set(constraint.hosted_tools_any).intersection(
        requirement.hosted_tools
    ):
        return False
    if (
        constraint.output_strategies_any
        and requirement.output_strategy not in constraint.output_strategies_any
    ):
        return False
    if constraint.controls_any and not set(constraint.controls_any).intersection(
        requirement.controls
    ):
        return False
    if constraint.state_modes_any and requirement.state_mode not in constraint.state_modes_any:
        return False
    if (
        constraint.has_function_tools is not None
        and constraint.has_function_tools != requirement.has_function_tools
    ):
        return False
    return True


def _request_cache_key(requirement: ModelCapabilityRequest) -> tuple[Any, ...]:
    return (
        requirement.model,
        requirement.hosted_tools,
        requirement.output_strategy,
        requirement.controls,
        requirement.state_mode,
        requirement.has_function_tools,
        tuple(
            sorted(
                (key, _cacheable_value(value))
                for key, value in (requirement.control_values or {}).items()
            )
        ),
    )


def _cacheable_value(value: object) -> object:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, list | tuple):
        return tuple(_cacheable_value(item) for item in value)
    if isinstance(value, set | frozenset):
        return tuple(sorted(repr(_cacheable_value(item)) for item in value))
    if isinstance(value, dict):
        return tuple(
            sorted((str(key), _cacheable_value(item)) for key, item in value.items())
        )
    return repr(value)
