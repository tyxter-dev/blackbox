from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from agent_runtime import AgentRuntime, ModelCacheControl
from agent_runtime.core.errors import ConfigurationError, UnsupportedFeatureError
from agent_runtime.providers.model_adapters.echo import EchoModelProvider
from agent_runtime.runtime.config import (
    RuntimeConfig,
    get_workflow_profile,
    workflow_profile_docs,
    workflow_profiles,
)
from agent_runtime.workspaces import WorkspaceSpec
from tests.fixtures.scripted_model import ScriptedModelProvider, text_only_turn

EXPECTED_PROFILES = {
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
}


def test_all_builtin_profiles_have_documented_defaults_and_tradeoffs() -> None:
    profiles = workflow_profiles()

    assert {profile.name for profile in profiles} == EXPECTED_PROFILES
    for profile in profiles:
        assert profile.description
        assert profile.tradeoffs
        assert profile.surfaces
        assert isinstance(profile.defaults_for(), dict)
    docs = workflow_profile_docs()
    assert set(docs) == EXPECTED_PROFILES
    assert docs["coding_agent"]["required"][0]["any_of"] == ["workspace"]


def test_profile_expands_surface_defaults_and_overrides() -> None:
    config = RuntimeConfig.profile("fast_text").with_overrides(max_output_tokens=128)

    kwargs = config.to_kwargs(surface="runtime")

    assert kwargs["temperature"] == 0.2
    assert kwargs["max_output_tokens"] == 128
    assert kwargs["max_iterations"] == 1


def test_wrong_surface_profile_fails_early() -> None:
    config = RuntimeConfig.profile("realtime_voice")

    with pytest.raises(ConfigurationError, match="cannot be used"):
        config.to_kwargs(surface="runtime")


def test_profile_required_values_fail_early() -> None:
    config = RuntimeConfig.profile("coding_agent")

    with pytest.raises(ConfigurationError, match="workspace"):
        config.to_kwargs(surface="runtime")


def test_provider_qualified_model_expands_to_provider_argument() -> None:
    config = RuntimeConfig.profile("fast_text").with_overrides(model="openai:gpt-5.5")

    kwargs = config.to_kwargs(surface="model")

    assert kwargs["provider"] == "openai:gpt-5.5"
    assert "model" not in kwargs


def test_provider_qualified_model_rejects_mismatched_provider() -> None:
    config = RuntimeConfig.profile("fast_text").with_overrides(
        provider="anthropic",
        model="openai:gpt-5.5",
    )

    with pytest.raises(ConfigurationError, match="does not match"):
        config.to_kwargs(surface="model")


def test_from_env_parses_common_scalar_and_nested_values() -> None:
    config = RuntimeConfig.from_env(
        environ={
            "AGENT_RUNTIME_PROFILE": "cost_sensitive",
            "AGENT_RUNTIME_MODEL": "openai:gpt-5.5",
            "AGENT_RUNTIME_TEMPERATURE": "0.1",
            "AGENT_RUNTIME_MAX_OUTPUT_TOKENS": "256",
            "AGENT_RUNTIME_PARALLEL_TOOL_CALLS": "false",
            "AGENT_RUNTIME_CACHE_STRATEGY": "ephemeral",
            "AGENT_RUNTIME_TOOL_SEARCH_MAX_RESULTS": "3",
            "AGENT_RUNTIME_CONTEXT_FLAGS": "eval,nightly",
        }
    )

    kwargs = config.to_kwargs(surface="runtime")

    assert kwargs["provider"] == "openai:gpt-5.5"
    assert kwargs["temperature"] == 0.1
    assert kwargs["max_output_tokens"] == 256
    assert kwargs["parallel_tool_calls"] is False
    assert kwargs["cache"]["strategy"] == "ephemeral"
    assert kwargs["tool_search"]["max_results"] == 3
    assert kwargs["context_flags"] == ["eval", "nightly"]


def test_from_file_loads_toml_config(tmp_path: Path) -> None:
    path = tmp_path / "runtime.toml"
    path.write_text(
        """
profile = "fast_text"
model = "openai:gpt-5.5"
max_output_tokens = 300

[cache]
strategy = "ephemeral"
""".strip(),
        encoding="utf-8",
    )

    kwargs = RuntimeConfig.from_file(path).to_kwargs(surface="model")

    assert kwargs["provider"] == "openai:gpt-5.5"
    assert kwargs["max_output_tokens"] == 300
    assert kwargs["cache"]["strategy"] == "ephemeral"


def test_from_file_loads_json_config(tmp_path: Path) -> None:
    path = tmp_path / "runtime.json"
    path.write_text(
        json.dumps(
            {
                "profile": "fast_text",
                "overrides": {
                    "provider": "scripted:test",
                    "max_output_tokens": 64,
                },
            }
        ),
        encoding="utf-8",
    )

    kwargs = RuntimeConfig.from_file(path).to_kwargs(surface="model")

    assert kwargs["provider"] == "scripted:test"
    assert kwargs["max_output_tokens"] == 64


