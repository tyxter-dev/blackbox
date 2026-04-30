from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from blackbox.planning.prompts import PromptFragment
from blackbox.tools.registry import ToolCallable, ToolDefinition, ToolRegistry
from blackbox.tools.runtime import ToolRuntime
from blackbox.tools.session import ToolSession


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
        risk: str | None = None,
        scopes: list[str] | None = None,
        latency: str | None = None,
        cost: str | None = None,
        side_effects: list[str] | None = None,
        examples: list[str] | None = None,
        negative_examples: list[str] | None = None,
        blocking: bool = False,
        metadata: dict[str, Any] | None = None,
        prompt_fragments: list[PromptFragment] | None = None,
    ) -> ToolDefinition:
        """Register a callable tool with schema, routing metadata, and prompt fragments."""

        return self.registry.register(
            function,
            name=name,
            description=description,
            parameters=parameters,
            category=category,
            tags=tags,
            risk=risk,
            scopes=scopes,
            latency=latency,
            cost=cost,
            side_effects=side_effects,
            examples=examples,
            negative_examples=negative_examples,
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
        from blackbox.workspaces.tools import register_workspace_tools

        return register_workspace_tools(self.register, workspace, ref, prefix=prefix)
