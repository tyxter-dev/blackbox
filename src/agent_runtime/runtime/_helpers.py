from __future__ import annotations

from dataclasses import replace
from typing import Any

from agent_runtime.core.accounting import ModelUsage
from agent_runtime.core.artifacts import Artifact, ArtifactRef
from agent_runtime.core.cache import (
    ProviderCacheStore,
    cache_usage_from_usage,
    record_provider_cache_usage,
)
from agent_runtime.core.events import AgentEvent, EventTypes
from agent_runtime.core.items import RunItem
from agent_runtime.core.results import AgentSessionResultStatus
from agent_runtime.core.sessions import AgentSession
from agent_runtime.core.state import ProviderState
from agent_runtime.providers.base import ModelCacheControl, TaskSpec


def _agent_task_spec(
    task: str | TaskSpec,
    *,
    model: str | None,
    workspace: Any | None,
    inputs: list[ArtifactRef] | None,
    metadata: dict[str, Any] | None,
    extra: dict[str, Any] | None,
) -> TaskSpec:
    if isinstance(task, TaskSpec):
        return replace(
            task,
            model=model if model is not None else task.model,
            workspace=workspace if workspace is not None else task.workspace,
            inputs=list(inputs) if inputs is not None else list(task.inputs),
            metadata={**task.metadata, **dict(metadata or {})},
            extra={**task.extra, **dict(extra or {})},
        )
    return TaskSpec(
        prompt=task,
        model=model,
        workspace=workspace,
        inputs=list(inputs or []),
        metadata=dict(metadata or {}),
        extra=dict(extra or {}),
    )


def _agent_event_text_delta(event: AgentEvent) -> str | None:
    if event.type != EventTypes.MODEL_TEXT_DELTA:
        return None
    return _first_text(event.data, "delta", "message", "text")


def _agent_event_text(event: AgentEvent) -> str | None:
    return _first_text(event.data, "output", "message", "text", "result")


def _first_text(data: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = data.get(key)
        if isinstance(value, str):
            return value
    return None


def _artifact_from_event(event: AgentEvent) -> Artifact | None:
    artifact = event.data.get("artifact")
    if isinstance(artifact, Artifact):
        return artifact
    if not isinstance(artifact, dict):
        return None
    metadata = dict(artifact.get("metadata") or {})
    metadata.setdefault("event_id", event.id)
    if event.provider is not None:
        metadata.setdefault("provider", event.provider)
    artifact_kwargs: dict[str, Any] = {
        "type": str(artifact.get("type", "provider_ref")),
        "name": str(
            artifact.get("name")
            or artifact.get("path")
            or artifact.get("id")
            or "artifact"
        ),
        "data": artifact.get("data"),
        "uri": artifact.get("uri"),
        "metadata": metadata,
    }
    if artifact.get("id") is not None:
        artifact_kwargs["id"] = str(artifact["id"])
    return Artifact(**artifact_kwargs)


def _append_artifact_unique(
    artifacts: list[Artifact],
    artifact_ids: set[str],
    artifact: Artifact,
) -> None:
    if artifact.id in artifact_ids:
        return
    artifact_ids.add(artifact.id)
    artifacts.append(artifact)


def _agent_session_result_status(
    session: AgentSession,
    events: list[AgentEvent],
) -> AgentSessionResultStatus:
    for event in reversed(events):
        if _agent_event_is_timeout(event):
            return "timeout"
        if event.type == EventTypes.SESSION_COMPLETED:
            return "completed"
        if event.type == EventTypes.SESSION_FAILED:
            return "failed"
        if event.type == EventTypes.SESSION_CANCELLED:
            return "cancelled"

    if session.status == "completed":
        return "completed"
    if session.status == "failed":
        return "failed"
    if session.status == "cancelled":
        return "cancelled"
    if session.status == "waiting":
        return "waiting_for_approval"
    return "failed"


def _agent_event_is_timeout(event: AgentEvent) -> bool:
    if event.type.endswith(".timeout") or event.type.endswith(".timed_out"):
        return True
    status = event.data.get("status")
    reason = event.data.get("reason")
    error = event.data.get("error")
    return status == "timeout" or reason == "timeout" or error == "timeout"


def _provider_state_from_session(
    session: AgentSession,
    *,
    events: list[AgentEvent],
) -> ProviderState:
    metadata = {key: value for key, value in session.metadata.items() if key != "raw"}
    continuation: dict[str, Any] = {
        "session_id": session.id,
        "status": session.status,
    }
    if session.agent_id is not None:
        continuation["agent_id"] = session.agent_id
    if events:
        metadata_last_event_id = metadata.get("last_event_id")
        last_event = (
            next(
                (event for event in reversed(events) if event.id == metadata_last_event_id),
                None,
            )
            if isinstance(metadata_last_event_id, str)
            else None
        ) or events[-1]
        continuation["last_event_id"] = last_event.id
        continuation["last_sequence"] = last_event.sequence
        continuation["run_id"] = last_event.run_id
    if metadata:
        continuation["metadata"] = metadata
    return ProviderState(
        provider=session.provider,
        conversation_id=session.id,
        continuation=continuation,
    )


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
