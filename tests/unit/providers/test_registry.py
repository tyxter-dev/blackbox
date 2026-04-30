from __future__ import annotations

import pytest

from blackbox.core.errors import ProviderNotFoundError
from blackbox.providers.model_adapters.echo import EchoModelProvider
from blackbox.providers.registry import ProviderRef, ProviderRegistry


def test_provider_ref_parse_with_resource() -> None:
    ref = ProviderRef.parse("openai/gpt-5")
    assert ref.provider_key == "openai"
    assert ref.resource == "gpt-5"


def test_provider_ref_parse_with_colon_separator() -> None:
    ref = ProviderRef.parse("openai:gpt-5.4")
    assert ref.provider_key == "openai"
    assert ref.resource == "gpt-5.4"


def test_provider_ref_parse_colon_takes_precedence_over_slash() -> None:
    ref = ProviderRef.parse("vertex-agent-engine:projects/foo/agent")
    assert ref.provider_key == "vertex-agent-engine"
    assert ref.resource == "projects/foo/agent"


def test_provider_ref_parse_without_resource() -> None:
    ref = ProviderRef.parse("echo")
    assert ref.provider_key == "echo"
    assert ref.resource is None


def test_register_and_resolve_model_provider() -> None:
    registry = ProviderRegistry()
    provider = EchoModelProvider()
    registry.register_model(provider)
    assert registry.get_model("echo/echo-mini") is provider


def test_unknown_model_provider_raises() -> None:
    registry = ProviderRegistry()
    with pytest.raises(ProviderNotFoundError):
        registry.get_model("missing/model")
