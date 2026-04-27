from __future__ import annotations

import pytest

from agent_runtime.core.capabilities import (
    CapabilityDetail,
    ModelCapabilities,
    ModelCapabilityProfile,
)
from agent_runtime.core.errors import UnsupportedFeatureError
from agent_runtime.mcp import MCPServerSpec, MCPToolset, resolve_mcp_route, to_remote_mcp
from agent_runtime.providers.registry import ProviderRef


def _profile(*, remote_mcp: bool) -> ModelCapabilityProfile:
    return ModelCapabilityProfile(
        provider="openai",
        model="gpt-test",
        hosted_tools={
            "remote_mcp": CapabilityDetail(
                status="supported" if remote_mcp else "unsupported"
            )
        },
        summary=ModelCapabilities(supports_remote_mcp=remote_mcp),
    )


def test_stdio_routes_local() -> None:
    toolset = MCPToolset(
        MCPServerSpec(name="tickets", transport="stdio", command="ticket-mcp")
    )

    assert (
        resolve_mcp_route(ProviderRef.parse("openai/gpt-test"), toolset, _profile(remote_mcp=True))
        == "local"
    )


def test_remote_http_with_capability_routes_provider_native() -> None:
    toolset = MCPToolset(
        MCPServerSpec(
            name="github",
            transport="streamable_http",
            url="https://mcp.example.com/mcp",
            allowed_tools=["list_issues"],
            authorization="Bearer token",
            require_approval="never",
        )
    )

    route = resolve_mcp_route(
        ProviderRef.parse("openai/gpt-test"),
        toolset,
        _profile(remote_mcp=True),
    )
    remote = to_remote_mcp(toolset)

    assert route == "provider_native"
    assert remote.server_label == "github"
    assert remote.allowed_tools == ["list_issues"]
    assert remote.authorization == "Bearer token"
    assert remote.require_approval == "never"


def test_remote_http_without_capability_routes_local_in_auto() -> None:
    toolset = MCPToolset(
        MCPServerSpec(
            name="github",
            transport="streamable_http",
            url="https://mcp.example.com/mcp",
        )
    )

    assert (
        resolve_mcp_route(
            ProviderRef.parse("scripted/test"),
            toolset,
            _profile(remote_mcp=False),
        )
        == "local"
    )


def test_provider_native_stdio_rejects() -> None:
    toolset = MCPToolset(
        MCPServerSpec(name="tickets", transport="stdio", command="ticket-mcp"),
        mode="provider_native",
    )

    with pytest.raises(UnsupportedFeatureError, match="stdio"):
        resolve_mcp_route(
            ProviderRef.parse("openai/gpt-test"),
            toolset,
            _profile(remote_mcp=True),
        )


def test_policy_forces_local_route_in_auto() -> None:
    toolset = MCPToolset(
        MCPServerSpec(
            name="github",
            transport="streamable_http",
            url="https://mcp.example.com/mcp",
        )
    )

    assert (
        resolve_mcp_route(
            ProviderRef.parse("openai/gpt-test"),
            toolset,
            _profile(remote_mcp=True),
            policy=object(),
        )
        == "local"
    )
