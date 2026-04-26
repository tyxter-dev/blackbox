from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from agent_runtime.core.artifacts import Artifact
from agent_runtime.core.policy import Policy


@dataclass(slots=True, frozen=True)
class HostedToolCall:
    provider: str
    hosted_tool_type: str
    provider_item_type: str
    call_id: str
    item_id: str | None = None
    status: str | None = None
    arguments: dict[str, Any] = field(default_factory=dict)
    raw: Any | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class HostedToolOutput:
    provider: str
    hosted_tool_type: str
    call_id: str
    status: Literal["completed", "failed", "denied"]
    content: str | None = None
    provider_input_item: dict[str, Any] | None = None
    artifacts: list[Artifact] = field(default_factory=list)
    payload: dict[str, Any] = field(default_factory=dict)
    raw: Any | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class HostedToolContext:
    run_id: str
    session_id: str | None
    provider: str
    model: str
    policy: Policy | None
    workspace_runtime: Any | None = None
    workspace_ref: Any | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class HostedToolHandler(Protocol):
    @property
    def hosted_tool_type(self) -> str: ...

    async def handle(
        self,
        call: HostedToolCall,
        context: HostedToolContext,
    ) -> HostedToolOutput: ...


class ShellHandler(HostedToolHandler, Protocol):
    pass


class ApplyPatchHandler(HostedToolHandler, Protocol):
    pass


class ComputerUseHandler(HostedToolHandler, Protocol):
    pass


class TextEditorHandler(HostedToolHandler, Protocol):
    pass
