from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from agent_runtime.core.errors import ToolExecutionError
from agent_runtime.planning.prompts import FragmentSelector, PromptFragment
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
    risk: str | None = None
    scopes: list[str] = field(default_factory=list)
    latency: str | None = None
    cost: str | None = None
    side_effects: list[str] = field(default_factory=list)
    examples: list[str] = field(default_factory=list)
    negative_examples: list[str] = field(default_factory=list)
    blocking: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)
    prompt_fragments: list[PromptFragment] = field(default_factory=list)


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
        tool_name = name or function.__name__
        definition = ToolDefinition(
            name=tool_name,
            description=description or inspect.getdoc(function) or tool_name,
            function=function,
            parameters=parameters or {"type": "object", "properties": {}},
            category=category,
            tags=tags or [],
            risk=risk,
            scopes=scopes or [],
            latency=latency,
            cost=cost,
            side_effects=side_effects or [],
            examples=examples or [],
            negative_examples=negative_examples or [],
            blocking=blocking,
            metadata=metadata or {},
            prompt_fragments=[
                _scope_prompt_fragment(tool_name, fragment)
                for fragment in prompt_fragments or []
            ],
        )
        self._tools[tool_name] = definition
        return definition

    def get(self, name: str) -> ToolDefinition:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise ToolExecutionError(f"Unknown tool: {name}") from exc

    def add(self, definition: ToolDefinition) -> ToolDefinition:
        self._tools[definition.name] = definition
        return definition

    def all_tools(self) -> list[ToolDefinition]:
        return list(self._tools.values())

    def clone(self) -> ToolRegistry:
        clone = ToolRegistry()
        clone._tools = dict(self._tools)
        return clone

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
                "metadata": _tool_metadata(tool),
            }
            for tool in self.all_tools()
        ]


def _scope_prompt_fragment(tool_name: str, fragment: PromptFragment) -> PromptFragment:
    selector = fragment.applies_to
    if tool_name in selector.tools:
        return fragment
    return PromptFragment(
        id=fragment.id,
        text=fragment.text,
        source=fragment.source,
        priority=fragment.priority,
        cacheable=fragment.cacheable,
        applies_to=FragmentSelector(
            tools=frozenset({*selector.tools, tool_name}),
            tool_tags=selector.tool_tags,
            hosted_tool_kinds=selector.hosted_tool_kinds,
            mcp_servers=selector.mcp_servers,
            mcp_tools=selector.mcp_tools,
            workspace_kinds=selector.workspace_kinds,
            providers=selector.providers,
            models=selector.models,
            channels=selector.channels,
            output_strategies=selector.output_strategies,
            dynamic_loading_modes=selector.dynamic_loading_modes,
            context_flags=selector.context_flags,
        ),
        requires=fragment.requires,
        placement=fragment.placement,
        conflict_group=fragment.conflict_group,
        metadata=fragment.metadata,
    )


def _tool_metadata(tool: ToolDefinition) -> dict[str, Any]:
    metadata = dict(tool.metadata)
    if tool.risk is not None:
        metadata["risk"] = tool.risk
    if tool.scopes:
        metadata["scopes"] = list(tool.scopes)
    if tool.latency is not None:
        metadata["latency"] = tool.latency
    if tool.cost is not None:
        metadata["cost"] = tool.cost
    if tool.side_effects:
        metadata["side_effects"] = list(tool.side_effects)
    if tool.examples:
        metadata["examples"] = list(tool.examples)
    if tool.negative_examples:
        metadata["negative_examples"] = list(tool.negative_examples)
    return metadata
