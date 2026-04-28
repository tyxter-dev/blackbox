from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal, Protocol, TypeAlias, runtime_checkable

from agent_runtime.core.errors import UnsupportedFeatureError
from agent_runtime.hosted_tools import (
    ApplyPatch,
    CodeInterpreter,
    ComputerUse,
    FileSearch,
    HostedToolRaw,
    HostedToolSpec,
    ImageGeneration,
    Memory,
    RemoteMCP,
    Shell,
    TextEditor,
    ToolSearch,
    URLContext,
    WebFetch,
    WebSearch,
)

CapabilityStatus: TypeAlias = Literal[
    "supported",
    "unsupported",
    "conditional",
    "passthrough",
]

CapabilitySource: TypeAlias = Literal[
    "static",
    "derived",
    "provider_discovered",
    "user_override",
]

HostedToolKind: TypeAlias = Literal[
    "web_search",
    "file_search",
    "code_interpreter",
    "remote_mcp",
    "tool_search",
    "computer_use",
    "image_generation",
    "web_fetch",
    "url_context",
    "bash",
    "shell",
    "apply_patch",
    "text_editor",
    "memory",
    "raw",
]

OutputStrategyKind: TypeAlias = Literal[
    "provider_native",
    "finalizer_tool",
    "posthoc_parse",
    "posthoc_parse_with_retry",
]

ControlName: TypeAlias = Literal[
    "instructions",
    "temperature",
    "top_p",
    "max_output_tokens",
    "tool_choice",
    "parallel_tool_calls",
    "reasoning_effort",
    "reasoning_summary",
    "verbosity",
    "cache",
    "cache_key",
    "cache_ttl",
    "cached_content",
    "cache_breakpoints",
    "background",
    "include",
    "store",
    "conversation",
    "modalities",
    "top_logprobs",
    "safety_settings",
    "tool_search",
    "compaction",
    "extra",
]

StateMode: TypeAlias = Literal[
    "none",
    "provider_stateful",
    "stateless_replay",
    "zdr",
    "encrypted_reasoning",
]


@dataclass(slots=True, frozen=True)
class HostedToolSupport:
    """How a provider supports one hosted tool kind.

    The flags distinguish request mapping, event mapping, provider-side
    execution, and runtime-side execution because providers often expose the
    same tool name with different ownership and observability guarantees.
    """

    tool_type: str
    request_mapping: bool
    event_mapping: bool
    provider_server_execution: bool
    runtime_client_execution: bool
    requires_handler: bool = False
    requires_approval_default: bool = False
    models: list[str] = field(default_factory=list)
    notes: str | None = None


@dataclass(slots=True, frozen=True)
class ModelCapabilities:
    """Feature flags for a model-level provider.

    ``supports_structured_output`` means the adapter accepts and enforces a
    provider-native schema contract. Runtime-level posthoc validation does not
    set this flag.
    """

    supports_streaming_events: bool = True
    supports_function_tools: bool = False
    supports_parallel_tool_calls: bool = False
    supports_hosted_tools: bool = False
    supports_tool_search: bool = False
    supports_client_executed_tool_search: bool = False
    supports_remote_mcp: bool = False
    supports_reasoning_items: bool = False
    supports_provider_state: bool = False
    supports_structured_output: bool = False
    hosted_tools: dict[str, HostedToolSupport] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class CapabilityDetail:
    """Granular support information for one provider capability.

    ``supported`` means the adapter can honor the feature natively.
    ``conditional`` means support depends on another setting or model.
    ``passthrough`` means the runtime can send provider-specific data but
    cannot validate the behavior uniformly.
    """

    status: CapabilityStatus
    reason: str | None = None
    native_name: str | None = None
    supported_values: tuple[Any, ...] = ()
    requires: tuple[str, ...] = ()
    incompatible_with: tuple[str, ...] = ()
    notes: str | None = None
    source: CapabilitySource = "static"
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def is_supported(self) -> bool:
        return self.status in {"supported", "conditional", "passthrough"}


