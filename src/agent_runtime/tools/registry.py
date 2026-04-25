from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from agent_runtime.core.errors import ToolExecutionError
from agent_runtime.tools.results import ToolResult

ToolCallable = Callable[..., ToolResult | str | dict[str, Any] | Any]


@dataclass(slots=True)
class ToolDefinition:
    name: str
    description: str
    function: ToolCallable
    parameters: dict[str, Any] = field(default_factory=lambda: {"type": "object", "properties": {}})
    category: str | None = None
    tags: list[str] = field(default_factory=list)
    blocking: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


class ToolRegistry:
    """Local tool registry with context injection.

    This intentionally keeps the best old-library idea: injected context is not part
    of the LLM-visible schema. Context values are matched by parameter name.
    """

    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}

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
    ) -> ToolDefinition:
        tool_name = name or function.__name__
        definition = ToolDefinition(
            name=tool_name,
            description=description or inspect.getdoc(function) or tool_name,
            function=function,
            parameters=parameters or {"type": "object", "properties": {}},
            category=category,
            tags=tags or [],
            blocking=blocking,
            metadata=metadata or {},
        )
        self._tools[tool_name] = definition
        return definition

    def get(self, name: str) -> ToolDefinition:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise ToolExecutionError(f"Unknown tool: {name}") from exc

    def list(self) -> list[ToolDefinition]:
        return list(self._tools.values())

    def to_provider_tools(self) -> list[dict[str, Any]]:
        """Export local tools as provider-neutral schemas.

        Real providers should convert this into native function/tool definitions.
        """
        return [
            {
                "type": "function",
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters,
                "metadata": tool.metadata,
            }
            for tool in self.list()
        ]
