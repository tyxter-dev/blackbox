"""Capability honesty contract.

Two halves to this contract:

1. Every provider's ``capabilities()`` method returns the right shape and
   advertises the features it actually supports.
2. Every flag advertised as ``False`` must produce a typed runtime error
   when the corresponding operation is attempted, not a silent no-op.

This is a *contract* test rather than a unit test because the same
expectations apply across every adapter the runtime exposes.
"""
from __future__ import annotations

import pytest

from agent_runtime import AgentRuntime, AgentSpec
from agent_runtime.agents.claude_code import ClaudeCodeAgentProvider
from agent_runtime.agents.local import LocalAgentProvider
from agent_runtime.agents.openai_cloud import OpenAICloudAgentProvider
from agent_runtime.agents.vertex_agent_engine import VertexAIAgentEngineProvider
from agent_runtime.core.approvals import ApprovalDecision
from agent_runtime.core.capabilities import (
    AgentCapabilities,
    ModelCapabilities,
    ModelCapabilityProfile,
    get_model_capability_profile,
)
from agent_runtime.core.errors import (
    ApprovalError,
    ProviderNotConfiguredError,
    SessionError,
    UnsupportedFeatureError,
)
from agent_runtime.core.sessions import SessionRef
from agent_runtime.models.anthropic_messages import AnthropicMessagesProvider
from agent_runtime.models.echo import EchoModelProvider
from agent_runtime.models.gemini_generate_content import GeminiGenerateContentProvider
from agent_runtime.models.openai_responses import OpenAIResponsesProvider
from agent_runtime.models.xai_responses import XAIResponsesProvider
from agent_runtime.providers.base import AgentProvider, ModelProvider, TaskSpec, TurnRequest
from agent_runtime.providers.registry import ProviderRegistry
from agent_runtime.runtime import ModelRuntime
from tests.fixtures.scripted_model import ScriptedModelProvider, tool_call_turn

# --- positive: capabilities surfaces match the PRD --------------------------

def test_echo_capabilities_advertise_streaming_and_state() -> None:
    caps = EchoModelProvider().capabilities()
    assert isinstance(caps, ModelCapabilities)
    assert caps.supports_streaming_events is True
    assert caps.supports_provider_state is True
    assert caps.supports_function_tools is False  # honest minimal


def test_openai_responses_capabilities_match_prd() -> None:
    caps = OpenAIResponsesProvider(api_key="x").capabilities()
    assert caps.supports_function_tools
    assert caps.supports_parallel_tool_calls
    assert caps.supports_hosted_tools
    assert caps.supports_remote_mcp
    assert caps.supports_reasoning_items
    assert caps.supports_provider_state
    assert caps.supports_structured_output


def test_anthropic_messages_capabilities_match_prd() -> None:
    caps = AnthropicMessagesProvider(api_key="x").capabilities()
    assert caps.supports_function_tools
    assert caps.supports_hosted_tools
    assert caps.supports_remote_mcp
    assert caps.supports_reasoning_items
    assert caps.supports_structured_output is False


def test_gemini_capabilities_match_prd() -> None:
    caps = GeminiGenerateContentProvider(api_key="x").capabilities()
    assert caps.supports_streaming_events
    assert caps.supports_function_tools
    assert caps.supports_parallel_tool_calls
    assert caps.supports_hosted_tools
    assert caps.supports_remote_mcp is False  # native MCP not supported per PRD
    assert caps.supports_reasoning_items
    assert caps.supports_provider_state
    assert caps.supports_structured_output


def test_xai_responses_capabilities_match_prd() -> None:
    caps = XAIResponsesProvider(api_key="x").capabilities()
    assert caps.supports_streaming_events
    assert caps.supports_function_tools
    assert caps.supports_parallel_tool_calls
    assert caps.supports_hosted_tools
    assert caps.supports_tool_search is False
    assert caps.supports_client_executed_tool_search is False
    assert caps.supports_remote_mcp is False
    assert caps.supports_reasoning_items
    assert caps.supports_provider_state
    assert caps.supports_structured_output is False


@pytest.mark.parametrize(
    "provider",
    [
        EchoModelProvider(),
        OpenAIResponsesProvider(api_key="x"),
        AnthropicMessagesProvider(api_key="x"),
        GeminiGenerateContentProvider(api_key="x"),
        XAIResponsesProvider(api_key="x"),
    ],
)
def test_model_providers_return_granular_capability_profiles(
    provider: ModelProvider,
) -> None:
    profile = get_model_capability_profile(provider, "test-model")

    assert isinstance(profile, ModelCapabilityProfile)
    assert profile.provider
    assert profile.model == "test-model"
    assert profile.summary == provider.capabilities("test-model")


@pytest.mark.parametrize(
    "provider",
    [
        EchoModelProvider(),
        OpenAIResponsesProvider(api_key="x"),
        AnthropicMessagesProvider(api_key="x"),
        GeminiGenerateContentProvider(api_key="x"),
        XAIResponsesProvider(api_key="x"),
    ],
)
def test_granular_profiles_do_not_contradict_flat_summary(provider: ModelProvider) -> None:
    profile = get_model_capability_profile(provider, "test-model")

    provider_native = profile.output_strategies.get("provider_native")
    if provider_native is not None and provider_native.status == "supported":
        assert profile.summary.supports_structured_output is True
    finalizer_tool = profile.output_strategies.get("finalizer_tool")
    if finalizer_tool is not None and finalizer_tool.status == "supported":
        assert profile.summary.supports_function_tools is True


def test_local_agent_capabilities_default_no_approvals() -> None:
    caps = LocalAgentProvider(ModelRuntime(ProviderRegistry())).capabilities()
    assert isinstance(caps, AgentCapabilities)
    assert caps.supports_sessions
    assert caps.supports_cancellation
    assert caps.supports_approvals is False


