from __future__ import annotations

import time
from dataclasses import dataclass, field
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


@runtime_checkable
class MCPOAuthTokenProvider(Protocol):
    """Supplies OAuth bearer tokens for remote HTTP MCP servers."""

    async def token_for(
        self,
        spec: MCPServerSpec,
        *,
        challenge: MCPAuthChallenge | None = None,
        force_refresh: bool = False,
    ) -> MCPAuthToken | str: ...


@dataclass(slots=True, frozen=True)
class MCPAuthChallenge:
    server: str
    status_code: int
    www_authenticate: str | None = None
    resource_metadata_url: str | None = None
    scope: str | None = None


@dataclass(slots=True, frozen=True, repr=False)
class MCPAuthToken:
    access_token: str
    token_type: str = "Bearer"
    expires_at: float | None = None
    scope: str | None = None

    def expired(self, *, skew_seconds: float = 60.0) -> bool:
        if self.expires_at is None:
            return False
        return time.time() >= self.expires_at - skew_seconds

    def authorization_header(self) -> str:
        return f"{self.token_type} {self.access_token}"

    def __repr__(self) -> str:
        return (
            "MCPAuthToken("
            f"token_type={self.token_type!r}, "
            f"expires_at={self.expires_at!r}, "
            f"scope={self.scope!r}, "
            "access_token='<redacted>')"
        )


@dataclass(slots=True, frozen=True, repr=False)
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

    def __repr__(self) -> str:
        return "StaticBearerMCPAuthProvider(token='<redacted>')"


@dataclass(slots=True)
class OAuthBearerMCPAuthProvider:
    """Caches and refreshes OAuth bearer tokens for HTTP MCP requests."""

    token_provider: MCPOAuthTokenProvider
    refresh_skew_seconds: float = 60.0
    _tokens: dict[str, MCPAuthToken] = field(default_factory=dict, repr=False)

    async def headers_for(self, spec: MCPServerSpec) -> dict[str, str]:
        token = self._tokens.get(spec.name)
        if token is None or token.expired(skew_seconds=self.refresh_skew_seconds):
            token = await self._fetch_token(spec, challenge=None, force_refresh=False)
        return {"Authorization": token.authorization_header()}

    async def handle_auth_challenge(
        self,
        spec: MCPServerSpec,
        challenge: MCPAuthChallenge,
    ) -> dict[str, str] | None:
        token = await self._fetch_token(spec, challenge=challenge, force_refresh=True)
        return {"Authorization": token.authorization_header()}

    def cache_identity_for(self, spec: MCPServerSpec) -> str | None:
        if spec.auth_identity:
            return spec.auth_identity
        identity_for = getattr(self.token_provider, "cache_identity_for", None)
        if callable(identity_for):
            value = identity_for(spec)
            return value if isinstance(value, str) and value else None
        return None

    async def _fetch_token(
        self,
        spec: MCPServerSpec,
        *,
        challenge: MCPAuthChallenge | None,
        force_refresh: bool,
    ) -> MCPAuthToken:
        raw = await self.token_provider.token_for(
            spec,
            challenge=challenge,
            force_refresh=force_refresh,
        )
        token = raw if isinstance(raw, MCPAuthToken) else MCPAuthToken(access_token=raw)
        self._tokens[spec.name] = token
        return token


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
