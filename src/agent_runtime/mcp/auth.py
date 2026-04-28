from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from agent_runtime.mcp.spec import MCPServerSpec


@runtime_checkable
class MCPAuthProvider(Protocol):
    """Supplies MCP request headers and refreshes them after auth challenges."""

    async def headers_for(self, spec: MCPServerSpec) -> dict[str, str]: ...

    async def handle_auth_challenge(
        self,
        spec: MCPServerSpec,
        challenge: MCPAuthChallenge,
    ) -> dict[str, str] | None:
        """Return replacement headers for an auth challenge, or None to fail."""
        ...


@runtime_checkable
class LegacyMCPAuthProvider(Protocol):
    """Legacy auth provider contract for one Authorization header value."""

    async def authorization_header(self, server: MCPServerSpec) -> str | None: ...


@dataclass(slots=True, frozen=True)
class MCPAuthChallenge:
    server: str
    status_code: int
    www_authenticate: str | None = None
    resource_metadata_url: str | None = None
    scope: str | None = None


@dataclass(slots=True, frozen=True)
class StaticBearerMCPAuthProvider:
    token: str

    async def headers_for(self, spec: MCPServerSpec) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}

    async def handle_auth_challenge(
        self,
        spec: MCPServerSpec,
        challenge: MCPAuthChallenge,
    ) -> dict[str, str] | None:
        return await self.headers_for(spec)


async def auth_headers_for(
    provider: MCPAuthProvider | LegacyMCPAuthProvider | None,
    spec: MCPServerSpec,
) -> dict[str, str]:
    headers: dict[str, str] = {}
    if spec.authorization is not None:
        headers["Authorization"] = spec.authorization
    if provider is None:
        return headers
    headers_for = getattr(provider, "headers_for", None)
    if callable(headers_for):
        headers.update(await headers_for(spec))
        return headers
    authorization_header = getattr(provider, "authorization_header", None)
    if callable(authorization_header):
        value = await authorization_header(spec)
        if value is not None:
            headers["Authorization"] = value
    return headers
