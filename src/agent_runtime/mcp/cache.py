from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class MCPToolCacheEntry:
    tools: dict[str, Any]
    cached_at: float = field(default_factory=time.monotonic)
    ttl_seconds: float | None = None

    def expired(self) -> bool:
        if self.ttl_seconds is None:
            return False
        return time.monotonic() - self.cached_at >= self.ttl_seconds
