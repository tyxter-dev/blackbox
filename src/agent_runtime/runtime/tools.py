from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agent_runtime.planning.prompts import PromptFragment
from agent_runtime.tools.registry import ToolCallable, ToolDefinition, ToolRegistry
from agent_runtime.tools.runtime import ToolRuntime
from agent_runtime.tools.session import ToolSession


@dataclass(slots=True)
class ToolRuntimeFacade:
    """High-level facade exposing the local tool registry on AgentRuntime.

    Wraps a ``ToolRegistry`` and a default ``ToolRuntime`` so applications can
    register tools without touching the underlying classes.
    """

    registry: ToolRegistry = field(default_factory=ToolRegistry)
    default_runtime: ToolRuntime = field(init=False)

    def __post_init__(self) -> None:
        self.default_runtime = ToolRuntime(self.registry)

    def register(
        self,
        function: ToolCallable,
        *,
        name: str | None = None,
        description: str | None = None,
        parameters: dict[str, Any] | None = None,
        category: str | None = None,
        tags: list[str] | None = None,
        blocking: bool = False,
        metadata: dict[str, Any] | None = None,
        prompt_fragments: list[PromptFragment] | None = None,
    ) -> ToolDefinition:
        return self.registry.register(
            function,
            name=name,
            description=description,
            parameters=parameters,
            category=category,
            tags=tags,
            blocking=blocking,
            metadata=metadata,
            prompt_fragments=prompt_fragments,
        )

    def get(self, name: str) -> ToolDefinition:
        return self.registry.get(name)

    def all_tools(self) -> list[ToolDefinition]:
        return self.registry.all_tools()

    def to_provider_tools(self) -> list[dict[str, Any]]:
        return self.registry.to_provider_tools()

    def session(self) -> ToolSession:
        """Create an isolated tool registry seeded with the global tools."""
        return ToolSession.from_registry(self.registry)

    async def call(
        self, name: str, arguments: dict[str, Any] | None = None, *, mock: bool = False
    ) -> Any:
        return await self.default_runtime.call(name, arguments, mock=mock)

    def register_workspace(
        self,
        workspace: Any,
        ref: Any,
        *,
        prefix: str = "workspace",
    ) -> list[ToolDefinition]:
        """Register provider-backed workspace operations as model-callable tools."""
        from agent_runtime.workspaces.tools import register_workspace_tools

        return register_workspace_tools(self.register, workspace, ref, prefix=prefix)
