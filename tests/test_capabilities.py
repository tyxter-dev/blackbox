from __future__ import annotations

from agent_runtime.agents.claude_code import ClaudeCodeAgentProvider
from agent_runtime.agents.local import LocalAgentProvider
from agent_runtime.agents.openai_cloud import OpenAICloudAgentProvider
from agent_runtime.agents.vertex_agent_engine import VertexAIAgentEngineProvider
from agent_runtime.core.capabilities import AgentCapabilities, ModelCapabilities
from agent_runtime.models.anthropic_messages import AnthropicMessagesProvider
from agent_runtime.models.echo import EchoModelProvider
from agent_runtime.models.gemini_generate_content import GeminiGenerateContentProvider
from agent_runtime.models.openai_responses import OpenAIResponsesProvider
from agent_runtime.providers.registry import ProviderRegistry
from agent_runtime.runtime import ModelRuntime


def test_echo_capabilities_advertise_streaming_and_state() -> None:
    caps = EchoModelProvider().capabilities()
    assert isinstance(caps, ModelCapabilities)
    assert caps.supports_streaming_events is True
    assert caps.supports_provider_state is True
    assert caps.supports_function_tools is False  # echo is intentionally minimal


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
    assert caps.supports_function_tools
    assert caps.supports_parallel_tool_calls
    assert caps.supports_remote_mcp is False  # native MCP not supported per PRD


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


def test_cloud_agent_stub_capabilities_advertise_full_lifecycle() -> None:
    for provider in (
        OpenAICloudAgentProvider(api_key="x"),
        ClaudeCodeAgentProvider(api_key="x"),
        VertexAIAgentEngineProvider(project="p"),
    ):
        caps = provider.capabilities()
        assert caps.supports_sessions
        assert caps.supports_artifacts
        assert caps.supports_workspace
        assert caps.supports_approvals
        assert caps.supports_cancellation


def test_vertex_agent_engine_advertises_deployments_and_evals() -> None:
    caps = VertexAIAgentEngineProvider(project="p").capabilities()
    assert caps.supports_deployments
    assert caps.supports_evals
