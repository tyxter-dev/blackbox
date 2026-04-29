from __future__ import annotations

import copy
import json
import os
import tomllib
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from importlib import import_module
from pathlib import Path
from typing import Any, Literal

from agent_runtime.core.errors import ConfigurationError
from agent_runtime.providers.registry import ProviderRef

WorkflowSurface = Literal["model", "runtime", "agent_session", "realtime"]
WorkflowProfileName = Literal[
    "fast_text",
    "structured_extraction",
    "tool_agent",
    "retrieval_agent",
    "coding_agent",
    "cloud_agent_session",
    "realtime_voice",
    "eval_run",
    "cost_sensitive",
    "high_reliability",
]


@dataclass(slots=True, frozen=True)
class RequiredConfigValue:
    """A profile requirement satisfied when any listed config key is present."""

    any_of: tuple[str, ...]
    reason: str


@dataclass(slots=True, frozen=True)
class WorkflowProfile:
    """Documented workflow preset that expands into normal runtime arguments."""

    name: WorkflowProfileName
    description: str
    tradeoffs: str
    surfaces: tuple[WorkflowSurface, ...]
    defaults: Mapping[str, Any] = field(default_factory=dict)
    surface_defaults: Mapping[WorkflowSurface, Mapping[str, Any]] = field(
        default_factory=dict
    )
    required: tuple[RequiredConfigValue, ...] = ()
    required_capabilities: tuple[str, ...] = ()

    def defaults_for(self, surface: WorkflowSurface | None = None) -> dict[str, Any]:
        values = copy.deepcopy(dict(self.defaults))
        if surface is not None:
            values.update(copy.deepcopy(dict(self.surface_defaults.get(surface, {}))))
        return values


