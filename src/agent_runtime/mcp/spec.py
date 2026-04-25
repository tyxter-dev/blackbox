from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

MCPTransport = Literal["stdio", "http", "sse", "streamable_http"]


@dataclass(slots=True, frozen=True)
class MCPServerSpec:
    name: str
    transport: MCPTransport
    command: str | None = None
    args: list[str] = field(default_factory=list)
    url: str | None = None
    headers: dict[str, str] = field(default_factory=dict)
    env: dict[str, str] = field(default_factory=dict)
    cwd: str | None = None
    timeout_seconds: float = 30.0
    tool_cache_ttl_seconds: float | None = 300.0
    require_approval: bool = False
    metadata: dict[str, str] = field(default_factory=dict)
