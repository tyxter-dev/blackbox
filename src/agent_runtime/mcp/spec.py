from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal
from urllib.parse import urlparse

from agent_runtime.mcp.trust import (
    MCPServerRiskProfile,
    MCPServerTrustPolicy,
    MCPToolTrustPolicy,
)

MCPTransport = Literal["stdio", "http", "sse", "streamable_http"]
MCPApprovalMode = Literal["always", "never", "auto"]

_SECRET_KEY_PARTS = (
    "authorization",
    "token",
    "secret",
    "password",
    "api_key",
    "apikey",
    "credential",
)


@dataclass(slots=True, frozen=True, repr=False)
class MCPServerSpec:
    """Connection and policy specification for one MCP server.

    ``stdio`` servers are launched and owned by the runtime. HTTP/SSE servers
    are remote endpoints and require explicit ``allow_remote_http`` when local
    runtime dispatch would connect outside localhost. Tool allow/deny lists
    define the runtime-visible catalog, ``require_approval`` controls tool-call
    approval policy, and auth/header fields are redacted from ``repr`` so specs
    can be logged safely.
    """

    name: str
    transport: MCPTransport
    command: str | None = None
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    cwd: str | None = None
    url: str | None = None
    headers: dict[str, str] = field(default_factory=dict)
    authorization: str | None = None
    auth_provider_name: str | None = None
    auth_identity: str | None = None
    protocol_version: str = "2025-11-25"
    protocol_version_fallbacks: tuple[str, ...] = ("2025-06-18", "2025-03-26")
    timeout_seconds: float = 30.0
    startup_timeout_seconds: float = 10.0
    shutdown_timeout_seconds: float = 5.0
    max_output_bytes: int | None = 1_000_000
    tool_cache_ttl_seconds: float | None = 300.0
    allowed_tools: list[str] | None = None
    denied_tools: list[str] | None = None
    require_approval: MCPApprovalMode = "auto"
    allow_remote_http: bool = False
    trust_policy: MCPServerTrustPolicy | None = None
    tool_policies: dict[str, MCPToolTrustPolicy] = field(default_factory=dict)
    allow_provider_native: bool = False
    require_sandbox: bool = True
    allowed_roots: tuple[str, ...] = ()
    allowed_egress_domains: tuple[str, ...] = ()
    redact: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.name or not self.name.strip():
            raise ValueError("MCP server name must be non-empty.")
        if self.transport not in {"stdio", "http", "sse", "streamable_http"}:
            raise ValueError(f"Unsupported MCP transport: {self.transport!r}.")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive.")
        if self.startup_timeout_seconds <= 0:
            raise ValueError("startup_timeout_seconds must be positive.")
        if self.shutdown_timeout_seconds <= 0:
            raise ValueError("shutdown_timeout_seconds must be positive.")
        if self.max_output_bytes is not None and self.max_output_bytes <= 0:
            raise ValueError("max_output_bytes must be positive when set.")
        if isinstance(self.require_approval, bool):
            object.__setattr__(
                self, "require_approval", "always" if self.require_approval else "auto"
            )
        if self.require_approval not in {"always", "never", "auto"}:
            raise ValueError("require_approval must be 'always', 'never', or 'auto'.")
        overlap = set(self.allowed_tools or ()).intersection(self.denied_tools or ())
        if overlap:
            names = ", ".join(sorted(overlap))
            raise ValueError(f"MCP allowed_tools and denied_tools overlap: {names}.")

    @property
    def effective_transport(self) -> Literal["stdio", "sse", "streamable_http"]:
        return "streamable_http" if self.transport == "http" else self.transport

    def validate_managed(self, *, allow_remote_http: bool | None = None) -> None:
        if self.transport == "stdio":
            if not self.command:
                raise ValueError(f"stdio MCP server '{self.name}' requires command.")
            return
        if not self.url:
            raise ValueError(f"{self.transport} MCP server '{self.name}' requires url.")
        if self.is_remote_http_url and not (
            self.allow_remote_http
            if allow_remote_http is None
            else allow_remote_http or self.allow_remote_http
        ):
            raise ValueError(
                f"Remote MCP server '{self.name}' requires allow_remote_http=True "
                "for runtime-owned local dispatch."
            )

    def validate_security(self, *, production: bool = True) -> MCPServerRiskProfile:
        from agent_runtime.mcp.security import validate_mcp_server_security

        return validate_mcp_server_security(self, production=production)

    @property
    def is_http_transport(self) -> bool:
        return self.transport in {"http", "sse", "streamable_http"}

    @property
    def is_remote_http_url(self) -> bool:
        if not self.url or not self.is_http_transport:
            return False
        parsed = urlparse(self.url)
        host = (parsed.hostname or "").lower()
        if host in {"localhost", "127.0.0.1", "::1"}:
            return False
        return parsed.scheme in {"http", "https"}

    def to_redacted_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "transport": self.transport,
            "command": self.command,
            "args": list(self.args),
            "env": redact_mapping(self.env),
            "cwd": self.cwd,
            "url": redact_url(self.url),
            "headers": redact_mapping(self.headers),
            "authorization": "<redacted>" if self.authorization else None,
            "auth_provider_name": self.auth_provider_name,
            "auth_identity": "<redacted>" if self.auth_identity else None,
            "protocol_version": self.protocol_version,
            "protocol_version_fallbacks": self.protocol_version_fallbacks,
            "timeout_seconds": self.timeout_seconds,
            "startup_timeout_seconds": self.startup_timeout_seconds,
            "shutdown_timeout_seconds": self.shutdown_timeout_seconds,
            "max_output_bytes": self.max_output_bytes,
            "tool_cache_ttl_seconds": self.tool_cache_ttl_seconds,
            "allowed_tools": list(self.allowed_tools) if self.allowed_tools is not None else None,
            "denied_tools": list(self.denied_tools) if self.denied_tools is not None else None,
            "require_approval": self.require_approval,
            "allow_remote_http": self.allow_remote_http,
            "trust_policy": (
                self.trust_policy.to_redacted_dict()
                if self.trust_policy is not None
                else None
            ),
            "tool_policies": {
                key: value.to_redacted_dict()
                for key, value in sorted(self.tool_policies.items())
            },
            "allow_provider_native": self.allow_provider_native,
            "require_sandbox": self.require_sandbox,
            "allowed_roots": self.allowed_roots,
            "allowed_egress_domains": self.allowed_egress_domains,
            "redact": self.redact,
            "metadata": redact_mapping(self.metadata),
        }

    def __repr__(self) -> str:
        args = ", ".join(f"{key}={value!r}" for key, value in self.to_redacted_dict().items())
        return f"MCPServerSpec({args})"


def redact_mapping(mapping: dict[str, Any]) -> dict[str, Any]:
    redacted: dict[str, Any] = {}
    for key, value in mapping.items():
        if _looks_secret_key(key):
            redacted[key] = "<redacted>"
        elif isinstance(value, dict):
            redacted[key] = redact_mapping(value)
        else:
            redacted[key] = value
    return redacted


def redact_url(url: str | None) -> str | None:
    if url is None:
        return None
    parsed = urlparse(url)
    if not parsed.query:
        return url
    return parsed._replace(query="<redacted>").geturl()


def _looks_secret_key(key: str) -> bool:
    lowered = key.lower()
    return any(part in lowered for part in _SECRET_KEY_PARTS)
