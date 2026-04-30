from __future__ import annotations

from dataclasses import fields, is_dataclass
from typing import Any, TypeVar, cast, get_args, get_origin

from blackbox.mcp import MCPServerSpec, MCPToolset
from blackbox.workspace_agents.permissions import (
    ApprovalRequirement,
    ConnectorSpec,
    ToolPermission,
)
from blackbox.workspace_agents.schedules import (
    ScheduleSpec,
    ScheduleTrigger,
)
from blackbox.workspace_agents.spec import (
    MemorySpec,
    PublicationSpec,
    SkillBundleRef,
    WorkspaceAgentMetadata,
    WorkspaceAgentSpec,
    WorkspaceAgentVersion,
)

T = TypeVar("T")

_TYPE_REGISTRY: dict[type[Any], dict[str, type[Any]]] = {
    WorkspaceAgentSpec: {
        "connectors": ConnectorSpec,
        "permissions": ToolPermission,
        "schedules": ScheduleSpec,
        "skills": SkillBundleRef,
        "mcp_servers": MCPServerSpec,
        "mcp_toolsets": MCPToolset,
        "memory": MemorySpec,
        "publication": PublicationSpec,
        "version": WorkspaceAgentVersion,
        "metadata": WorkspaceAgentMetadata,
    },
    MCPToolset: {"server": MCPServerSpec},
    ToolPermission: {"approval": ApprovalRequirement},
    ScheduleSpec: {"trigger": ScheduleTrigger},
}


def workspace_agent_to_dict(spec: WorkspaceAgentSpec) -> dict[str, Any]:
    """Serialize a workspace agent spec to plain Python containers."""

    return _dataclass_to_dict(spec)


def workspace_agent_from_dict(data: dict[str, Any]) -> WorkspaceAgentSpec:
    """Hydrate a workspace agent spec from plain Python containers."""

    return _from_dict(WorkspaceAgentSpec, data)


def dataclass_to_dict(value: Any) -> dict[str, Any]:
    """Serialize a dataclass instance to plain Python containers."""

    if not is_dataclass(value) or isinstance(value, type):
        raise TypeError(f"Expected dataclass instance, got {type(value)!r}.")
    return _dataclass_to_dict(value)


def dataclass_from_dict(cls: type[T], data: dict[str, Any]) -> T:
    """Hydrate a dataclass instance using registered nested dataclass mappings."""

    return _from_dict(cls, data)


def _from_dict(cls: type[T], data: dict[str, Any]) -> T:
    nested = _TYPE_REGISTRY.get(cls, {})
    kwargs: dict[str, Any] = {}
    for field in fields(cast(Any, cls)):
        if not field.init:
            continue
        name = field.name
        if name not in data:
            continue
        value = data[name]
        nested_type = nested.get(name)
        if nested_type is None:
            kwargs[name] = value
        else:
            kwargs[name] = _coerce_nested(nested_type, value)
    return cls(**kwargs)


def _dataclass_to_dict(value: Any) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for field in fields(cast(Any, value)):
        if not field.init:
            continue
        result[field.name] = _plain_value(getattr(value, field.name))
    return result


def _plain_value(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return _dataclass_to_dict(value)
    if isinstance(value, list):
        return [_plain_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_plain_value(item) for item in value)
    if isinstance(value, dict):
        return {key: _plain_value(item) for key, item in value.items()}
    return value


def _coerce_nested(cls: type[Any], value: Any) -> Any:
    if isinstance(value, list):
        return [_from_dict(cls, item) if isinstance(item, dict) else item for item in value]
    if isinstance(value, dict):
        return _from_dict(cls, value)
    origin = get_origin(cls)
    if origin is not None and get_args(cls):
        return value
    return value
