from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

MCPTransport = Literal["stdio", "http"]


@dataclass(slots=True, frozen=True)
class MCPServerSpec:
    name: str
    transport: MCPTransport
    command: str | None = None
    args: list[str] = field(default_factory=list)
    url: str | None = None
    headers: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, str] = field(default_factory=dict)