def test_local_agent_capabilities_with_approval_policy() -> None:
    provider = LocalAgentProvider(
        ModelRuntime(ProviderRegistry()),
        approval_policy=lambda name, args: True,
    )
    assert provider.capabilities().supports_approvals is True


def test_cloud_agent_stub_capabilities_do_not_advertise_unimplemented_lifecycle() -> None:
    for provider in (
        OpenAICloudAgentProvider(api_key="x"),
        VertexAIAgentEngineProvider(project="p"),
    ):
        caps = provider.capabilities()
        assert caps.supports_sessions is False
        assert caps.supports_streaming_events is False
        assert caps.supports_artifacts is False
        assert caps.supports_workspace is False
        assert caps.supports_approvals is False
        assert caps.supports_mcp is False
        assert caps.supports_cancellation is False
        assert caps.supports_resume is False


def test_vertex_agent_engine_does_not_advertise_unimplemented_lifecycle() -> None:
    caps = VertexAIAgentEngineProvider(project="p").capabilities()
    assert caps.supports_deployments is False
    assert caps.supports_evals is False


def test_claude_code_sdk_capabilities_advertise_provider_lifecycle() -> None:
    provider = ClaudeCodeAgentProvider(api_key="x")
    caps = provider.capabilities()
    assert caps.supports_sessions is True
    assert caps.supports_streaming_events is True
    assert caps.supports_artifacts is True
    assert caps.supports_workspace is True
    assert caps.supports_approvals is True
    assert caps.supports_mcp is True
    assert caps.supports_cancellation is True
    assert caps.supports_resume is True


@pytest.mark.parametrize(
    "provider",
    [
        OpenAICloudAgentProvider(api_key="x"),
        VertexAIAgentEngineProvider(project="p"),
    ],
)
async def test_cloud_agent_stub_operations_raise_unsupported_when_configured(
    provider: AgentProvider,
) -> None:
    session = SessionRef(provider=provider.provider_id, id="sess_test")

    with pytest.raises(UnsupportedFeatureError):
        await provider.create_agent(AgentSpec(name="x"))
    with pytest.raises(UnsupportedFeatureError):
        await provider.start_session("agent", TaskSpec(prompt="run"))
    with pytest.raises(UnsupportedFeatureError):
        async for _ in provider.stream_events(session):
            pass
    with pytest.raises(UnsupportedFeatureError):
        await provider.send_message(session, "continue")
    with pytest.raises(UnsupportedFeatureError):
        await provider.approve("approval_1", ApprovalDecision.approve())
    with pytest.raises(UnsupportedFeatureError):
        await provider.cancel(session)
    with pytest.raises(UnsupportedFeatureError):
        await provider.list_artifacts(session)


# --- negative: a False flag must error, not silently succeed ---------------

async def test_approve_with_no_pending_request_raises_approval_error() -> None:
    runtime = AgentRuntime()
    runtime.registry.register_model(EchoModelProvider())
    runtime.registry.register_agent(LocalAgentProvider(runtime.models))

    with pytest.raises(ApprovalError):
        await runtime.agents.approve(
            provider="local",
            approval_id="approval_does_not_exist",
            decision=ApprovalDecision.approve(),
        )


async def test_tool_call_without_tool_runtime_raises_session_error() -> None:
    runtime = AgentRuntime()
    scripted = ScriptedModelProvider()
    runtime.registry.register_model(scripted)
    runtime.registry.register_agent(LocalAgentProvider(runtime.models))

    scripted.queue(tool_call_turn(call_id="c1", name="missing", arguments={}))

    await runtime.agents.create_agent(provider="local", spec=AgentSpec(name="d"))
    session = await runtime.agents.create_session(
        provider="local", agent="d", task="x", model="scripted/test",
    )
    with pytest.raises(SessionError):
        async for _ in runtime.agents.stream(session):
            pass


async def test_local_agent_artifacts_default_empty_page() -> None:
    runtime = AgentRuntime()
    runtime.registry.register_model(EchoModelProvider())
    runtime.registry.register_agent(LocalAgentProvider(runtime.models))

    await runtime.agents.create_agent(provider="local", spec=AgentSpec(name="d"))
    session = await runtime.agents.create_session(
        provider="local", agent="d", task="x", model="echo/echo-mini",
    )
    page = await runtime.agents.list_artifacts(session)
    assert page.items == []
    assert page.has_more is False
    assert page.next_cursor is None


async def test_cloud_agent_stub_create_agent_requires_credentials() -> None:
    provider = OpenAICloudAgentProvider()
    with pytest.raises(ProviderNotConfiguredError):
        await provider.create_agent(AgentSpec(name="x"))


async def test_cloud_agent_stub_create_agent_raises_unsupported_when_configured() -> None:
    provider = OpenAICloudAgentProvider(api_key="x")
    with pytest.raises(UnsupportedFeatureError):
        await provider.create_agent(AgentSpec(name="x"))


async def test_gemini_requires_credentials_or_client() -> None:
    provider = GeminiGenerateContentProvider()
    with pytest.raises(ProviderNotConfiguredError):
        async for _ in provider.stream_turn(TurnRequest(model="gemini-test", input="hi")):
            pass


async def test_gemini_client_satisfies_streaming_capability() -> None:
    class EmptyModels:
        async def generate_content_stream(self, **kwargs: object) -> object:
            if False:
                yield None

    class EmptyClient:
        aio = type("Aio", (), {"models": EmptyModels()})()

    provider = GeminiGenerateContentProvider(client=EmptyClient())
    events = [
        event
        async for event in provider.stream_turn(TurnRequest(model="gemini-test", input="hi"))
    ]
    assert events[-1].type == "model.completed"
