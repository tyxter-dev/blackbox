from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from agent_runtime.planning.prompts import PromptFragment
from agent_runtime.tools.registry import ToolCallable, ToolDefinition, ToolRegistry
from agent_runtime.tools.runtime import ToolRuntime


@dataclass(slots=True)
class ToolSession:
    """Isolated tool registry overlay for one run or session."""

    registry: ToolRegistry = field(default_factory=ToolRegistry)

    @classmethod
    def from_registry(cls, registry: ToolRegistry) -> ToolSession:
        return cls(registry=registry.clone())

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
        """Register a tool on this session's isolated registry overlay."""

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

    def names(self) -> list[str]:
        return [tool.name for tool in self.all_tools()]

    def to_provider_tools(self) -> list[dict[str, Any]]:
        return self.registry.to_provider_tools()

    def register_workspace(
        self,
        workspace: Any,
        ref: Any,
        *,
        prefix: str = "workspace",
    ) -> list[ToolDefinition]:
        from agent_runtime.workspaces.tools import register_workspace_tools

        return register_workspace_tools(self.register, workspace, ref, prefix=prefix)

    def runtime(
        self,
        *,
        context: Mapping[str, Any] | None = None,
        max_concurrent: int | None = None,
        timeout: float | None = None,
        allowed_tools: set[str] | None = None,
    ) -> ToolRuntime:
        return ToolRuntime(
            self.registry,
            context=context or {},
            max_concurrent=max_concurrent,
            timeout=timeout,
            allowed_tools=allowed_tools,
        )
