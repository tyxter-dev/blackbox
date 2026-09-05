"""Immutable package constraints carried by the current run/session context."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from blackbox.core.errors import ConfigurationError, UnsupportedFeatureError
from blackbox.core.policy import PolicyDecision, PolicyRequest

if TYPE_CHECKING:
    from blackbox.tools.registry import ToolDefinition


@dataclass(frozen=True, slots=True)
class ToolGrant:
    ref: str
    scopes: frozenset[str]
    connector: str | None
    connector_scopes: frozenset[str]
    approval_mode: str
    approval_reason: str | None


@dataclass(frozen=True, slots=True)
class PackagePermissions:
    grants: tuple[ToolGrant, ...]

    def decide(self, request: PolicyRequest, *, approvals: bool = True) -> PolicyDecision:
        metadata = request.metadata
        ref = str(metadata.get("tool_ref") or metadata.get("ref") or request.action)
        ref = canonical_ref(ref)
        scopes = frozenset(
            metadata.get("permission_scopes") or metadata.get("scopes") or ["execute"]
        )
        connector = metadata.get("connector")
        connector_scopes = frozenset(metadata.get("connector_scopes") or ())
        for grant in self.grants:
            if grant.ref != ref or grant.connector != connector:
                continue
            if "admin" not in grant.scopes and not scopes <= grant.scopes:
                continue
            if not connector_scopes <= grant.connector_scopes:
                continue
            needs_approval = (
                grant.approval_mode == "always"
                or (
                    grant.approval_mode == "on_write"
                    and bool(scopes & {"write", "delete", "admin"})
                )
                or (grant.approval_mode == "on_execute" and bool(scopes & {"execute", "admin"}))
            )
            if approvals and needs_approval and request.checkpoint != "before_tool_exposure":
                return PolicyDecision.require_approval(grant.approval_reason)
            return PolicyDecision.allow()
        return PolicyDecision.deny(
            f"Package permission denied {ref!r} for scopes {sorted(scopes)!r}."
        )


_ACTIVE: ContextVar[tuple[PackagePermissions, ...]] = ContextVar(
    "blackbox_package_permissions", default=()
)


def active_permissions() -> tuple[PackagePermissions, ...]:
    return _ACTIVE.get()


@contextmanager
def permission_boundary(permissions: tuple[PackagePermissions, ...]) -> Iterator[None]:
    token = _ACTIVE.set(permissions)
    try:
        yield
    finally:
        _ACTIVE.reset(token)


def canonical_ref(ref: str) -> str:
    ref = {"hosted:bash": "hosted:shell", "hosted:computer_use": "hosted:computer"}.get(ref, ref)
    return ref if ":" in ref else f"local:{ref}"


def package_decision(request: PolicyRequest, *, approvals: bool = True) -> PolicyDecision:
    approval: PolicyDecision | None = None
    for permissions in active_permissions():
        decision = permissions.decide(request, approvals=approvals)
        if decision.verdict == "deny":
            return decision
        if decision.verdict == "require_approval":
            approval = decision
    return approval or PolicyDecision.allow()


def tool_request(
    definition: ToolDefinition,
    *,
    checkpoint: str = "before_tool_exposure",
    arguments: dict[str, Any] | None = None,
) -> PolicyRequest:
    from typing import cast

    from blackbox.core.policy import PolicyCheckpoint

    metadata = dict(definition.metadata)
    if definition.name.startswith("mcp:"):
        ref = definition.name
    elif definition.category == "workspace":
        operation = (
            metadata.get("workspace_operation")
            or definition.name.partition("_")[2]
            or definition.name
        )
        ref = f"workspace:{operation}"
    else:
        ref = canonical_ref(definition.name)
    scopes = list(definition.scopes)
    return PolicyRequest(
        checkpoint=cast(PolicyCheckpoint, checkpoint),
        action=definition.name,
        arguments=dict(arguments or {}),
        metadata={
            "category": definition.category,
            "tags": list(definition.tags),
            "risk": definition.risk,
            "side_effects": list(definition.side_effects),
            "latency": definition.latency,
            "cost": definition.cost,
            **(
                {
                    "server": definition.name[4:].partition(".")[0],
                    "tool": definition.name[4:].partition(".")[2],
                }
                if definition.name.startswith("mcp:")
                else {}
            ),
            "tool_metadata": metadata,
            "tool_ref": ref,
            "ref": ref,
            "scopes": scopes,
            "permission_scopes": scopes or ["execute"],
            "connector": metadata.get("connector"),
            "connector_scopes": list(metadata.get("connector_scopes") or []),
        },
    )


def internal_discovery_tool(definition: ToolDefinition) -> bool:
    from blackbox.tools.toolsets import DynamicToolsetSession

    owner = getattr(definition.function, "__self__", None)
    function = getattr(definition.function, "__func__", None)
    return isinstance(owner, DynamicToolsetSession) and function in {
        DynamicToolsetSession.search_tools,
        DynamicToolsetSession.load_tools,
    }


def definition_allowed(definition: ToolDefinition) -> bool:
    return (
        internal_discovery_tool(definition)
        or package_decision(tool_request(definition)).verdict != "deny"
    )


def validate_package_model_config(hosted_tools: list[Any], extra: dict[str, Any]) -> list[Any]:
    """Allow client-executed hosted contracts; reject opaque native execution."""
    if not active_permissions():
        return hosted_tools
    from blackbox.tools.hosted.specs import (
        ApplyPatch,
        ComputerUse,
        Memory,
        Shell,
        TextEditor,
        WebSearch,
    )

    if extra:
        raise ConfigurationError("allowlist_v1 does not support provider-native extra parameters.")
    allowed: list[Any] = []
    for spec in hosted_tools:
        if isinstance(spec, WebSearch):
            decision = package_decision(
                hosted_request("web_search", checkpoint="before_hosted_tool_config")
            )
            if decision.verdict == "require_approval":
                raise UnsupportedFeatureError("WebSearch cannot enforce package per-call approval.")
            if decision.verdict == "allow":
                allowed.append(spec)
            continue
        if not isinstance(spec, ApplyPatch | ComputerUse | Memory | TextEditor | Shell):
            raise UnsupportedFeatureError(
                "allowlist_v1 cannot enforce provider-executed hosted tools or RemoteMCP."
            )
        if isinstance(spec, Shell) and spec.execution != "local":
            raise UnsupportedFeatureError("allowlist_v1 requires Shell(execution='local').")
        kind = {
            ApplyPatch: "apply_patch",
            ComputerUse: "computer",
            Memory: "memory",
            TextEditor: "text_editor",
            Shell: "shell",
        }[type(spec)]
        request = hosted_request(kind, checkpoint="before_tool_exposure")
        decision = package_decision(request)
        if decision.verdict != "deny":
            allowed.append(spec)
    return allowed


def hosted_request(kind: str, *, checkpoint: str = "before_hosted_tool_call") -> PolicyRequest:
    from typing import cast

    from blackbox.core.policy import PolicyCheckpoint

    return PolicyRequest(
        checkpoint=cast(PolicyCheckpoint, checkpoint),
        action=kind,
        metadata={
            "tool_ref": f"hosted:{kind}",
            "scopes": ["read"] if kind == "web_search" else ["execute"],
            "connector": None,
            "connector_scopes": [],
        },
    )


def approval_key(definition: ToolDefinition) -> tuple[str, int, str]:
    """Bind an approval to the callable and its authoritative permission requirements."""
    request = tool_request(definition)
    return definition.name, id(definition.function), repr(request.metadata)


_APPROVED: ContextVar[frozenset[tuple[str, tuple[str, ...], str | None, tuple[str, ...]]]] = (
    ContextVar("blackbox_approved_package_requests", default=frozenset())
)


def _request_key(
    request: PolicyRequest,
) -> tuple[str, tuple[str, ...], str | None, tuple[str, ...]]:
    metadata = request.metadata
    return (
        canonical_ref(str(metadata.get("tool_ref") or metadata.get("ref") or request.action)),
        tuple(sorted(metadata.get("permission_scopes") or metadata.get("scopes") or ["execute"])),
        metadata.get("connector"),
        tuple(sorted(metadata.get("connector_scopes") or [])),
    )


@contextmanager
def approved_package_call(request: PolicyRequest | None) -> Iterator[None]:
    token = _APPROVED.set(_APPROVED.get() | {_request_key(request)} if request else _APPROVED.get())
    try:
        yield
    finally:
        _APPROVED.reset(token)


def package_call_approved(request: PolicyRequest) -> bool:
    return _request_key(request) in _APPROVED.get()
