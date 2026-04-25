from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

from agent_runtime.core.artifacts import Artifact
from agent_runtime.core.events import AgentEvent
from agent_runtime.core.items import RunItem
from agent_runtime.core.state import ProviderState

T = TypeVar("T")


@dataclass(slots=True)
class ToolPayload:
    """A single payload returned by a tool during a high-level run.

    ``content`` was already shown to the model. ``payload`` is the structured
    application-facing data and is never auto-fed back into the model.
    """

    tool_name: str
    payload: Any
    call_id: str | None = None


@dataclass(slots=True)
class AgentResult(Generic[T]):
    """Collected output of ``AgentRuntime.run(...)``.

    ``output`` carries the validated structured result when the caller passes
    an ``output_type``. Otherwise it falls back to the final text projection.
    """

    output: T
    text: str
    events: list[AgentEvent] = field(default_factory=list)
    items: list[RunItem] = field(default_factory=list)
    artifacts: list[Artifact] = field(default_factory=list)
    payloads: list[ToolPayload] = field(default_factory=list)
    provider_state: ProviderState | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
