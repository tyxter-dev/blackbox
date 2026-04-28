from __future__ import annotations

from dataclasses import replace
from typing import Any, cast

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
from agent_runtime.mcp import MCPToolset
from agent_runtime.planning.prompts import PromptBundle, PromptMode, PromptSpec
from agent_runtime.planning.run_plan import (
    DynamicToolLoadingSpec,
    ResolvedMCPToolset,
    ResolvedRunSpec,
    ResolvedTool,
)
from agent_runtime.providers.base import ModelCacheControl, TaskSpec, ToolSearchControl
from agent_runtime.tools.hosted.specs import HostedToolSpec, hosted_tool_kind
from agent_runtime.tools.registry import ToolRegistry


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
                hidden=payload_metadata.get("agent_runtime_hidden") is True,
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
    return ResolvedMCPToolset(
        server_label=toolset.server.name,
        route=route,
        allowed_tools=tuple(tools or ()),
        metadata={
            "mode": toolset.mode,
            "transport": toolset.server.transport,
            "description": toolset.description,
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


def _workspace_ref_metadata(workspace: Any) -> dict[str, Any]:
    if workspace is None:
        return {}
    state = getattr(workspace, "session_state", None)
    metadata = {
        "id": getattr(workspace, "id", None),
        "kind": getattr(workspace, "kind", None),
        "provider": getattr(workspace, "provider", None),
        "root": getattr(workspace, "root", None),
        "name": getattr(workspace, "name", None),
        "provider_workspace_id": getattr(workspace, "provider_workspace_id", None),
        "provider_session_id": getattr(workspace, "provider_session_id", None),
        "metadata": dict(getattr(workspace, "metadata", {}) or {}),
    }
    if state is not None:
        from agent_runtime.workspaces.serialization import workspace_state_to_dict

        metadata["session_state"] = workspace_state_to_dict(state)
    return metadata


def _drain_workspace_events(provider: Any, workspace: Any) -> list[AgentEvent]:
    drain = getattr(provider, "drain_events", None)
    if not callable(drain):
        return []
    return [_agent_event_with_workspace(event, workspace) for event in drain()]


def _agent_event_with_workspace(event: AgentEvent, workspace: Any) -> AgentEvent:
    workspace_id = getattr(workspace, "id", None)
    workspace_kind = getattr(workspace, "kind", None)
    workspace_provider = getattr(workspace, "provider", None)
    if not isinstance(workspace_id, str):
        return event
    event_type = _canonical_workspace_event_type(event.type)
    data = dict(event.data)
    should_attach = (
        event_type.startswith("workspace.")
        or event_type == EventTypes.ARTIFACT_CREATED
        or event_type.startswith("approval.")
    )
    if should_attach:
        data.setdefault("workspace_id", workspace_id)
        data.setdefault("workspace_kind", workspace_kind)
        data.setdefault("workspace_provider", workspace_provider)
        artifact = data.get("artifact")
        if isinstance(artifact, Artifact):
            data["artifact"] = replace(
                artifact,
                metadata={
                    "workspace_id": workspace_id,
                    "workspace_kind": workspace_kind,
                    "workspace_provider": workspace_provider,
                    **dict(artifact.metadata),
                },
            )
        elif isinstance(artifact, dict):
            artifact_data = dict(artifact)
            artifact_data["metadata"] = {
                "workspace_id": workspace_id,
                "workspace_kind": workspace_kind,
                "workspace_provider": workspace_provider,
                **dict(artifact_data.get("metadata") or {}),
            }
            data["artifact"] = artifact_data
    return replace(event, type=event_type, data=data)


def _canonical_workspace_event_type(event_type: str) -> str:
    aliases = {
        "file_read": EventTypes.WORKSPACE_FILE_READ,
        "file.changed": EventTypes.WORKSPACE_FILE_CHANGED,
        "file_changed": EventTypes.WORKSPACE_FILE_CHANGED,
        "patch": EventTypes.WORKSPACE_PATCH_CREATED,
        "patch_created": EventTypes.WORKSPACE_PATCH_CREATED,
        "command_started": EventTypes.WORKSPACE_COMMAND_STARTED,
        "command_output": EventTypes.WORKSPACE_COMMAND_OUTPUT,
        "command_completed": EventTypes.WORKSPACE_COMMAND_COMPLETED,
        "test_started": EventTypes.WORKSPACE_TEST_STARTED,
        "test_completed": EventTypes.WORKSPACE_TEST_COMPLETED,
        "snapshot_created": EventTypes.WORKSPACE_SNAPSHOT_CREATED,
        "artifact": EventTypes.ARTIFACT_CREATED,
        "approval_required": EventTypes.APPROVAL_REQUESTED,
    }
    return aliases.get(event_type, event_type)


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


def _workspace_metadata_from_events(events: list[AgentEvent]) -> dict[str, Any]:
    workspaces: dict[str, dict[str, Any]] = {}
    for event in events:
        data = event.data
        metadata = data.get("metadata")
        metadata_dict = metadata if isinstance(metadata, dict) else {}
        workspace_id = data.get("workspace_id") or metadata_dict.get("workspace_id")
        if not isinstance(workspace_id, str):
            artifact = data.get("artifact")
            if isinstance(artifact, Artifact):
                artifact_workspace_id = artifact.metadata.get("workspace_id")
                if isinstance(artifact_workspace_id, str):
                    workspace_id = artifact_workspace_id
        if not isinstance(workspace_id, str):
            continue

        entry = workspaces.setdefault(
            workspace_id,
            {"artifacts": [], "snapshots": []},
        )
        provider = data.get("workspace_provider") or metadata_dict.get("workspace_provider")
        if isinstance(provider, str):
            entry["provider"] = provider
        kind = data.get("workspace_kind") or metadata_dict.get("workspace_kind")
        if isinstance(kind, str):
            entry["kind"] = kind
        session_state = data.get("session_state") or metadata_dict.get("workspace_session_state")
        if isinstance(session_state, dict):
            entry["session_state"] = session_state

        artifact = data.get("artifact")
        if isinstance(artifact, Artifact):
            artifacts = cast(list[str], entry.setdefault("artifacts", []))
            if artifact.id not in artifacts:
                artifacts.append(artifact.id)
            if artifact.type == "workspace_snapshot":
                snapshots = cast(list[str], entry.setdefault("snapshots", []))
                if artifact.id not in snapshots:
                    snapshots.append(artifact.id)
    return workspaces


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
