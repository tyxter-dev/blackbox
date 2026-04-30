from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from blackbox.core.artifacts import Artifact
from blackbox.core.events import AgentEvent


@dataclass(slots=True, frozen=True)
class ToolResult:
    """Local tool result.

    ``content`` is safe to send back to a model or cloud agent. ``payload`` is for
    application code and should not be automatically exposed to a model.
    """

    content: str
    payload: Any | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    events: list[AgentEvent] = field(default_factory=list)
    artifacts: list[Artifact] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.error is None
