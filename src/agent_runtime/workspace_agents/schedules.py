from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal
from uuid import uuid4

ScheduleTriggerKind = Literal["manual", "interval", "cron", "calendar"]
ScheduledRunStatus = Literal["queued", "running", "completed", "failed", "cancelled", "skipped"]


@dataclass(slots=True, frozen=True)
class ScheduleTrigger:
    kind: ScheduleTriggerKind = "manual"
    expression: str | None = None
    timezone: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class ScheduleSpec:
    """Declarative schedule. Execution is owned by downstream schedulers."""

    name: str
    trigger: ScheduleTrigger = field(default_factory=ScheduleTrigger)
    input: str | None = None
    enabled: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class ScheduledRunRef:
    agent_id: str
    schedule_name: str
    status: ScheduledRunStatus = "queued"
    id: str = field(default_factory=lambda: f"scheduled_run_{uuid4().hex}")
    run_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
