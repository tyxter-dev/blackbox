from __future__ import annotations

from typing import Any

from agent_runtime.core.accounting import ModelUsage
from agent_runtime.core.cache import (
    ProviderCacheStore,
    cache_usage_from_usage,
    record_provider_cache_usage,
)
from agent_runtime.core.events import AgentEvent, EventTypes
from agent_runtime.core.items import RunItem
from agent_runtime.providers.base import ModelCacheControl


def _mcp_metadata_from_events(events: list[AgentEvent]) -> dict[str, Any]:
    tool_lists: list[dict[str, Any]] = []
    calls: list[dict[str, Any]] = []
    approvals: list[dict[str, Any]] = []
    for event in events:
        if event.type == EventTypes.MCP_LIST_TOOLS_COMPLETED:
            tools = event.data.get("tools")
            tool_names = _tool_names(tools)
            tool_lists.append(
                {
                    "server_label": event.data.get("server_label"),
                    "tool_count": len(tool_names),
                    "tool_names": tool_names,
                    "item_id": event.item_id,
                }
            )
        elif event.type == EventTypes.MCP_CALL_COMPLETED:
            calls.append(
                {
                    "server_label": event.data.get("server_label"),
                    "name": event.data.get("name"),
                    "item_id": event.item_id,
                    "failed": event.data.get("error") is not None
                    or event.data.get("is_error") is True,
                }
            )
        elif event.type == EventTypes.MCP_APPROVAL_REQUIRED:
            approvals.append(
                {
                    "server_label": event.data.get("server_label"),
                    "name": event.data.get("name"),
                    "item_id": event.item_id,
                }
            )
    if not (tool_lists or calls or approvals):
        return {}
    metadata: dict[str, Any] = {
        "tool_lists": tool_lists,
        "calls": calls,
        "approval_requests": approvals,
    }
    if tool_lists:
        metadata["tool_list_cacheable"] = True
        metadata["context_item_ids"] = [
            item["item_id"] for item in tool_lists if item.get("item_id") is not None
        ]
    return metadata


def _hosted_tool_metadata_from_events(events: list[AgentEvent]) -> dict[str, Any]:
    calls: list[dict[str, Any]] = []
    completed: list[dict[str, Any]] = []
    for event in events:
        item = event.data.get("item")
        item_type = event.data.get("hosted_tool_type")
        if item_type is None and isinstance(item, RunItem):
            item_type = item.data.get("hosted_tool_type")
        if not isinstance(item_type, str):
            continue
        summary = {
            "type": item_type,
            "item_type": event.data.get("item_type"),
            "item_id": event.item_id,
            "status": event.data.get("status")
            or (item.status if isinstance(item, RunItem) else None),
        }
        if "query" in event.data:
            summary["query"] = event.data["query"]
        if "call_id" in event.data:
            summary["call_id"] = event.data["call_id"]
        if "results" in event.data:
            summary["result_count"] = len(event.data["results"]) if isinstance(
                event.data["results"], list
            ) else None
        if event.type == EventTypes.MODEL_ITEM_COMPLETED:
            completed.append(summary)
        else:
            calls.append(summary)
    if not (calls or completed):
        return {}
    return {
        "calls": calls,
        "completed": completed,
        "types": sorted(
            {
                item["type"]
                for item in [*calls, *completed]
                if isinstance(item.get("type"), str)
            }
        ),
    }


def _prompt_metadata_from_events(events: list[AgentEvent]) -> dict[str, Any]:
    for event in reversed(events):
        if event.type != EventTypes.PROMPT_BUNDLE_CREATED:
            continue
        metadata = event.data.get("metadata")
        return dict(metadata) if isinstance(metadata, dict) else {}
    return {}


def _tool_choice_metadata_from_events(events: list[AgentEvent]) -> dict[str, Any]:
    selected: list[str] = []
    loaded: list[str] = []
    called: list[str] = []
    rejected: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    for event in events:
        if event.type == EventTypes.TOOL_CHOICE_SELECTED:
            name = event.data.get("name")
            if isinstance(name, str):
                selected.append(name)
        elif event.type == EventTypes.TOOL_CHOICE_LOADED:
            name = event.data.get("name")
            if isinstance(name, str):
                loaded.append(name)
        elif event.type == EventTypes.TOOL_CHOICE_CALLED:
            name = event.data.get("name")
            if isinstance(name, str):
                called.append(name)
        elif event.type == EventTypes.TOOL_CHOICE_REJECTED:
            rejected.append(dict(event.data))
        elif event.type == EventTypes.TOOL_CHOICE_FAILED:
            failed.append(dict(event.data))
    if not (selected or loaded or called or rejected or failed):
        return {}
    return {
        "selected": selected,
        "loaded": loaded,
        "called": called,
        "rejected": rejected,
        "failed": failed,
    }


