from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from blackbox.mcp import MCPToolset
from blackbox.tools.hosted.specs import HostedToolSpec, hosted_tool_kind
from blackbox.tools.registry import ToolDefinition, ToolRegistry
from blackbox.tools.routing import ToolCandidate, ToolKind, ToolRisk, estimate_schema_tokens

_WORKSPACE_PREFIX = "workspace_"
_MCP_PREFIX = "mcp:"


def local_tool_candidates(registry: ToolRegistry) -> list[ToolCandidate]:
    return [_candidate_from_tool(tool) for tool in registry.all_tools()]


def hosted_tool_candidates(hosted_tools: Iterable[HostedToolSpec]) -> list[ToolCandidate]:
    candidates: list[ToolCandidate] = []
    for index, spec in enumerate(hosted_tools):
        kind = hosted_tool_kind(spec)
        risk = _hosted_risk(kind, spec)
        candidates.append(
            ToolCandidate(
                ref=f"hosted:{kind}",
                name=kind,
                kind="hosted",
                description=f"Provider-hosted {kind} tool.",
                category="hosted",
                tags=(kind, "hosted"),
                source="hosted_tools",
                risk=risk,
                requires_approval=_hosted_requires_approval(spec),
                metadata={"hosted_tool_index": index, "hosted_tool_kind": kind, "spec": spec},
            )
        )
    return candidates


async def mcp_tool_candidates(toolsets: Iterable[MCPToolset]) -> list[ToolCandidate]:
    """Build routing candidates for configured MCP servers or explicit MCP tools."""

    candidates: list[ToolCandidate] = []
    for toolset in toolsets:
        server = toolset.server
        configured_tools = toolset.allowed_tools or server.allowed_tools or ()
        if configured_tools:
            for name in configured_tools:
                candidates.append(
                    ToolCandidate(
                        ref=f"mcp:{server.name}.{name}",
                        name=f"mcp:{server.name}.{name}",
                        kind="mcp",
                        description=f"MCP tool {name} on server {server.name}.",
                        category="mcp",
                        tags=("mcp", server.name),
                        source=server.name,
                        provider="mcp",
                        risk=_mcp_risk(server.require_approval, {}),
                        requires_approval=server.require_approval == "always",
                        metadata={
                            "server": server.name,
                            "tool": name,
                            "route_mode": toolset.mode,
                            "transport": server.transport,
                        },
                    )
                )
            continue
        candidates.append(
            ToolCandidate(
                ref=f"mcp:{server.name}.*",
                name=f"mcp:{server.name}.*",
                kind="mcp",
                description=toolset.description or f"MCP server {server.name}.",
                category="mcp",
                tags=("mcp", server.name),
                source=server.name,
                provider="mcp",
                risk=_mcp_risk(server.require_approval, {}),
                requires_approval=server.require_approval == "always",
                metadata={
                    "server": server.name,
                    "route_mode": toolset.mode,
                    "transport": server.transport,
                    "deferred_discovery": True,
                },
            )
        )
    return candidates


def dedupe_by_ref(candidates: Iterable[ToolCandidate]) -> list[ToolCandidate]:
    deduped: dict[str, ToolCandidate] = {}
    for candidate in candidates:
        deduped.setdefault(candidate.ref, candidate)
    return list(deduped.values())


def _candidate_from_tool(tool: ToolDefinition) -> ToolCandidate:
    kind = _tool_kind(tool)
    ref = _tool_ref(tool, kind)
    payload = {
        "type": "function",
        "name": tool.name,
        "description": tool.description,
        "parameters": tool.parameters,
        "metadata": dict(tool.metadata),
    }
    return ToolCandidate(
        ref=ref,
        name=tool.name,
        kind=kind,
        description=tool.description,
        parameters=dict(tool.parameters),
        category=tool.category,
        tags=tuple(tool.tags),
        source=_tool_source(tool, kind),
        provider=_tool_provider(tool, kind),
        risk=_tool_risk(tool, kind),
        estimated_schema_tokens=estimate_schema_tokens(payload),
        requires_approval=_tool_requires_approval(tool),
        metadata={
            **dict(tool.metadata),
            "definition_name": tool.name,
            "scopes": list(tool.scopes),
            "side_effects": list(tool.side_effects),
            "latency": tool.latency,
            "cost": tool.cost,
        },
    )


