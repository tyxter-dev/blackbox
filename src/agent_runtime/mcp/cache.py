from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Any

from agent_runtime.mcp.spec import MCPServerSpec


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
) -> str:
    identity: dict[str, Any] = {
        "server": spec.name,
        "transport": spec.transport,
        "session_id": session_id,
        "protocol_version": protocol_version,
        "allowed_tools": sorted(spec.allowed_tools or ()),
        "denied_tools": sorted(spec.denied_tools or ()),
        "auth": {
            "authorization": spec.authorization is not None,
            "auth_provider_name": spec.auth_provider_name,
            "headers": sorted(spec.headers),
        },
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
