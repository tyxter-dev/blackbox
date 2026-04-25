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
from agent_runtime.core.capabilities import AgentCapabilities, ModelCapabilities
from agent_runtime.core.errors import (
    ApprovalError,
    ProviderNotConfiguredError,
    SessionError,
    UnsupportedFeatureError,
)
from agent_runtime.models.anthropic_messages import AnthropicMessagesProvider
from agent_runtime.models.echo import EchoModelProvider
from agent_runtime.models.gemini_generate_content import GeminiGenerateContentProvider
from agent_runtime.models.openai_responses import OpenAIResponsesProvider
from agent_runtime.providers.base import TurnRequest
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


def test_anthropic_messages_capabilities_match_prd() -> None:
    caps = AnthropicMessagesProvider(api_key="x").capabilities()
    assert caps.supports_function_tools
    assert caps.supports_hosted_tools
    assert caps.supports_remote_mcp
    assert caps.supports_reasoning_items


def test_gemini_capabilities_match_prd() -> None:
    caps = GeminiGenerateContentProvider(api_key="x").capabilities()
    assert caps.supports_streaming_events
    assert caps.supports_function_tools
    assert caps.supports_parallel_tool_calls
    assert caps.supports_hosted_tools
    assert caps.supports_remote_mcp is False  # native MCP not supported per PRD
    assert caps.supports_reasoning_items
    assert caps.supports_provider_state


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
        ClaudeCodeAgentProvider(api_key="x"),
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
