from __future__ import annotations

from typing import Any

import pytest

from blackbox import AgentRuntime, WorkspaceAgentSpec
from blackbox.core.capabilities import AgentCapabilities
from blackbox.core.errors import UnsupportedFeatureError
from blackbox.core.tool_permissions import permission_boundary
from blackbox.providers.agent_adapters.local import LocalAgentProvider
from blackbox.workspace_agents.permissions import compile_package_permissions
from blackbox.workspace_agents.runtime import run_workspace_agent


@pytest.mark.parametrize("inherited", [False, True])
async def test_managed_package_fails_before_startup(inherited: bool) -> None:
    effects: list[str] = []

    class Managed:
        provider_id = "managed"

        def capabilities(self) -> AgentCapabilities:
            return AgentCapabilities()

        async def create_agent(self, spec: Any) -> Any:
            effects.append("create")
            raise AssertionError("startup happened")

        async def start_session(self, *args: Any, **kwargs: Any) -> Any:
            effects.append("start")
            raise AssertionError("startup happened")

    runtime = AgentRuntime()
    runtime.registry.register_agent(Managed())
    package = WorkspaceAgentSpec(
        "managed",
        agent_provider="managed",
        permission_mode="inherit" if inherited else "allowlist_v1",
    )
    with permission_boundary((compile_package_permissions([], []),) if inherited else ()):
        with pytest.raises(UnsupportedFeatureError):
            await run_workspace_agent(runtime, package, input="go")
    assert effects == []
    assert LocalAgentProvider(runtime.models).capabilities().supports_package_permissions


@pytest.mark.parametrize("provider_name", ["openai", "claude", "codex"])
def test_native_client_cannot_advertise_unimplemented_package_enforcement(
    provider_name: str,
) -> None:
    from blackbox.providers.agent_adapters.claude_code import ClaudeCodeAgentProvider
    from blackbox.providers.agent_adapters.codex import CodexAgentProvider
    from blackbox.providers.agent_adapters.openai_cloud import OpenAICloudAgentProvider

    class Client:
        def capabilities(self) -> AgentCapabilities:
            return AgentCapabilities(supports_package_permissions=True)

    providers = {
        "openai": OpenAICloudAgentProvider,
        "claude": ClaudeCodeAgentProvider,
        "codex": CodexAgentProvider,
    }
    assert not providers[provider_name](client=Client()).capabilities().supports_package_permissions


@pytest.mark.parametrize("surface", ["workspace", "mcp", "client_hosted"])
async def test_local_unimplemented_setup_fails_before_agent_creation(surface: str) -> None:
    from blackbox.mcp import MCPServerSpec
    from blackbox.tools.hosted.specs import Shell

    runtime = AgentRuntime()
    local = LocalAgentProvider(runtime.models)
    runtime.registry.register_agent(local)
    package = WorkspaceAgentSpec(
        "local",
        agent_provider="local",
        permission_mode="allowlist_v1",
        mcp_servers=[MCPServerSpec(name="test", transport="stdio")] if surface == "mcp" else [],
        hosted_tools=[Shell(execution="local")] if surface == "client_hosted" else [],
    )
    with pytest.raises(UnsupportedFeatureError):
        await run_workspace_agent(
            runtime,
            package,
            input="go",
            **({"workspace": object()} if surface == "workspace" else {}),
        )
    assert local._agents == {} and local._sessions == {}