def _tool_kind(tool: ToolDefinition) -> ToolKind:
    if tool.name.startswith(_MCP_PREFIX) or tool.metadata.get("mcp") is True:
        return "mcp"
    if tool.metadata.get("workspace_id") is not None or tool.category == "workspace":
        return "workspace"
    return "local"


def _tool_ref(tool: ToolDefinition, kind: str) -> str:
    metadata_ref = tool.metadata.get("route_ref") or tool.metadata.get("ref")
    if isinstance(metadata_ref, str):
        return metadata_ref
    if kind == "mcp":
        return tool.name if tool.name.startswith(_MCP_PREFIX) else f"mcp:{tool.name}"
    if kind == "workspace":
        operation = _workspace_operation(tool.name)
        return f"workspace:{operation}"
    return f"local:{tool.name}"


def _workspace_operation(name: str) -> str:
    if name.startswith(_WORKSPACE_PREFIX):
        return name[len(_WORKSPACE_PREFIX):]
    _, separator, tail = name.partition("_")
    return tail if separator else name


def _tool_source(tool: ToolDefinition, kind: str) -> str | None:
    if kind == "workspace":
        provider = tool.metadata.get("workspace_provider")
        return str(provider) if provider is not None else "workspace"
    if kind == "mcp":
        server = tool.metadata.get("server")
        return str(server) if server is not None else "mcp"
    return "registry"


def _tool_provider(tool: ToolDefinition, kind: str) -> str | None:
    if kind == "workspace":
        provider = tool.metadata.get("workspace_provider")
        return str(provider) if provider is not None else "workspace"
    if kind == "mcp":
        return "mcp"
    return None


def _tool_risk(tool: ToolDefinition, kind: str) -> ToolRisk:
    explicit = _normalize_risk(tool.risk)
    if explicit is not None:
        return explicit
    if any("secret" in scope.lower() for scope in tool.scopes):
        return "secret_access"
    side_effects = {effect.lower() for effect in tool.side_effects}
    if "network" in side_effects:
        return "network"
    if side_effects:
        return "external_side_effect"
    if kind == "workspace":
        operation = _workspace_operation(tool.name)
        if operation in {"write_file", "delete_file", "apply_patch"}:
            return "workspace_write"
        if operation in {"run_command", "run_tests"}:
            return "command"
        if operation == "expose_port":
            return "network"
    if kind == "mcp":
        return _mcp_risk("auto", tool.metadata)
    return "read_only"


def _normalize_risk(value: str | None) -> ToolRisk | None:
    if value is None:
        return None
    lowered = value.lower()
    if lowered in {"low", "read", "read_only", "readonly"}:
        return "read_only"
    if lowered in {"workspace_write", "write"}:
        return "workspace_write"
    if lowered in {"command", "shell"}:
        return "command"
    if lowered == "network":
        return "network"
    if lowered in {"secret", "secret_access"}:
        return "secret_access"
    if lowered in {"high", "external", "external_side_effect", "side_effect"}:
        return "external_side_effect"
    return None


def _tool_requires_approval(tool: ToolDefinition) -> bool:
    if tool.metadata.get("requires_approval") is True:
        return True
    return _tool_risk(tool, _tool_kind(tool)) in {
        "external_side_effect",
        "secret_access",
    }


def _mcp_risk(require_approval: str, metadata: dict[str, Any]) -> ToolRisk:
    if require_approval == "always":
        return "external_side_effect"
    if metadata.get("destructive") is True:
        return "external_side_effect"
    if metadata.get("read_only") is True:
        return "read_only"
    return "network"


def _hosted_risk(kind: str, spec: HostedToolSpec) -> ToolRisk:
    if kind in {"web_search", "web_fetch", "url_context", "remote_mcp"}:
        return "network"
    if kind in {"shell", "bash"}:
        return "command"
    if kind == "apply_patch":
        return "workspace_write"
    if kind in {"computer_use", "memory", "raw"}:
        return "external_side_effect"
    return "read_only"


def _hosted_requires_approval(spec: HostedToolSpec) -> bool:
    value = getattr(spec, "require_approval", None)
    return value is True or value == "always"
