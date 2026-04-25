from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal
from uuid import uuid4

ApprovalStatus = Literal["pending", "approved", "denied"]


@dataclass(slots=True, frozen=True)
class ApprovalRequest:
    action: str
    reason: str | None = None
    data: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: f"approval_{uuid4().hex}")


@dataclass(slots=True, frozen=True)
class ApprovalDecision:
    approved: bool
    reason: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def approve(cls, reason: str | None = None) -> "ApprovalDecision":
        return cls(approved=True, reason=reason)

    @classmethod
    def deny(cls, reason: str | None = None) -> "ApprovalDecision":
        return cls(approved=False, reason=reason)