@dataclass(slots=True, frozen=True)
class RuntimeConfig:
    """Typed workflow configuration that can be passed to existing runtime APIs.

    ``RuntimeConfig`` is intentionally a value object. It does not execute work
    and it does not introduce a new facade; runtime methods expand it into the
    same keyword arguments they already accept.
    """

    profile_name: str | None = None
    overrides: Mapping[str, Any] = field(default_factory=dict)
    source: str | None = None

    @classmethod
    def profile(cls, name: WorkflowProfileName | str) -> RuntimeConfig:
        profile = get_workflow_profile(name)
        return cls(profile_name=profile.name)

    @classmethod
    def from_mapping(
        cls,
        values: Mapping[str, Any],
        *,
        source: str | None = None,
    ) -> RuntimeConfig:
        data = dict(values)
        override_block = data.pop("overrides", None)
        if override_block is not None and not isinstance(override_block, Mapping):
            raise ConfigurationError("RuntimeConfig 'overrides' must be a mapping.")
        profile_name = _optional_str(data.pop("profile", data.pop("profile_name", None)))
        effective_overrides = dict(data)
        if isinstance(override_block, Mapping):
            effective_overrides.update(dict(override_block))
        if profile_name is not None:
            get_workflow_profile(profile_name)
        return cls(
            profile_name=profile_name,
            overrides=effective_overrides,
            source=source,
        )

    @classmethod
    def from_env(
        cls,
        *,
        prefix: str = "AGENT_RUNTIME_",
        environ: Mapping[str, str] | None = None,
    ) -> RuntimeConfig:
        env = environ if environ is not None else os.environ
        overrides: dict[str, Any] = {}
        profile_name: str | None = None
        profile_key = f"{prefix}PROFILE"
        if profile_key in env:
            profile_name = env[profile_key]
            get_workflow_profile(profile_name)

        for suffix, path, parser in _ENV_FIELDS:
            key = f"{prefix}{suffix}"
            if key not in env:
                continue
            _set_path(overrides, path, parser(env[key]))

        return cls(profile_name=profile_name, overrides=overrides, source="env")

    @classmethod
    def from_file(cls, path: str | Path) -> RuntimeConfig:
        config_path = Path(path)
        suffix = config_path.suffix.lower()
        if suffix == ".json":
            payload = json.loads(config_path.read_text(encoding="utf-8"))
        elif suffix == ".toml":
            payload = tomllib.loads(config_path.read_text(encoding="utf-8"))
        elif suffix in {".yaml", ".yml"}:
            payload = _load_yaml(config_path)
        else:
            raise ConfigurationError(
                f"Unsupported runtime config file extension {suffix!r}."
            )
        if not isinstance(payload, Mapping):
            raise ConfigurationError("Runtime config file must contain a mapping.")
        return cls.from_mapping(payload, source=str(config_path))

    def with_overrides(self, **overrides: Any) -> RuntimeConfig:
        merged = dict(self.overrides)
        merged.update(overrides)
        return RuntimeConfig(
            profile_name=self.profile_name,
            overrides=merged,
            source=self.source,
        )

    def validate(self, *, surface: WorkflowSurface | None = None) -> None:
        if self.profile_name is None:
            return
        profile = get_workflow_profile(self.profile_name)
        if surface is not None and surface not in profile.surfaces:
            raise ConfigurationError(
                f"Workflow profile {profile.name!r} cannot be used with the "
                f"{surface!r} runtime surface."
            )
        values = self._merged_values(surface=surface)
        for requirement in profile.required:
            if not any(_has_value(values, key) for key in requirement.any_of):
                choices = ", ".join(requirement.any_of)
                raise ConfigurationError(
                    f"Workflow profile {profile.name!r} requires one of "
                    f"{choices}: {requirement.reason}"
                )

    def to_kwargs(self, *, surface: WorkflowSurface | None = None) -> dict[str, Any]:
        self.validate(surface=surface)
        values = self._merged_values(surface=surface)
        _normalize_provider_model(values)
        return values

    def describe(self) -> dict[str, Any]:
        profile = get_workflow_profile(self.profile_name) if self.profile_name else None
        return {
            "profile": self.profile_name,
            "source": self.source,
            "overrides": copy.deepcopy(dict(self.overrides)),
            "description": profile.description if profile is not None else None,
            "tradeoffs": profile.tradeoffs if profile is not None else None,
        }

    def _merged_values(self, *, surface: WorkflowSurface | None) -> dict[str, Any]:
        values: dict[str, Any] = {}
        if self.profile_name is not None:
            values.update(get_workflow_profile(self.profile_name).defaults_for(surface))
        values.update(copy.deepcopy(dict(self.overrides)))
        return values


def get_workflow_profile(name: WorkflowProfileName | str) -> WorkflowProfile:
    try:
        return _PROFILES[str(name)]
    except KeyError as exc:
        known = ", ".join(sorted(_PROFILES))
        raise ConfigurationError(
            f"Unknown workflow profile {name!r}. Known profiles: {known}."
        ) from exc


def workflow_profiles() -> tuple[WorkflowProfile, ...]:
    return tuple(_PROFILES[name] for name in _PROFILE_ORDER)


def workflow_profile_docs() -> dict[str, dict[str, Any]]:
    return {
        profile.name: {
            "description": profile.description,
            "tradeoffs": profile.tradeoffs,
            "surfaces": list(profile.surfaces),
            "defaults": profile.defaults_for(),
            "surface_defaults": {
                surface: dict(defaults)
                for surface, defaults in profile.surface_defaults.items()
            },
            "required": [
                {"any_of": list(item.any_of), "reason": item.reason}
                for item in profile.required
            ],
            "required_capabilities": list(profile.required_capabilities),
        }
        for profile in workflow_profiles()
    }


