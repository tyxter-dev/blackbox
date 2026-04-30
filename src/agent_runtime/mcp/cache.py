from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Any

from agent_runtime.mcp.spec import MCPServerSpec
from agent_runtime.mcp.trust import trust_fingerprint


@dataclass(slots=True)
class MCPToolCacheEntry:
    server: str
    tools: dict[str, Any]
    cached_at: float = field(default_factory=time.monotonic)
    ttl_seconds: float | None = None
    session_id: str | None = None
    protocol_version: str | None = None
    key: str | None = None

    def expired(self) -> bool:
        if self.ttl_seconds is None:
            return False
        return time.monotonic() - self.cached_at >= self.ttl_seconds


def mcp_tool_cache_key(
    spec: MCPServerSpec,
    *,
    session_id: str | None = None,
    protocol_version: str | None = None,
    auth_cache_identity: str | None = None,
) -> str:
    """Return a stable discovery-cache key for a server/session/protocol identity.

    Secret values are omitted while one-way auth fingerprints, tool filters,
    and endpoints still contribute to cache identity.
    """
    authorization_header = _authorization_header(spec.headers)
    identity: dict[str, Any] = {
        "server": spec.name,
        "transport": spec.transport,
        "session_id": session_id,
        "protocol_version": protocol_version,
        "allowed_tools": sorted(spec.allowed_tools or ()),
        "denied_tools": sorted(spec.denied_tools or ()),
        "auth": {
            "authorization": spec.authorization is not None,
            "authorization_hash": (
                _stable_hash(spec.authorization) if spec.authorization is not None else None
            ),
            "auth_provider_name": spec.auth_provider_name,
            "auth_identity_hash": (
                _stable_hash(auth_cache_identity)
                if auth_cache_identity is not None
                else None
            ),
            "authorization_header_hash": (
                _stable_hash(authorization_header)
                if authorization_header is not None
                else None
            ),
            "headers": sorted(spec.headers),
        },
        "trust_fingerprint": trust_fingerprint(spec),
    }
    if spec.transport == "stdio":
        identity["command"] = {
            "command": spec.command,
            "args": list(spec.args),
            "cwd": spec.cwd,
            "env_keys": sorted(spec.env),
        }
    else:
        identity["url_hash"] = _stable_hash(spec.url or "")
    return _stable_hash(identity)


def _stable_hash(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _authorization_header(headers: dict[str, str]) -> str | None:
    for key, value in headers.items():
        if key.lower() == "authorization":
            return value
    return None
