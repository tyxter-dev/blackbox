"""Negative capability tests.

For every capability flag a provider advertises as ``False``, the corresponding
operation must produce a typed runtime error rather than silently no-op or
return a misleading success. These tests guard against capability dishonesty.
"""
from __future__ import annotations

import pytest

from agent_runtime import AgentRuntime, AgentSpec
from agent_runtime.agents.local import LocalAgentProvider
from agent_runtime.core.errors import ApprovalError, SessionError
from agent_runtime.models.echo import EchoModelProvider
from tests.fixtures.scripted_model import ScriptedModelProvider, tool_call_turn


async def test_local_agent_without_approvals_does_not_advertise_them() -> None:
    runtime = AgentRuntime()
    runtime.registry.register_model(EchoModelProvider())
    local = LocalAgentProvider(runtime.models)
    runtime.registry.register_agent(local)
    assert local.capabilities().supports_approvals is False


async def test_approve_with_no_pending_request_raises_approval_error() -> None:
    runtime = AgentRuntime()
    runtime.registry.register_model(EchoModelProvider())
    runtime.registry.register_agent(LocalAgentProvider(runtime.models))

    with pytest.raises(ApprovalError):
        await runtime.agents.approve(
            provider="local",
            approval_id="approval_does_not_exist",
            decision=__import__(
                "agent_runtime.core.approvals", fromlist=["ApprovalDecision"]
            ).ApprovalDecision.approve(),
        )


async def test_tool_call_without_tool_runtime_raises_session_error() -> None:
    runtime = AgentRuntime()
    scripted = ScriptedModelProvider()
    runtime.registry.register_model(scripted)
    # LocalAgentProvider here has no ToolRuntime.
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
    local = LocalAgentProvider(runtime.models)
    runtime.registry.register_agent(local)

    await runtime.agents.create_agent(provider="local", spec=AgentSpec(name="d"))
    session = await runtime.agents.create_session(
        provider="local", agent="d", task="x", model="echo/echo-mini",
    )
    page = await runtime.agents.list_artifacts(session)
    assert page.items == []
    assert page.has_more is False
    assert page.next_cursor is None


async def test_echo_provider_does_not_claim_function_tools() -> None:
    """Echo advertises function_tools=False; using it as a tool-driving model should still work,
    but its capability flag should remain honest so policy/router code can short-circuit."""
    caps = EchoModelProvider().capabilities()
    assert caps.supports_function_tools is False


async def test_cloud_agent_stub_create_agent_requires_credentials() -> None:
    from agent_runtime.agents.openai_cloud import OpenAICloudAgentProvider
    from agent_runtime.core.errors import ProviderNotConfiguredError

    provider = OpenAICloudAgentProvider()  # no api_key
    with pytest.raises(ProviderNotConfiguredError):
        await provider.create_agent(AgentSpec(name="x"))