@dataclass(slots=True, frozen=True)
class CapabilityConstraint:
    """Cross-feature rule that can change or block capability support.

    Constraints describe requirements like "finalizer tools require function
    tools" or "this hosted tool is unavailable with a given output strategy."
    They are used for validation and diagnostics, not as prompt text.
    """

    name: str
    status: CapabilityStatus
    reason: str
    hosted_tools_any: tuple[HostedToolKind, ...] = ()
    output_strategies_any: tuple[OutputStrategyKind, ...] = ()
    controls_any: tuple[ControlName, ...] = ()
    state_modes_any: tuple[StateMode, ...] = ()
    has_function_tools: bool | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class ModelCapabilityProfile:
    """Provider/model feature profile used by planning and adapters.

    ``summary`` keeps the broad legacy feature flags, while the typed maps
    explain support for specific hosted tools, output strategies, controls, and
    state modes. Runtime planning should prefer these granular details when
    deciding whether to map, reject, or pass through a requested feature.
    """

    provider: str
    model: str | None = None
    hosted_tools: dict[HostedToolKind, CapabilityDetail] = field(default_factory=dict)
    output_strategies: dict[OutputStrategyKind, CapabilityDetail] = field(default_factory=dict)
    controls: dict[ControlName, CapabilityDetail] = field(default_factory=dict)
    state_modes: dict[StateMode, CapabilityDetail] = field(default_factory=dict)
    constraints: tuple[CapabilityConstraint, ...] = ()
    summary: ModelCapabilities = field(default_factory=ModelCapabilities)
    source: CapabilitySource = "static"
    metadata: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class GranularModelCapabilityProvider(Protocol):
    """Provider protocol for adapters that expose detailed capability profiles."""

    @property
    def provider_id(self) -> str:
        ...

    def capabilities(self, model: str | None = None) -> ModelCapabilities:
        ...

    def capability_profile(self, model: str | None = None) -> ModelCapabilityProfile:
        ...


@dataclass(slots=True, frozen=True)
class AgentCapabilities:
    """Feature flags for a cloud/local agent provider."""

    supports_sessions: bool = True
    supports_streaming_events: bool = True
    supports_artifacts: bool = False
    supports_workspace: bool = False
    supports_approvals: bool = False
    supports_mcp: bool = False
    supports_deployments: bool = False
    supports_evals: bool = False
    supports_cancellation: bool = False
    supports_resume: bool = False


def get_model_capability_profile(
    provider: Any,
    model: str | None = None,
) -> ModelCapabilityProfile:
    if isinstance(provider, GranularModelCapabilityProvider):
        return provider.capability_profile(model)
    return derive_profile_from_summary(
        provider_id=str(provider.provider_id),
        model=model,
        summary=provider.capabilities(model),
    )


def derive_profile_from_summary(
    *,
    provider_id: str,
    model: str | None,
    summary: ModelCapabilities,
) -> ModelCapabilityProfile:
    hosted_tools: dict[HostedToolKind, CapabilityDetail] = {
        "raw": CapabilityDetail(
            status="passthrough" if summary.supports_hosted_tools else "unsupported",
            reason="Derived from supports_hosted_tools.",
            source="derived",
        )
    }

    output_strategies: dict[OutputStrategyKind, CapabilityDetail] = {
        "posthoc_parse": CapabilityDetail(status="supported", source="derived"),
        "posthoc_parse_with_retry": CapabilityDetail(status="supported", source="derived"),
        "finalizer_tool": CapabilityDetail(
            status="supported" if summary.supports_function_tools else "unsupported",
            reason="Finalizer tool requires function tools.",
            source="derived",
        ),
        "provider_native": CapabilityDetail(
            status="supported" if summary.supports_structured_output else "unsupported",
            reason="Derived from supports_structured_output.",
            source="derived",
        ),
    }

    controls: dict[ControlName, CapabilityDetail] = {
        "instructions": CapabilityDetail(status="conditional", source="derived"),
        "temperature": CapabilityDetail(status="conditional", source="derived"),
        "top_p": CapabilityDetail(status="conditional", source="derived"),
        "max_output_tokens": CapabilityDetail(status="conditional", source="derived"),
        "tool_choice": CapabilityDetail(
            status="conditional" if summary.supports_function_tools else "unsupported",
            source="derived",
        ),
        "parallel_tool_calls": CapabilityDetail(
            status="supported" if summary.supports_parallel_tool_calls else "unsupported",
            source="derived",
        ),
        "reasoning_effort": CapabilityDetail(
            status="conditional" if summary.supports_reasoning_items else "unsupported",
            source="derived",
        ),
        "extra": CapabilityDetail(status="passthrough", source="derived"),
    }

    state_modes: dict[StateMode, CapabilityDetail] = {
        "none": CapabilityDetail(status="supported", source="derived"),
        "provider_stateful": CapabilityDetail(
            status="conditional" if summary.supports_provider_state else "unsupported",
            source="derived",
        ),
        "stateless_replay": CapabilityDetail(
            status="conditional" if summary.supports_provider_state else "unsupported",
            source="derived",
        ),
        "zdr": CapabilityDetail(status="unsupported", source="derived"),
        "encrypted_reasoning": CapabilityDetail(status="unsupported", source="derived"),
    }

    return ModelCapabilityProfile(
        provider=provider_id,
        model=model,
        hosted_tools=hosted_tools,
        output_strategies=output_strategies,
        controls=controls,
        state_modes=state_modes,
        summary=summary,
        source="derived",
    )