def _load_yaml(path: Path) -> Any:
    try:
        yaml = import_module("yaml")
    except ImportError as exc:  # pragma: no cover - depends on optional extra
        raise ConfigurationError(
            "YAML runtime config files require the optional PyYAML dependency."
        ) from exc
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def _normalize_provider_model(values: dict[str, Any]) -> None:
    provider = values.get("provider")
    model = values.get("model")
    if not isinstance(model, str) or ":" not in model:
        return
    model_ref = ProviderRef.parse(model)
    if provider is None:
        values["provider"] = model
        values.pop("model", None)
        return
    provider_ref = ProviderRef.parse(str(provider))
    if provider_ref.provider_key != model_ref.provider_key:
        raise ConfigurationError(
            f"Configured provider {provider_ref.provider_key!r} does not match "
            f"provider-qualified model {model!r}."
        )
    values["provider"] = model
    values.pop("model", None)


def _has_value(values: Mapping[str, Any], path: str) -> bool:
    current: Any = values
    for part in path.split("."):
        if isinstance(current, Mapping):
            if part not in current:
                return False
            current = current[part]
            continue
        current = getattr(current, part, None)
        if current is None:
            return False
    return current is not None


def _set_path(values: dict[str, Any], path: tuple[str, ...], value: Any) -> None:
    current = values
    for part in path[:-1]:
        nested = current.setdefault(part, {})
        if not isinstance(nested, dict):
            raise ConfigurationError(f"Cannot merge env config into {'.'.join(path)}.")
        current = nested
    current[path[-1]] = value


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _parse_bool(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ConfigurationError(f"Invalid boolean value {value!r}.")


def _parse_int(value: str) -> int:
    return int(value.strip())


def _parse_float(value: str) -> float:
    return float(value.strip())


def _parse_json(value: str) -> Any:
    return json.loads(value)


def _parse_csv(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


def _parse_string(value: str) -> str:
    return value


EnvParser = Callable[[str], Any]
_ENV_FIELDS: tuple[tuple[str, tuple[str, ...], EnvParser], ...] = (
    ("PROVIDER", ("provider",), _parse_string),
    ("MODEL", ("model",), _parse_string),
    ("INSTRUCTIONS", ("instructions",), _parse_string),
    ("PROMPT_MODE", ("prompt_mode",), _parse_string),
    ("CHANNEL", ("channel",), _parse_string),
    ("TEMPERATURE", ("temperature",), _parse_float),
    ("TOP_P", ("top_p",), _parse_float),
    ("MAX_OUTPUT_TOKENS", ("max_output_tokens",), _parse_int),
    ("MAX_ITERATIONS", ("max_iterations",), _parse_int),
    ("TOOL_SELECTION", ("tool_selection",), _parse_string),
    ("TOOLS", ("tools",), _parse_csv),
    ("TOOLS_JSON", ("tools",), _parse_json),
    ("HOSTED_TOOLS_JSON", ("hosted_tools",), _parse_json),
    ("TOOLSETS_JSON", ("toolsets",), _parse_json),
    ("TOOL_BUDGET_JSON", ("tool_budget",), _parse_json),
    ("TOOL_ROUTING_JSON", ("tool_routing",), _parse_json),
    ("TOOL_CHOICE_JSON", ("tool_choice",), _parse_json),
    ("PARALLEL_TOOL_CALLS", ("parallel_tool_calls",), _parse_bool),
    ("REASONING_EFFORT", ("reasoning_effort",), _parse_string),
    ("VERBOSITY", ("verbosity",), _parse_string),
    ("STATE_MODE", ("state_mode",), _parse_string),
    ("BACKGROUND", ("background",), _parse_bool),
    ("STORE", ("store",), _parse_bool),
    ("INCLUDE", ("include",), _parse_csv),
    ("MODALITIES", ("modalities",), _parse_csv),
    ("CACHE_JSON", ("cache",), _parse_json),
    ("CACHE_STRATEGY", ("cache", "strategy"), _parse_string),
    ("CACHE_KEY", ("cache", "key"), _parse_string),
    ("CACHE_TTL", ("cache", "ttl"), _parse_string),
    ("TOOL_SEARCH_JSON", ("tool_search",), _parse_json),
    ("TOOL_SEARCH_ENABLED", ("tool_search", "enabled"), _parse_bool),
    ("TOOL_SEARCH_MAX_RESULTS", ("tool_search", "max_results"), _parse_int),
    ("COMPACTION_JSON", ("compaction",), _parse_json),
    ("COMPACTION_STRATEGY", ("compaction", "strategy"), _parse_string),
    ("WORKSPACE_JSON", ("workspace",), _parse_json),
    ("WORKSPACE_PROVIDER", ("workspace_provider",), _parse_string),
    ("OUTPUT_SPEC_JSON", ("output_spec",), _parse_json),
    ("OUTPUT_STRATEGY", ("output_strategy",), _parse_string),
    ("OUTPUT_SCHEMA_JSON", ("output_schema",), _parse_json),
    ("APPROVAL_POLICY", ("approval_policy",), _parse_string),
    ("POLICY", ("policy",), _parse_string),
    ("DATA_SOURCES_JSON", ("data_sources",), _parse_json),
    ("CONTEXT_FLAGS", ("context_flags",), _parse_csv),
    ("TRANSPORT", ("transport",), _parse_string),
    ("TOOL_MODE", ("tool_mode",), _parse_string),
    ("REALTIME_SESSION_JSON", ("realtime_session",), _parse_json),
)


_PROFILE_ORDER: tuple[WorkflowProfileName, ...] = (
    "fast_text",
    "structured_extraction",
    "tool_agent",
    "retrieval_agent",
    "coding_agent",
    "cloud_agent_session",
    "realtime_voice",
    "eval_run",
    "cost_sensitive",
    "high_reliability",
)

_PROFILES: dict[str, WorkflowProfile] = {
    "fast_text": WorkflowProfile(
        name="fast_text",
        description="Short text generation with minimal latency-oriented controls.",
        tradeoffs="Keeps token budget and sampling low; not intended for tools, long reasoning, or strict schemas.",
        surfaces=("model", "runtime"),
        defaults={"temperature": 0.2, "max_output_tokens": 512},
        surface_defaults={"runtime": {"max_iterations": 1}},
    ),
    "structured_extraction": WorkflowProfile(
        name="structured_extraction",
        description="Deterministic structured data extraction using an explicit schema.",
        tradeoffs="Requires a schema and prefers strict output over creativity; provider-native enforcement may fall back only when configured by the output spec.",
        surfaces=("model", "runtime"),
        defaults={"temperature": 0.0},
        surface_defaults={"model": {"output_strategy": "provider_native"}},
        required=(
            RequiredConfigValue(
                any_of=("output_spec", "output_schema", "output_type"),
                reason="structured extraction needs a target schema.",
            ),
        ),
        required_capabilities=("structured_output",),
    ),
    "tool_agent": WorkflowProfile(
        name="tool_agent",
        description="General tool-using agent loop with bounded dynamic tool exposure.",
        tradeoffs="Enables more orchestration than text-only runs; providers without function tools or parallel tool controls may reject the request.",
        surfaces=("runtime",),
        defaults={"parallel_tool_calls": True},
        surface_defaults={
            "runtime": {
                "tool_selection": "auto",
                "tool_budget": {
                    "max_tools_visible": 16,
                    "max_parallel_calls": 4,
                },
            }
        },
        required=(
            RequiredConfigValue(
                any_of=("tools", "toolsets", "hosted_tools"),
                reason="tool_agent must have at least one tool source.",
            ),
        ),
        required_capabilities=("function_tools",),
    ),
    "retrieval_agent": WorkflowProfile(
        name="retrieval_agent",
        description="Retrieval-heavy agent with provider tool search and conservative compaction.",
        tradeoffs="Improves context discovery at the cost of additional tool/search latency and provider-specific support.",
        surfaces=("model", "runtime"),
        defaults={
            "tool_search": {"enabled": True, "max_results": 5},
            "compaction": {"strategy": "auto"},
        },
        required=(
            RequiredConfigValue(
                any_of=("hosted_tools", "toolsets", "tools", "data_sources", "tool_search"),
                reason="retrieval_agent needs a retrieval source or tool-search control.",
            ),
        ),
        required_capabilities=("tool_search",),
    ),
    "coding_agent": WorkflowProfile(
        name="coding_agent",
        description="Workspace-oriented coding run with dynamic tools, cache, compaction, and risky-action approvals.",
        tradeoffs="Assumes workspace access and can expose more tools; policy controls should be kept enabled for write and command actions.",
        surfaces=("runtime",),
        defaults={
            "cache": {"strategy": "ephemeral"},
            "compaction": {"strategy": "auto"},
            "approval_policy": "risky_actions",
        },
        surface_defaults={
            "runtime": {
                "tools": "dynamic",
                "tool_selection": "auto",
                "max_iterations": 8,
                "tool_budget": {
                    "max_tools_visible": 24,
                    "max_parallel_calls": 4,
                },
            }
        },
        required=(
            RequiredConfigValue(
                any_of=("workspace",),
                reason="coding_agent must know which workspace to operate on.",
            ),
        ),
        required_capabilities=("function_tools",),
    ),
    "cloud_agent_session": WorkflowProfile(
        name="cloud_agent_session",
        description="Provider-managed agent session configuration for cloud agent adapters.",
        tradeoffs="Delegates lifecycle and tool behavior to the agent provider; direct model-turn controls do not apply.",
        surfaces=("agent_session",),
        defaults={"metadata": {"workflow_profile": "cloud_agent_session"}},
        required=(
            RequiredConfigValue(
                any_of=("agent",),
                reason="cloud_agent_session must identify the managed agent.",
            ),
        ),
        required_capabilities=("agent_sessions",),
    ),
    "realtime_voice": WorkflowProfile(
        name="realtime_voice",
        description="Realtime voice session with text/audio input and audio output defaults.",
        tradeoffs="Realtime transports are provider-specific and require audio-capable models.",
        surfaces=("realtime",),
        defaults={
            "transport": "websocket",
            "tool_mode": "manual",
            "realtime_session": {
                "input_modalities": ["text", "audio"],
                "output_modalities": ["text", "audio"],
                "audio": {"voice": "alloy"},
            },
        },
        required_capabilities=("realtime_audio",),
    ),
    "eval_run": WorkflowProfile(
        name="eval_run",
        description="Deterministic run shape intended for repeatable evaluations and trace comparison.",
        tradeoffs="Low variance settings make evals easier to compare but can reduce creative exploration.",
        surfaces=("model", "runtime"),
        defaults={"temperature": 0.0, "parallel_tool_calls": False},
        surface_defaults={"runtime": {"context_flags": ["eval"]}},
    ),
    "cost_sensitive": WorkflowProfile(
        name="cost_sensitive",
        description="Lower-cost run shape with reduced token and tool budgets plus cache-friendly defaults.",
        tradeoffs="May truncate long answers or under-expose tools compared with reliability-oriented profiles.",
        surfaces=("model", "runtime"),
        defaults={
            "temperature": 0.2,
            "max_output_tokens": 1024,
            "cache": {"strategy": "auto"},
        },
        surface_defaults={
            "runtime": {
                "max_iterations": 4,
                "tool_budget": {
                    "max_tools_visible": 8,
                    "max_tool_calls": 8,
                    "max_parallel_calls": 2,
                },
            }
        },
    ),
    "high_reliability": WorkflowProfile(
        name="high_reliability",
        description="Conservative high-accuracy settings with deterministic sampling and serialized tool calls.",
        tradeoffs="Prefers careful responses over speed or cost; reasoning controls are provider/model dependent.",
        surfaces=("model", "runtime"),
        defaults={
            "temperature": 0.0,
            "parallel_tool_calls": False,
            "reasoning_effort": "high",
        },
        surface_defaults={"runtime": {"max_iterations": 12}},
        required_capabilities=("reasoning_effort",),
    ),
}