def _tool_routing_metadata_from_events(events: list[AgentEvent]) -> dict[str, Any]:
    plans: list[dict[str, Any]] = []
    late_bound: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    mode: str | None = None
    for event in events:
        if event.type == EventTypes.TOOL_ROUTING_COMPLETED:
            event_mode = event.data.get("mode")
            if isinstance(event_mode, str):
                mode = event_mode
            plans.append(
                {
                    "iteration": event.data.get("iteration"),
                    "selected_refs": list(event.data.get("selected_refs") or []),
                    "blocked_refs": list(event.data.get("blocked_refs") or []),
                    "selected_names": list(event.data.get("selected_names") or []),
                    "fingerprint": event.data.get("fingerprint"),
                    "candidate_count": event.data.get("candidate_count"),
                    "selected_count": event.data.get("selected_count"),
                    "budget": dict(event.data.get("budget") or {}),
                }
            )
        elif event.type == EventTypes.TOOL_ROUTING_LATE_BOUND:
            late_bound.append(dict(event.data))
        elif event.type == EventTypes.TOOL_ROUTING_FAILED:
            failed.append(dict(event.data))
    if not (plans or late_bound or failed):
        return {}
    return {
        "mode": mode,
        "plans": plans,
        "late_bound": late_bound,
        "failed": failed,
    }


async def _provider_cache_metadata(
    *,
    provider: str | None,
    model: str | None,
    cache: ModelCacheControl | None,
    usage: Any,
    provider_cache_store: ProviderCacheStore,
) -> dict[str, Any]:
    if provider is None or model is None:
        return {}
    cache_usage = cache_usage_from_usage(
        provider=provider,
        model=model,
        usage=usage,
        requested=cache is not None,
        key=cache.key if cache is not None else None,
        provider_cache_id=cache.cached_content if cache is not None else None,
        strategy=cache.strategy if cache is not None else None,
        ttl=cache.ttl if cache is not None else None,
    )
    if cache_usage is None:
        return {}
    metadata = cache_usage.to_dict()
    if cache is not None:
        record = await record_provider_cache_usage(
            provider_cache_store,
            provider=provider,
            model=model,
            key=cache.key,
            provider_cache_id=cache.cached_content,
            strategy=cache.strategy,
            ttl=cache.ttl,
            usage=usage,
        )
        if record is not None:
            metadata["record"] = record.to_summary()
    return metadata


def _tool_usage_from_events(events: list[AgentEvent]) -> ModelUsage | None:
    tool_calls = _tool_call_count(events)
    if tool_calls == 0:
        return None
    return ModelUsage(tool_calls=tool_calls)


def _tool_call_count(events: list[AgentEvent]) -> int:
    counted_types = {
        EventTypes.TOOL_CALL_REQUESTED,
        EventTypes.HOSTED_TOOL_CALL_REQUESTED,
        EventTypes.MCP_CALL_STARTED,
        EventTypes.TOOL_SEARCH_REQUESTED,
    }
    seen: set[tuple[str, str]] = set()
    for event in events:
        if event.type not in counted_types:
            continue
        key = _event_call_key(event)
        seen.add((event.type, key))
    return len(seen)


def _event_call_key(event: AgentEvent) -> str:
    for key in ("call_id", "id", "name"):
        value = event.data.get(key)
        if isinstance(value, str) and value:
            return value
    if event.item_id:
        return event.item_id
    return event.id


def _attach_accounting_metadata(metadata: dict[str, Any]) -> None:
    accounting: dict[str, Any] = {}
    for key in ("usage", "cost", "cache"):
        value = metadata.get(key)
        if value is not None:
            accounting[key] = value
    if accounting:
        metadata["accounting"] = accounting


def _event_usage(event: AgentEvent) -> Any:
    usage = event.data.get("usage")
    details = event.data.get("usage_provider_details")
    if isinstance(usage, dict) and isinstance(details, dict) and details:
        return {**usage, "provider_details": details}
    return usage


def _tool_names(tools: Any) -> list[str]:
    if not isinstance(tools, list):
        return []
    names: list[str] = []
    for tool in tools:
        name = tool.get("name") if isinstance(tool, dict) else getattr(tool, "name", None)
        if isinstance(name, str):
            names.append(name)
    return names