def capability_profile_to_dict(profile: ModelCapabilityProfile) -> dict[str, Any]:
    return asdict(profile)


def capability_profile_from_dict(data: dict[str, Any]) -> ModelCapabilityProfile:
    summary_data = data.get("summary") or {}
    hosted_support = {
        key: HostedToolSupport(**value)
        for key, value in dict(summary_data.get("hosted_tools") or {}).items()
    }
    summary = ModelCapabilities(
        **{**dict(summary_data), "hosted_tools": hosted_support}
    )
    hosted_tools = {
        key: _capability_detail_from_dict(value)
        for key, value in dict(data.get("hosted_tools") or {}).items()
    }
    output_strategies = {
        key: _capability_detail_from_dict(value)
        for key, value in dict(data.get("output_strategies") or {}).items()
    }
    controls = {
        key: _capability_detail_from_dict(value)
        for key, value in dict(data.get("controls") or {}).items()
    }
    state_modes = {
        key: _capability_detail_from_dict(value)
        for key, value in dict(data.get("state_modes") or {}).items()
    }
    constraints = tuple(
        CapabilityConstraint(
            **{
                **dict(value),
                "hosted_tools_any": tuple(value.get("hosted_tools_any") or ()),
                "output_strategies_any": tuple(value.get("output_strategies_any") or ()),
                "controls_any": tuple(value.get("controls_any") or ()),
                "state_modes_any": tuple(value.get("state_modes_any") or ()),
                "has_function_tools": value.get("has_function_tools"),
            }
        )
        for value in data.get("constraints") or ()
        if isinstance(value, dict)
    )
    return ModelCapabilityProfile(
        provider=str(data["provider"]),
        model=data.get("model"),
        hosted_tools=hosted_tools,
        output_strategies=output_strategies,
        controls=controls,
        state_modes=state_modes,
        constraints=constraints,
        summary=summary,
        source=data.get("source", "static"),
        metadata=dict(data.get("metadata") or {}),
    )


def _capability_detail_from_dict(data: Any) -> CapabilityDetail:
    value = dict(data)
    return CapabilityDetail(
        **{
            **value,
            "supported_values": tuple(value.get("supported_values") or ()),
            "requires": tuple(value.get("requires") or ()),
            "incompatible_with": tuple(value.get("incompatible_with") or ()),
            "metadata": dict(value.get("metadata") or {}),
        }
    )


def hosted_tool_type(spec: HostedToolSpec) -> str:
    if isinstance(spec, WebSearch):
        return "web_search"
    if isinstance(spec, WebFetch):
        return "web_fetch"
    if isinstance(spec, URLContext):
        return "url_context"
    if isinstance(spec, FileSearch):
        return "file_search"
    if isinstance(spec, CodeInterpreter):
        return "code_interpreter"
    if isinstance(spec, Shell):
        return "shell"
    if isinstance(spec, ApplyPatch):
        return "apply_patch"
    if isinstance(spec, ComputerUse):
        return "computer_use"
    if isinstance(spec, TextEditor):
        return "text_editor"
    if isinstance(spec, Memory):
        return "memory"
    if isinstance(spec, ImageGeneration):
        return "image_generation"
    if isinstance(spec, ToolSearch):
        return "tool_search"
    if isinstance(spec, RemoteMCP):
        return "mcp"
    if isinstance(spec, HostedToolRaw):
        return "raw"
    return type(spec).__name__


def validate_hosted_tools(
    capabilities: ModelCapabilities,
    specs: list[HostedToolSpec],
    *,
    provider: str,
) -> None:
    if not specs:
        return
    if not capabilities.supports_hosted_tools:
        raise UnsupportedFeatureError(f"Provider '{provider}' does not support hosted tools.")
    for spec in specs:
        if isinstance(spec, HostedToolRaw):
            continue
        tool_type = hosted_tool_type(spec)
        if tool_type not in capabilities.hosted_tools:
            raise UnsupportedFeatureError(
                f"Provider '{provider}' does not support hosted tool '{tool_type}'."
            )
