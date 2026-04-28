from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Literal

from agent_runtime.core.events import AgentEvent, EventTypes
from agent_runtime.planning.prompts import PromptFragment
from agent_runtime.tools.catalog import ToolCatalog
from agent_runtime.tools.registry import ToolCallable, ToolDefinition, ToolRegistry
from agent_runtime.tools.results import ToolResult

ToolSelection = Literal["static", "dynamic", "auto"]


@dataclass(slots=True, frozen=True)
class ToolBudget:
    """Runtime limits for a model-visible tool surface."""

    max_tools_visible: int | None = 32
    max_tool_calls: int | None = None
    max_parallel_calls: int | None = None
    large_catalog_threshold: int = 16
    search_result_limit: int = 10


class Toolset:
    """Collection of local runtime tools that can be routed as a unit."""

    def __init__(
        self,
        *,
        name: str | None = None,
        description: str | None = None,
        tools: list[ToolDefinition] | None = None,
    ) -> None:
        self.name = name or self.__class__.__name__
        self.description = description
        self.registry = ToolRegistry()
        for tool in tools or []:
            self.registry.add(tool)

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

    def all_tools(self) -> list[ToolDefinition]:
        return self.registry.all_tools()


@dataclass(slots=True)
class DynamicToolsetSession:
    """Mutable model-visible tool surface for a single runtime run."""

    registry: ToolRegistry
    catalog: ToolCatalog
    budget: ToolBudget
    visible_names: set[str] = field(default_factory=set)
    core_names: set[str] = field(default_factory=set)
    rejected_tools: list[dict[str, Any]] = field(default_factory=list)

    def install_meta_tools(self) -> None:
        self.registry.register(
            self.search_tools,
            name="search_tools",
            description=(
                "Search the runtime tool catalog. Use this when the current visible "
                "tools are not enough for the task."
            ),
            parameters=SEARCH_TOOLS_PARAMETERS,
            category="system",
            tags=["tool-search", "dynamic"],
            risk="low",
            scopes=["tools:read"],
            metadata={"agent_runtime_dynamic_meta": True},
        )
        self.registry.register(
            self.load_tools,
            name="load_tools",
            description=(
                "Load catalog tools into the current model-visible tool set. "
                "Call loaded tools directly on the next turn."
            ),
            parameters=LOAD_TOOLS_PARAMETERS,
            category="system",
            tags=["tool-search", "dynamic"],
            risk="low",
            scopes=["tools:load"],
            metadata={"agent_runtime_dynamic_meta": True},
        )
        self.visible_names.update({"search_tools", "load_tools"})
        self.core_names.update({"search_tools", "load_tools"})

    def provider_tools(self) -> list[dict[str, Any]]:
        tools = self.registry.to_provider_tools()
        return [tool for tool in tools if tool.get("name") in self.visible_names]

    def initial_events(self) -> list[AgentEvent]:
        events = [
            AgentEvent(
                type=EventTypes.TOOL_CHOICE_SELECTED,
                data={
                    "name": name,
                    "reason": "initially_visible",
                    "dynamic": True,
                },
            )
            for name in sorted(self.visible_names)
        ]
        events.extend(
            AgentEvent(
                type=EventTypes.TOOL_CHOICE_REJECTED,
                data=dict(rejection),
            )
            for rejection in self.rejected_tools
        )
        return events

    def search_tools(
        self,
        query: str | None = None,
        category: str | None = None,
        scopes: list[str] | None = None,
        risk: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> ToolResult:
        result_limit = min(limit or self.budget.search_result_limit, self.budget.search_result_limit)
        entries = self.catalog.search(
            query=query,
            category=category,
            scopes=scopes,
            risk=risk,
            limit=result_limit,
            offset=offset,
        )
        results = [
            entry.to_summary(loaded=entry.name in self.visible_names)
            for entry in entries
            if entry.name not in self.core_names
        ]
        body: dict[str, Any] = {
            "results": results,
            "total_found": len(results),
            "total_matched": self.catalog.last_search_total,
            "available_categories": self.catalog.list_categories(),
            "hint": "Call load_tools with the tool names needed, then use them directly.",
        }
        if query:
            body["query"] = query
        if category:
            body["category"] = category
        if scopes:
            body["scopes"] = scopes
        if risk:
            body["risk"] = risk
        if self.catalog.last_search_total > offset + len(results):
            body["has_more"] = True
            body["next_offset"] = offset + len(results)
        return ToolResult(
            content=json.dumps(body, indent=2),
            payload=body,
            metadata={"query": query, "category": category, "scopes": scopes, "risk": risk},
            events=[
                AgentEvent(
                    type=EventTypes.TOOL_SEARCH_COMPLETED,
                    data={
                        "query": query,
                        "category": category,
                        "scopes": scopes,
                        "risk": risk,
                        "result_count": len(results),
                        "total_matched": self.catalog.last_search_total,
                    },
                )
            ],
        )

    def load_tools(self, tool_names: list[str]) -> ToolResult:
        loaded: list[str] = []
        already_loaded: list[str] = []
        invalid: list[str] = []
        failed_limit: list[str] = []
        events: list[AgentEvent] = []

        for name in tool_names:
            if not self.catalog.has_entry(name):
                invalid.append(name)
                continue
            if name in self.visible_names:
                already_loaded.append(name)
                continue
            if (
                self.budget.max_tools_visible is not None
                and len(self.visible_names) >= self.budget.max_tools_visible
            ):
                failed_limit.append(name)
                events.append(
                    AgentEvent(
                        type=EventTypes.TOOL_CHOICE_REJECTED,
                        data={
                            "name": name,
                            "reason": "max_tools_visible_exceeded",
                            "limit": self.budget.max_tools_visible,
                        },
                    )
                )
                continue
            self.visible_names.add(name)
            loaded.append(name)
            events.append(
                AgentEvent(
                    type=EventTypes.TOOL_CHOICE_LOADED,
                    data={"name": name, "reason": "load_tools"},
                )
            )
            events.append(
                AgentEvent(
                    type=EventTypes.TOOL_CHOICE_SELECTED,
                    data={"name": name, "reason": "loaded", "dynamic": True},
                )
            )

        body: dict[str, Any] = {
            "loaded": loaded,
            "already_loaded": already_loaded,
            "invalid": invalid,
            "failed_limit": failed_limit,
            "active_count": len(self.visible_names),
            "hint": "Loaded tools are visible on the next turn. Use them directly.",
        }
        return ToolResult(
            content=json.dumps(body, indent=2),
            payload=body,
            metadata={"requested": tool_names, "loaded": loaded},
            events=events,
        )


SEARCH_TOOLS_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "query": {
            "type": ["string", "null"],
            "description": "Search keywords for tool names, descriptions, tags, scopes, or examples.",
        },
        "category": {
            "type": ["string", "null"],
            "description": "Optional exact category filter.",
        },
        "scopes": {
            "type": ["array", "null"],
            "items": {"type": "string"},
            "description": "Optional scopes every result must provide.",
        },
        "risk": {
            "type": ["string", "null"],
            "description": "Optional exact risk filter such as low, medium, or high.",
        },
        "limit": {
            "type": ["integer", "null"],
            "description": "Maximum result count.",
        },
        "offset": {
            "type": "integer",
            "description": "Number of matches to skip for pagination.",
            "default": 0,
        },
    },
    "required": [],
}

LOAD_TOOLS_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "tool_names": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Tool names to make visible for the next model turn.",
        }
    },
    "required": ["tool_names"],
}
