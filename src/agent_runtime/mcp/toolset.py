from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Literal, cast
from urllib.parse import urlparse

from agent_runtime.core.capabilities import ModelCapabilityProfile
from agent_runtime.core.errors import UnsupportedFeatureError
from agent_runtime.mcp.spec import MCPServerSpec
from agent_runtime.providers.registry import ProviderRef
from agent_runtime.tools.hosted.specs import RemoteMCP

MCPRouteMode = Literal["auto", "local", "provider_native"]
MCPResolvedRoute = Literal["local", "provider_native"]


@dataclass(slots=True, frozen=True)
class MCPToolset:
    """Runtime MCP server configuration plus routing overrides for one toolset."""

    server: MCPServerSpec
    mode: MCPRouteMode = "auto"
    description: str | None = None
    allowed_tools: list[str] | None = None
    denied_tools: list[str] | None = None
    provider_extra: dict[str, object] | None = None

    def server_for_local_dispatch(self) -> MCPServerSpec:
        return replace(
            self.server,
            allowed_tools=self.allowed_tools
            if self.allowed_tools is not None
            else self.server.allowed_tools,
            denied_tools=self.denied_tools
            if self.denied_tools is not None
            else self.server.denied_tools,
        )


def resolve_mcp_route(
    provider: ProviderRef,
    toolset: MCPToolset,
    profile: ModelCapabilityProfile,
    *,
    policy: object | None = None,
) -> MCPResolvedRoute:
    """Choose local or provider-native execution for an MCP toolset."""
    if toolset.mode == "local":
        return "local"

    if toolset.mode == "provider_native":
        _validate_provider_native_route(provider, toolset, profile)
        return "provider_native"

    server = toolset.server
    if server.transport == "stdio":
        return "local"
    if _is_local_or_private_url(server.url):
        return "local"
    if policy is not None:
        return "local"
    if _profile_supports_remote_mcp(profile) and server.transport in {
        "http",
        "sse",
        "streamable_http",
    }:
        return "provider_native"
    return "local"


def to_remote_mcp(toolset: MCPToolset) -> RemoteMCP:
    """Convert a toolset into the provider-facing RemoteMCP descriptor."""
    server = toolset.server
    extra = dict(toolset.provider_extra or {})
    connector_id = _pop_optional_str(extra, "connector_id")
    tool_configs = _pop_dict(extra, "tool_configs")
    cache_control = _pop_dict(extra, "cache_control")
    headers = {str(key): str(value) for key, value in server.headers.items()}
    provider_headers = _pop_dict(extra, "headers")
    if provider_headers:
        headers.update({str(key): str(value) for key, value in provider_headers.items()})
    authorization = _pop_optional_str(extra, "authorization") or server.authorization
    defer_loading = extra.pop("defer_loading", None)
    require_approval = cast(
        Literal["always", "never"] | None,
        server.require_approval if server.require_approval in {"always", "never"} else None,
    )
    return RemoteMCP(
        server_label=server.name,
        server_url=server.url,
        connector_id=connector_id,
        server_description=toolset.description,
        authorization=authorization,
        headers=headers,
        allowed_tools=toolset.allowed_tools
        if toolset.allowed_tools is not None
        else server.allowed_tools,
        denied_tools=toolset.denied_tools
        if toolset.denied_tools is not None
        else server.denied_tools,
        require_approval=require_approval,
        tool_configs=cast(dict[str, dict[str, Any]], tool_configs or {}),
        defer_loading=defer_loading if isinstance(defer_loading, bool) else None,
        cache_control=cache_control,
        extra=extra,
    )


def _validate_provider_native_route(
    provider: ProviderRef,
    toolset: MCPToolset,
    profile: ModelCapabilityProfile,
) -> None:
    server = toolset.server
    if server.transport == "stdio":
        raise UnsupportedFeatureError("Provider-native MCP cannot use stdio servers.")
    if not _profile_supports_remote_mcp(profile):
        raise UnsupportedFeatureError(
            f"{provider.provider_key} does not support provider-native remote MCP."
        )
    connector_id = str((toolset.provider_extra or {}).get("connector_id") or "")
    if not server.url and not connector_id:
        raise UnsupportedFeatureError(
            "Provider-native MCP requires a remote server URL or connector_id."
        )
    if server.auth_provider_name:
        raise UnsupportedFeatureError(
            "Provider-native MCP cannot use runtime-only MCP auth providers."
        )
    if _is_local_or_private_url(server.url) and not server.allow_remote_http:
        raise UnsupportedFeatureError(
            "Provider-native MCP rejects localhost/private URLs unless "
            "allow_remote_http=True is set explicitly."
        )


def _profile_supports_remote_mcp(profile: ModelCapabilityProfile) -> bool:
    detail = profile.hosted_tools.get("remote_mcp")
    return profile.summary.supports_remote_mcp or (
        detail is not None and detail.is_supported
    )


def _is_local_or_private_url(url: str | None) -> bool:
    if not url:
        return False
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if host in {"localhost", "127.0.0.1", "::1"}:
        return True
    if host.startswith("10.") or host.startswith("192.168."):
        return True
    if host.startswith("172."):
        try:
            second = int(host.split(".", 2)[1])
        except (IndexError, ValueError):
            return False
        return 16 <= second <= 31
    return False


def _pop_optional_str(data: dict[str, Any], key: str) -> str | None:
    value = data.pop(key, None)
    return value if isinstance(value, str) else None


def _pop_dict(data: dict[str, Any], key: str) -> dict[str, Any] | None:
    value = data.pop(key, None)
    return dict(value) if isinstance(value, dict) else None
