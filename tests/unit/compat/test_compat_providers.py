from __future__ import annotations

import pytest

from blackbox.agents.local import LocalAgentProvider as CompatLocalAgentProvider
from blackbox.compat.providers import (
    create_runtime_with_default_providers,
    provider_ref_for_model,
    register_default_model_providers,
    resolve_model_route,
)
from blackbox.loop import AgentLoop as CompatAgentLoop
from blackbox.models.xai_responses import XAIResponsesProvider as CompatXAIResponsesProvider
from blackbox.providers.agent_adapters.local import LocalAgentProvider
from blackbox.providers.model_adapters.xai_responses import XAIResponsesProvider
from blackbox.providers.registry import ProviderRegistry
from blackbox.runtime import AgentRuntime
from blackbox.runtime.agent_loop import AgentLoop


@pytest.mark.parametrize(
    ("model", "provider", "resolved_model"),
    [
        ("gpt-4o-mini", "openai", "gpt-4o-mini"),
        ("o3", "openai", "o3"),
        ("claude-sonnet-4-5", "anthropic", "claude-sonnet-4-5"),
        ("gemini-2.5-flash", "google", "gemini-2.5-flash"),
        ("grok-4", "xai", "grok-4"),
        ("openai/gpt-4o-mini", "openai", "gpt-4o-mini"),
        ("openai:gpt-4o-mini", "openai", "gpt-4o-mini"),
        ("google/gemini-2.5-flash", "google", "gemini-2.5-flash"),
        ("gemini/gemini-2.5-flash", "google", "gemini-2.5-flash"),
        ("xai:grok-4", "xai", "grok-4"),
    ],
)
def test_resolve_model_route(model: str, provider: str, resolved_model: str) -> None:
    route = resolve_model_route(model)

    assert route.provider == provider
    assert route.model == resolved_model
    assert route.provider_ref == f"{provider}:{resolved_model}"


def test_provider_ref_for_model_returns_canonical_reference() -> None:
    assert provider_ref_for_model("gpt-4o-mini") == "openai:gpt-4o-mini"


def test_resolve_model_route_rejects_unknown_model() -> None:
    with pytest.raises(ValueError, match="Cannot infer provider"):
        resolve_model_route("custom-model")


def test_register_default_model_providers_creates_registry() -> None:
    registry = register_default_model_providers()

    assert registry.list_model_providers() == [
        "anthropic",
        "gemini",
        "google",
        "openai",
        "xai",
    ]


def test_register_default_model_providers_can_populate_runtime() -> None:
    runtime = AgentRuntime()

    returned = register_default_model_providers(runtime, include=["gemini", "xai"])

    assert returned is runtime.registry
    assert runtime.registry.list_model_providers() == ["gemini", "google", "xai"]


def test_register_default_model_providers_can_populate_existing_registry() -> None:
    registry = ProviderRegistry()

    returned = register_default_model_providers(registry, include=["openai"])

    assert returned is registry
    assert registry.list_model_providers() == ["openai"]


def test_register_default_model_providers_rejects_unknown_include() -> None:
    with pytest.raises(ValueError, match="Unknown provider"):
        register_default_model_providers(include=["ollama"])


def test_create_runtime_with_default_providers() -> None:
    runtime = create_runtime_with_default_providers(include=["anthropic"])

    assert isinstance(runtime, AgentRuntime)
    assert runtime.registry.list_model_providers() == ["anthropic"]


def test_xai_provider_defaults_to_xai_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "openai-key")
    monkeypatch.setenv("XAI_API_KEY", "xai-key")

    provider = XAIResponsesProvider()

    assert provider.api_key == "xai-key"


def test_legacy_model_adapter_import_path_reexports_canonical_provider() -> None:
    assert CompatXAIResponsesProvider is XAIResponsesProvider


def test_legacy_agent_adapter_import_path_reexports_canonical_provider() -> None:
    assert CompatLocalAgentProvider is LocalAgentProvider


def test_legacy_agent_loop_import_path_reexports_canonical_loop() -> None:
    assert CompatAgentLoop is AgentLoop
