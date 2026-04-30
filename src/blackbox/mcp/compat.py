from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from blackbox.core.errors import MCPError
from blackbox.mcp.spec import MCPServerSpec, MCPTransport

MCPProtocolStatus = Literal["supported", "deprecated"]


@dataclass(slots=True, frozen=True)
class MCPProtocolVersionSupport:
    """Runtime support declaration for one MCP protocol version."""

    version: str
    status: MCPProtocolStatus
    transports: tuple[MCPTransport, ...]
    notes: str = ""


MCP_PROTOCOL_COMPATIBILITY_MATRIX: tuple[MCPProtocolVersionSupport, ...] = (
    MCPProtocolVersionSupport(
        version="2025-11-25",
        status="supported",
        transports=("stdio", "http", "sse", "streamable_http"),
        notes="Default production MCP protocol target.",
    ),
    MCPProtocolVersionSupport(
        version="2025-06-18",
        status="supported",
        transports=("stdio", "http", "sse", "streamable_http"),
        notes="Supported fallback for servers that have not upgraded yet.",
    ),
    MCPProtocolVersionSupport(
        version="2025-03-26",
        status="deprecated",
        transports=("stdio", "http", "sse", "streamable_http"),
        notes="Compatibility fallback; prefer newer protocol versions when possible.",
    ),
)

SUPPORTED_MCP_PROTOCOL_VERSIONS: tuple[str, ...] = tuple(
    entry.version for entry in MCP_PROTOCOL_COMPATIBILITY_MATRIX
)


def mcp_protocol_matrix() -> tuple[MCPProtocolVersionSupport, ...]:
    """Return the immutable MCP protocol compatibility matrix."""

    return MCP_PROTOCOL_COMPATIBILITY_MATRIX


def supported_protocol_versions_for(spec: MCPServerSpec) -> tuple[str, ...]:
    """Return spec-requested protocol versions supported by this runtime."""

    requested = (spec.protocol_version, *spec.protocol_version_fallbacks)
    return tuple(version for version in requested if is_mcp_protocol_version_supported(version))


def is_mcp_protocol_version_supported(version: str) -> bool:
    return version in SUPPORTED_MCP_PROTOCOL_VERSIONS


def validate_requested_protocol_versions(spec: MCPServerSpec) -> tuple[str, ...]:
    supported = supported_protocol_versions_for(spec)
    if not supported:
        requested = ", ".join((spec.protocol_version, *spec.protocol_version_fallbacks))
        raise MCPError(
            "MCP server requested no runtime-supported protocol versions "
            f"for '{spec.name}': {requested}"
        )
    return supported


def validate_negotiated_protocol_version(
    version: str,
    *,
    spec: MCPServerSpec,
) -> str:
    supported = validate_requested_protocol_versions(spec)
    if version not in supported:
        raise MCPError(f"MCP server negotiated unsupported protocol version: {version}")
    return version
