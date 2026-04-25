"""Pluggable policy gate for the runtime.

A :class:`Policy` is consulted at well-defined checkpoints in the loop. v0.1
ships only a minimal Protocol plus an adapter that wraps the existing
``approval_policy`` callable. Declarative rule engines arrive in P2.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable

PolicyCheckpoint = Literal[
    "before_model_request",
    "before_tool_call",
    "before_mcp_call",
    "before_workspace_write",
    "before_command",
    "before_artifact_export",
    "before_final_output",
]

PolicyVerdict = Literal["allow", "deny", "require_approval"]


@dataclass(slots=True)
class PolicyRequest:
    checkpoint: PolicyCheckpoint
    action: str
    arguments: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class PolicyDecision:
    verdict: PolicyVerdict
    reason: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def allow(cls, reason: str | None = None) -> PolicyDecision:
        return cls(verdict="allow", reason=reason)

    @classmethod
    def deny(cls, reason: str | None = None) -> PolicyDecision:
        return cls(verdict="deny", reason=reason)

    @classmethod
    def require_approval(cls, reason: str | None = None) -> PolicyDecision:
        return cls(verdict="require_approval", reason=reason)


@runtime_checkable
class Policy(Protocol):
    async def check(self, request: PolicyRequest) -> PolicyDecision: ...


@dataclass(slots=True)
class AllowAllPolicy:
    """Default policy: every action is allowed."""

    async def check(self, request: PolicyRequest) -> PolicyDecision:
        return PolicyDecision.allow()


@dataclass(slots=True)
class ApprovalCallbackPolicy:
    """Adapter that turns a ``(name, arguments) -> bool`` callable into a Policy.

    Used internally so the existing ``approval_policy=`` argument keeps working
    while new code can target the Policy Protocol directly.
    """

    require_approval: Callable[[str, dict[str, Any]], bool]

    async def check(self, request: PolicyRequest) -> PolicyDecision:
        if request.checkpoint == "before_tool_call" and self.require_approval(
            request.action, request.arguments
        ):
            return PolicyDecision.require_approval()
        return PolicyDecision.allow()
