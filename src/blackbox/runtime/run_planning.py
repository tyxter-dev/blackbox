from __future__ import annotations

from dataclasses import replace
from typing import Any

from blackbox.core.events import AgentEvent, EventTypes
from blackbox.mcp import MCPToolset
from blackbox.mcp.trust import effective_server_trust_policy, trust_fingerprint
from blackbox.planning.prompts import PromptBundle, PromptMode, PromptSpec
from blackbox.planning.run_plan import (
    DynamicToolLoadingSpec,
    ResolvedMCPToolset,
    ResolvedRunSpec,
    ResolvedTool,
)
from blackbox.providers.base import ToolSearchControl
from blackbox.tools.hosted.specs import HostedToolSpec, hosted_tool_kind
from blackbox.tools.registry import ToolRegistry


def _resolve_prompt_spec(
    prompt: PromptSpec | None,
    *,
    prompt_mode: PromptMode | None,
    channel: str | None,
) -> PromptSpec:
    prompt_spec = prompt or PromptSpec()
    if prompt_mode is not None:
        prompt_spec = replace(prompt_spec, mode=prompt_mode)
    if channel is not None:
        prompt_spec = replace(prompt_spec, channel=channel)
    return prompt_spec


def _resolved_tools(
    tool_definitions: list[dict[str, Any]],
    *,
    registry: ToolRegistry,
) -> list[ResolvedTool]:
    registered = {tool.name: tool for tool in registry.all_tools()}
    resolved: list[ResolvedTool] = []
    for payload in tool_definitions:
        name = payload.get("name")
        if not isinstance(name, str):
            continue
        definition = registered.get(name)
        metadata = payload.get("metadata")
        payload_metadata = metadata if isinstance(metadata, dict) else {}
        resolved.append(
            ResolvedTool(
                name=name,
                definition=payload,
                tags=frozenset(definition.tags if definition is not None else ()),
                category=definition.category if definition is not None else None,
                metadata={
                    **dict(definition.metadata if definition is not None else {}),
                    **payload_metadata,
                },
                prompt_fragments=tuple(
                    definition.prompt_fragments if definition is not None else ()
                ),
                hidden=payload_metadata.get("blackbox_hidden") is True,
            )
        )
    return resolved


def _resolved_mcp_toolset(
    toolset: MCPToolset,
    *,
    route: str,
    allowed_tools: list[str] | None = None,
) -> ResolvedMCPToolset:
    configured_allowed = (
        toolset.allowed_tools
        if toolset.allowed_tools is not None
        else toolset.server.allowed_tools
    )
    tools = allowed_tools if allowed_tools is not None else configured_allowed
    trust_policy = effective_server_trust_policy(toolset.server)
    return ResolvedMCPToolset(
        server_label=toolset.server.name,
        route=route,
        allowed_tools=tuple(tools or ()),
        metadata={
            "mode": toolset.mode,
            "transport": toolset.server.transport,
            "description": toolset.description,
            "trust_level": trust_policy.trust_level.value,
            "route_mode": trust_policy.route_mode.value,
            "fingerprint": trust_fingerprint(toolset.server),
        },
    )


def _dynamic_loading_from_controls(
    *,
    tool_search: ToolSearchControl | None,
    hosted_tools: list[HostedToolSpec],
    mcp_toolsets: list[ResolvedMCPToolset],
) -> DynamicToolLoadingSpec | None:
    hosted_kinds = {hosted_tool_kind(tool) for tool in hosted_tools}
    if tool_search is not None or "tool_search" in hosted_kinds:
        return DynamicToolLoadingSpec(mode="tool_search")
    if any(toolset.route == "provider_native" for toolset in mcp_toolsets):
        return DynamicToolLoadingSpec(mode="provider_native_mcp")
    return None


def _prompt_events(plan: ResolvedRunSpec, bundle: PromptBundle) -> list[AgentEvent]:
    """Build observability events for prompt planning and bundle output."""

    events = [
        AgentEvent(
            type=EventTypes.PROMPT_PLAN_CREATED,
            provider=plan.provider,
            data={
                "provider": plan.provider,
                "model": plan.model,
                "mode": bundle.metadata.get("mode"),
                "channel": bundle.metadata.get("channel"),
                "effective_tool_ids": list(plan.effective_tool_ids),
                "hosted_tool_kinds": list(plan.hosted_tool_kinds),
                "mcp_servers": list(plan.mcp_servers),
                "context_flags": list(plan.context_flags),
                "output_strategy": plan.output_strategy,
                "dynamic_loading_mode": (
                    plan.dynamic_loading.mode if plan.dynamic_loading is not None else None
                ),
            },
        )
    ]
    events.extend(
        AgentEvent(
            type=EventTypes.PROMPT_FRAGMENT_SELECTED,
            provider=plan.provider,
            data={
                "fragment_id": item.fragment.id,
                "source": item.fragment.source,
                "priority": item.fragment.priority,
                "cacheable": item.fragment.cacheable,
                "placement": item.fragment.placement,
                "matched_on": dict(item.matched_on),
            },
        )
        for item in bundle.selected_fragments
    )
    events.extend(
        AgentEvent(
            type=EventTypes.PROMPT_FRAGMENT_SKIPPED,
            provider=plan.provider,
            data={
                "fragment_id": item.fragment.id,
                "source": item.fragment.source,
                "reason": item.reason,
                "details": dict(item.details),
            },
        )
        for item in bundle.skipped_fragments
    )
    events.extend(
        AgentEvent(
            type=EventTypes.PROMPT_CACHE_SECTION_CREATED,
            provider=plan.provider,
            data={
                "section_id": section.id,
                "cacheable": section.cacheable,
                "content_length": len(section.content),
            },
        )
        for section in bundle.cache_sections
    )
    events.append(
        AgentEvent(
            type=EventTypes.PROMPT_PARITY_CHECKED,
            provider=plan.provider,
            data={
                "status": bundle.metadata.get("parity"),
                "issues": [issue.to_dict() for issue in bundle.parity_issues],
                "parity_fingerprint": bundle.parity_fingerprint,
            },
        )
    )
    events.append(
        AgentEvent(
            type=EventTypes.PROMPT_BUNDLE_CREATED,
            provider=plan.provider,
            data={
                "section_ids": [section.id for section in bundle.sections],
                "fragment_ids": [item.fragment.id for item in bundle.selected_fragments],
                "effective_tool_ids": list(bundle.effective_tool_ids),
                "prompt_fingerprint": bundle.prompt_fingerprint,
                "parity_fingerprint": bundle.parity_fingerprint,
                "metadata": dict(bundle.metadata),
            },
        )
    )
    return events