def test_yaml_loader_reports_optional_dependency_when_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "runtime.yaml"
    path.write_text("profile: fast_text\n", encoding="utf-8")

    def fail_import(name: str) -> Any:
        if name == "yaml":
            raise ImportError("missing")
        raise AssertionError(name)

    monkeypatch.setattr("agent_runtime.runtime.config.import_module", fail_import)

    with pytest.raises(ConfigurationError, match="PyYAML"):
        RuntimeConfig.from_file(path)


def test_unknown_profile_lists_known_profiles() -> None:
    with pytest.raises(ConfigurationError, match="fast_text"):
        get_workflow_profile("unknown")


async def test_model_runtime_accepts_config_and_expands_existing_arguments() -> None:
    runtime = AgentRuntime()
    scripted = ScriptedModelProvider()
    scripted.queue(text_only_turn("ok"))
    runtime.registry.register_model(scripted)
    config = RuntimeConfig.profile("fast_text").with_overrides(
        provider="scripted:test",
        cache={"strategy": "ephemeral"},
    )

    result = await runtime.models.run(
        input="hello",
        config=config,
        max_output_tokens=128,
    )

    assert result.text == "ok"
    request = scripted.calls[0]
    assert request.model == "test"
    assert request.controls.temperature == 0.2
    assert request.controls.max_output_tokens == 128
    assert isinstance(request.controls.cache, ModelCacheControl)
    assert request.controls.cache.strategy == "ephemeral"


async def test_model_runtime_explicit_provider_overrides_config_provider() -> None:
    runtime = AgentRuntime()
    scripted = ScriptedModelProvider()
    scripted.queue(text_only_turn("ok"))
    runtime.registry.register_model(scripted)
    config = RuntimeConfig.profile("fast_text").with_overrides(provider="echo:echo-mini")

    result = await runtime.models.run(
        provider="scripted:test",
        input="hello",
        config=config,
    )

    assert result.text == "ok"
    assert scripted.calls[0].model == "test"


async def test_model_runtime_rejects_invalid_profile_before_provider_call() -> None:
    runtime = AgentRuntime()
    scripted = ScriptedModelProvider()
    runtime.registry.register_model(scripted)
    config = RuntimeConfig.profile("structured_extraction").with_overrides(
        provider="scripted:test"
    )

    with pytest.raises(ConfigurationError, match="schema"):
        await runtime.models.run(input="hello", config=config)

    assert scripted.calls == []


async def test_model_runtime_rejects_unsupported_config_before_dispatch() -> None:
    runtime = AgentRuntime()
    runtime.registry.register_model(EchoModelProvider())
    config = RuntimeConfig.profile("high_reliability").with_overrides(
        provider="echo:echo-mini"
    )

    with pytest.raises(
        UnsupportedFeatureError,
        match=r"temperature|parallel_tool_calls|reasoning_effort",
    ):
        await runtime.models.run(input="hello", config=config)


async def test_agent_runtime_run_accepts_config_and_forwards_model_controls() -> None:
    runtime = AgentRuntime()
    scripted = ScriptedModelProvider()
    scripted.queue(text_only_turn("ok"))
    runtime.registry.register_model(scripted)
    config = RuntimeConfig.profile("fast_text").with_overrides(provider="scripted:test")

    result = await runtime.run(
        input="hello",
        config=config,
        max_output_tokens=128,
    )

    assert result.text == "ok"
    request = scripted.calls[0]
    assert request.model == "test"
    assert request.controls.temperature == 0.2
    assert request.controls.max_output_tokens == 128


async def test_agent_runtime_plan_run_accepts_config() -> None:
    runtime = AgentRuntime()
    runtime.registry.register_model(ScriptedModelProvider())
    config = RuntimeConfig.profile("fast_text").with_overrides(provider="scripted:test")

    plan = await runtime.plan_run(input="hello", config=config)

    assert plan.provider == "scripted"
    assert plan.model == "test"


async def test_agent_runtime_rejects_wrong_surface_config_before_dispatch() -> None:
    runtime = AgentRuntime()
    scripted = ScriptedModelProvider()
    runtime.registry.register_model(scripted)
    config = RuntimeConfig.profile("realtime_voice").with_overrides(
        provider="scripted:test"
    )

    with pytest.raises(ConfigurationError, match="cannot be used"):
        await runtime.run(input="hello", config=config)

    assert scripted.calls == []


async def test_agent_runtime_coding_profile_maps_named_approval_policy(
    tmp_path: Path,
) -> None:
    runtime = AgentRuntime()
    scripted = ScriptedModelProvider()
    scripted.queue(text_only_turn("done"))
    runtime.registry.register_model(scripted)
    config = RuntimeConfig.profile("coding_agent").with_overrides(
        provider="scripted:test",
        workspace=WorkspaceSpec.local(tmp_path),
        cache=None,
        compaction=None,
    )

    result = await runtime.run(input="inspect workspace", config=config)

    assert result.text == "done"
    assert scripted.calls[0].model == "test"
