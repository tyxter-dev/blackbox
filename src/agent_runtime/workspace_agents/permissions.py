from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from agent_runtime.core.policy import PolicyCheckpoint, PolicyRequest

ConnectorAuthMode = Literal["end_user", "agent_owned"]
PermissionScope = Literal["read", "write", "delete", "execute", "admin", "custom"]
ApprovalMode = Literal["never", "on_write", "on_execute", "always", "policy"]


@dataclass(slots=True, frozen=True)
class ApprovalRequirement:
    """Declarative approval requirement for a packaged agent capability."""

    mode: ApprovalMode = "policy"
    reason: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def requires_approval_for(self, scope: PermissionScope) -> bool:
        if self.mode == "always":
            return True
        if self.mode == "never":
            return False
        if self.mode == "on_write":
            return scope in {"write", "delete", "admin"}
        if self.mode == "on_execute":
            return scope in {"execute", "admin"}
        return False


@dataclass(slots=True, frozen=True)
class ConnectorSpec:
    """External connector a workspace agent may use.

    ``end_user`` means each caller brings their own authorization. ``agent_owned``
    means the agent package points at a shared service/account credential managed
    by the downstream application.
    """

    name: str
    kind: str
    auth_mode: ConnectorAuthMode = "end_user"
    scopes: list[str] = field(default_factory=list)
    tool_refs: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class ToolPermission:
    """Permission declaration for a local, hosted, MCP, or connector-backed tool."""

    ref: str
    scopes: list[PermissionScope] = field(default_factory=lambda: ["read"])
    connector: str | None = None
    approval: ApprovalRequirement = field(default_factory=ApprovalRequirement)
    metadata: dict[str, Any] = field(default_factory=dict)

    def allows(self, scope: PermissionScope) -> bool:
        return "admin" in self.scopes or scope in self.scopes

    def to_policy_request(
        self,
        *,
        agent_id: str,
        checkpoint: PolicyCheckpoint,
        action: str | None = None,
        arguments: dict[str, Any] | None = None,
    ) -> PolicyRequest:
        return PolicyRequest(
            checkpoint=checkpoint,
            action=action or self.ref,
            arguments=arguments or {},
            metadata={
                "agent_id": agent_id,
                "tool_ref": self.ref,
                "connector": self.connector,
                "scopes": list(self.scopes),
                "approval_mode": self.approval.mode,
                **self.metadata,
            },
        )
