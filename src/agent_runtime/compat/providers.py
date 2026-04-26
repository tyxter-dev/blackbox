from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Literal, cast

from agent_runtime.models.anthropic_messages import AnthropicMessagesProvider
from agent_runtime.models.gemini_generate_content import GeminiGenerateContentProvider
from agent_runtime.models.openai_responses import OpenAIResponsesProvider
from agent_runtime.models.xai_responses import XAIResponsesProvider
from agent_runtime.providers.registry import ProviderRegistry

ProviderKey = Literal["openai", "anthropic", "google", "xai"]
ProviderAlias = Literal["openai", "anthropic", "google", "gemini", "xai"]

_EXPLICIT_PREFIXES: dict[str, ProviderKey] = {
    "openai/": "openai",
    "openai:": "openai",
    "anthropic/": "anthropic",
    "anthropic:": "anthropic",
    "gemini/": "google",
    "gemini:": "google",
    "google/": "google",
    "google:": "google",
    "xai/": "xai",
    "xai:": "xai",
}

_BARE_PREFIXES: dict[str, ProviderKey] = {
    "gpt-": "openai",
    "o1-": "openai",
    "o3-": "openai",
    "o4-": "openai",
    "chatgpt-": "openai",
    "claude-": "anthropic",
    "gemini-": "google",
    "grok-": "xai",
}

_EXACT_BARE: dict[str, ProviderKey] = {
    "o1": "openai",
    "o3": "openai",
    "o4": "openai",
}


@dataclass(frozen=True, slots=True)
class ModelRoute:
    provider: ProviderKey
    model: str

    @property
    def provider_ref(self) -> str:
        return f"{self.provider}:{self.model}"


def resolve_model_route(model: str) -> ModelRoute:
    """Resolve common provider/model strings into runtime routing fields."""
    lower = model.lower()
    for prefix, provider in _EXPLICIT_PREFIXES.items():
        if lower.startswith(prefix):
            return ModelRoute(provider=provider, model=model[len(prefix):])
    for prefix, provider in _BARE_PREFIXES.items():
        if lower.startswith(prefix):
            return ModelRoute(provider=provider, model=model)
    exact_provider = _EXACT_BARE.get(lower)
    if exact_provider is not None:
        return ModelRoute(provider=exact_provider, model=model)
    raise ValueError(
        f"Cannot infer provider for model '{model}'. Use an explicit provider "
        "prefix such as openai/, anthropic/, google/, or xai/."
    )


def provider_ref_for_model(model: str) -> str:
    """Return the canonical ``provider:model`` reference for a model string."""
    return resolve_model_route(model).provider_ref


def register_default_model_providers(
    target: ProviderRegistry | Any | None = None,
    *,
    include: Iterable[ProviderAlias | str] | None = None,
    timeout: float | None = None,
    max_retries: int = 2,
    retry_min_delay: float = 0.5,
) -> ProviderRegistry:
    """Register the built-in model providers on a registry or runtime.

    Credentials are resolved lazily by each provider from its standard
    environment variable, so this helper is safe to call in offline tests and
    apps that only use a subset of providers.
    """
    registry = ProviderRegistry() if target is None else target.registry if hasattr(target, "registry") else target
    if not isinstance(registry, ProviderRegistry):
        raise TypeError("target must be a ProviderRegistry or expose a .registry")

    selected = _normalize_include(include)
    if "openai" in selected:
        registry.register_model(
            OpenAIResponsesProvider(
                timeout=timeout,
                max_retries=max_retries,
                retry_min_delay=retry_min_delay,
            )
        )
    if "anthropic" in selected:
        registry.register_model(
            AnthropicMessagesProvider(
                timeout=timeout,
                max_retries=max_retries,
                retry_min_delay=retry_min_delay,
            )
        )
    if "google" in selected:
        registry.register_model(
            GeminiGenerateContentProvider(
                timeout=timeout,
                max_retries=max_retries,
                retry_min_delay=retry_min_delay,
            ),
            "gemini",
        )
    if "xai" in selected:
        registry.register_model(
            XAIResponsesProvider(
                timeout=timeout,
                max_retries=max_retries,
                retry_min_delay=retry_min_delay,
            )
        )
    return registry


def create_runtime_with_default_providers(
    *,
    include: Iterable[ProviderAlias | str] | None = None,
    timeout: float | None = None,
    max_retries: int = 2,
    retry_min_delay: float = 0.5,
) -> Any:
    """Create an AgentRuntime preloaded with the built-in model providers."""
    from agent_runtime.runtime import AgentRuntime

    runtime = AgentRuntime()
    register_default_model_providers(
        runtime,
        include=include,
        timeout=timeout,
        max_retries=max_retries,
        retry_min_delay=retry_min_delay,
    )
    return runtime


def _normalize_include(include: Iterable[ProviderAlias | str] | None) -> set[ProviderKey]:
    if include is None:
        return {"openai", "anthropic", "google", "xai"}
    selected: set[ProviderKey] = set()
    for value in include:
        if value == "gemini":
            selected.add("google")
        elif value in {"openai", "anthropic", "google", "xai"}:
            selected.add(cast(ProviderKey, value))
        else:
            raise ValueError(
                f"Unknown provider '{value}'. Expected one of: openai, anthropic, google, "
                "gemini, xai."
            )
    return selected
