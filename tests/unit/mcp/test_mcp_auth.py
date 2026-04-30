from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from email.message import Message
from typing import Any

import pytest

from agent_runtime.core.errors import MCPAuthenticationError
from agent_runtime.mcp import (
    MCPAuthChallenge,
    MCPAuthToken,
    MCPServerSpec,
    OAuthBearerMCPAuthProvider,
    StaticBearerMCPAuthProvider,
    StreamableHTTPMCPTransport,
)


class _TokenProvider:
    def __init__(self) -> None:
        self.calls: list[tuple[bool, MCPAuthChallenge | None]] = []

    async def token_for(
        self,
        spec: MCPServerSpec,
        *,
        challenge: MCPAuthChallenge | None = None,
        force_refresh: bool = False,
    ) -> MCPAuthToken:
        self.calls.append((force_refresh, challenge))
        return MCPAuthToken(
            access_token="fresh" if force_refresh else "old",
            expires_at=time.time() + 300,
            scope=challenge.scope if challenge is not None else None,
        )


class _Response:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.headers: dict[str, str] = {"Content-Type": "application/json"}
        self._payload = payload

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")


def _auth_error(
    status: int = 401,
    *,
    header: str = 'Bearer resource_metadata="https://auth.example.com/.well-known/oauth", scope="tickets.read"',
) -> urllib.error.HTTPError:
    headers = Message()
    headers["WWW-Authenticate"] = header
    return urllib.error.HTTPError(
        "https://mcp.example.com/mcp",
        status,
        "auth failed",
        headers,
        None,
    )


async def test_oauth_auth_provider_refreshes_token_after_http_challenge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token_provider = _TokenProvider()
    auth_provider = OAuthBearerMCPAuthProvider(token_provider)
    spec = MCPServerSpec(
        name="tickets",
        transport="streamable_http",
        url="https://mcp.example.com/mcp",
    )
    transport = StreamableHTTPMCPTransport(spec=spec, auth_provider=auth_provider)
    seen_headers: list[str | None] = []

    def fake_urlopen(
        request: urllib.request.Request,
        timeout: float,
    ) -> _Response:
        seen_headers.append(request.get_header("Authorization"))
        if seen_headers[-1] == "Bearer old":
            raise _auth_error()
        return _Response(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "result": {"tools": []},
            }
        )

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    result = await transport.request("tools/list")

    assert result == {"tools": []}
    assert seen_headers == ["Bearer old", "Bearer fresh"]
    assert [call[0] for call in token_provider.calls] == [False, True]
    assert token_provider.calls[1][1] is not None
    assert token_provider.calls[1][1].scope == "tickets.read"
    assert (
        token_provider.calls[1][1].resource_metadata_url
        == "https://auth.example.com/.well-known/oauth"
    )


async def test_http_auth_failure_raises_typed_error_without_token_leak(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    auth_provider = StaticBearerMCPAuthProvider("secret-token")
    spec = MCPServerSpec(
        name="tickets",
        transport="streamable_http",
        url="https://mcp.example.com/mcp",
    )
    transport = StreamableHTTPMCPTransport(spec=spec, auth_provider=auth_provider)

    def fake_urlopen(
        request: urllib.request.Request,
        timeout: float,
    ) -> _Response:
        raise _auth_error()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(MCPAuthenticationError) as exc_info:
        await transport.request("tools/list")

    exc = exc_info.value
    assert exc.server == "tickets"
    assert exc.status_code == 401
    assert exc.scope == "tickets.read"
    assert exc.safe_to_retry is False
    assert "secret-token" not in str(exc)
    assert "secret-token" not in repr(auth_provider)


async def test_http_auth_failure_without_refresh_provider_is_retryable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec = MCPServerSpec(
        name="tickets",
        transport="streamable_http",
        url="https://mcp.example.com/mcp",
    )
    transport = StreamableHTTPMCPTransport(spec=spec)

    def fake_urlopen(
        request: urllib.request.Request,
        timeout: float,
    ) -> _Response:
        raise _auth_error(status=403, header='Bearer scope="tickets.write"')

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(MCPAuthenticationError) as exc_info:
        await transport.request("tools/list")

    assert exc_info.value.server == "tickets"
    assert exc_info.value.status_code == 403
    assert exc_info.value.scope == "tickets.write"
    assert exc_info.value.safe_to_retry is True
