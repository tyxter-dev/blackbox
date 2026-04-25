from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, TypeAlias

from agent_runtime.core.errors import UnsupportedFeatureError


@dataclass(slots=True, frozen=True)
class WebSearch:
    search_context_size: str | None = None
    user_location: dict[str, Any] | None = None


@dataclass(slots=True, frozen=True)
class FileSearch:
    vector_store_ids: list[str]
    max_num_results: int | None = None
    filters: dict[str, Any] | None = None
    include_results: bool = False


@dataclass(slots=True, frozen=True)
class CodeInterpreter:
    container_id: str | None = None


MCPApprovalPolicy: TypeAlias = Literal["always", "never"] | dict[str, Any]


@dataclass(slots=True, frozen=True)
class RemoteMCP:
    server_label: str
    server_url: str
    server_description: str | None = None
    authorization: str | None = None
    allowed_tools: list[str] | None = None
    denied_tools: list[str] | None = None
    require_approval: MCPApprovalPolicy | None = None
    defer_loading: bool | None = None
    tool_configs: dict[str, dict[str, Any]] = field(default_factory=dict)
    cache_control: dict[str, Any] | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class HostedToolRaw:
    payload: dict[str, Any]


HostedToolSpec: TypeAlias = WebSearch | FileSearch | CodeInterpreter | RemoteMCP | HostedToolRaw


def to_openai_tool(spec: HostedToolSpec) -> dict[str, Any]:
    if isinstance(spec, WebSearch):
        payload: dict[str, Any] = {"type": "web_search"}
        if spec.search_context_size is not None:
            payload["search_context_size"] = spec.search_context_size
        if spec.user_location is not None:
            payload["user_location"] = dict(spec.user_location)
        return payload
    if isinstance(spec, FileSearch):
        payload = {"type": "file_search", "vector_store_ids": list(spec.vector_store_ids)}
        if spec.max_num_results is not None:
            payload["max_num_results"] = spec.max_num_results
        if spec.filters is not None:
            payload["filters"] = dict(spec.filters)
        return payload
    if isinstance(spec, CodeInterpreter):
        payload = {"type": "code_interpreter"}
        if spec.container_id is not None:
            payload["container"] = {"type": "container_id", "container_id": spec.container_id}
        return payload
    if isinstance(spec, RemoteMCP):
        if spec.denied_tools:
            raise UnsupportedFeatureError(
                "OpenAI RemoteMCP mapping supports allowed_tools, but not denied_tools."
            )
        payload = {
            "type": "mcp",
            "server_label": spec.server_label,
            "server_url": spec.server_url,
        }
        if spec.server_description is not None:
            payload["server_description"] = spec.server_description
        if spec.authorization is not None:
            payload["authorization"] = spec.authorization
        if spec.allowed_tools is not None:
            payload["allowed_tools"] = list(spec.allowed_tools)
        if spec.require_approval is not None:
            payload["require_approval"] = spec.require_approval
        payload.update(spec.extra)
        return payload
    return dict(spec.payload)


def to_gemini_tool(spec: HostedToolSpec) -> dict[str, Any]:
    if isinstance(spec, WebSearch):
        return {"google_search": {}}
    if isinstance(spec, HostedToolRaw):
        return dict(spec.payload)
    raise UnsupportedFeatureError(
        f"Gemini hosted tool mapping is not implemented for {type(spec).__name__}."
    )


def to_anthropic_tool(spec: HostedToolSpec) -> dict[str, Any]:
    if isinstance(spec, RemoteMCP):
        payload: dict[str, Any] = {
            "type": "mcp_toolset",
            "mcp_server_name": spec.server_label,
        }
        default_config: dict[str, Any] = {}
        configs: dict[str, dict[str, Any]] = {
            name: dict(config) for name, config in spec.tool_configs.items()
        }
        if spec.allowed_tools:
            default_config["enabled"] = False
            for name in spec.allowed_tools:
                configs.setdefault(name, {})["enabled"] = True
        if spec.denied_tools:
            for name in spec.denied_tools:
                configs.setdefault(name, {})["enabled"] = False
        if spec.defer_loading is not None:
            default_config["defer_loading"] = spec.defer_loading
        if default_config:
            payload["default_config"] = default_config
        if configs:
            payload["configs"] = configs
        if spec.cache_control is not None:
            payload["cache_control"] = dict(spec.cache_control)
        return payload
    if isinstance(spec, HostedToolRaw):
        return dict(spec.payload)
    raise UnsupportedFeatureError(
        f"Anthropic hosted tool mapping is not implemented for {type(spec).__name__}."
    )


def anthropic_mcp_servers(specs: list[HostedToolSpec]) -> list[dict[str, Any]]:
    servers: list[dict[str, Any]] = []
    for spec in specs:
        if not isinstance(spec, RemoteMCP):
            continue
        server: dict[str, Any] = {
            "type": "url",
            "url": spec.server_url,
            "name": spec.server_label,
        }
        if spec.authorization is not None:
            server["authorization_token"] = spec.authorization
        servers.append(server)
    return servers


def anthropic_beta_values(specs: list[HostedToolSpec]) -> list[str]:
    if any(isinstance(spec, RemoteMCP) for spec in specs):
        return ["mcp-client-2025-11-20"]
    return []


def to_raw_hosted_tool(spec: HostedToolSpec, *, provider: str) -> dict[str, Any]:
    if isinstance(spec, HostedToolRaw):
        return dict(spec.payload)
    raise UnsupportedFeatureError(
        f"{provider} hosted tool mapping is not implemented for {type(spec).__name__}."
    )


def openai_include_values(specs: list[HostedToolSpec]) -> list[str]:
    include: list[str] = []
    if any(isinstance(spec, FileSearch) and spec.include_results for spec in specs):
        include.append("file_search_call.results")
    return include
