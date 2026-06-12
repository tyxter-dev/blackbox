"""Reference schedule executor for workspace agent packages.

``ScheduleSpec`` is declarative; this module provides the execution bridge
that turns due schedules into runs. It is dependency-free: cron expressions
are parsed by a built-in five-field parser (minute, hour, day-of-month,
month, day-of-week with ``*``, lists, ranges, and ``/step``), and interval
expressions accept ``90``/``90s``/``15m``/``2h``/``1d`` forms.

Semantics:

- ``cron``: due at the next matching wall-clock minute after the last run
  (or after tracking starts). Standard vixie-cron OR rule applies when both
  day-of-month and day-of-week are restricted. ``ScheduleTrigger.timezone``
  selects the wall clock via ``zoneinfo``; default is UTC.
- ``interval``: first due one interval after tracking starts, then every
  interval after each run.
- ``manual``: never due automatically; run it with :meth:`ScheduleExecutor.run_now`.
- ``calendar``: not supported by the reference executor; such schedules are
  reported by :meth:`ScheduleExecutor.unsupported_schedules` and otherwise
  ignored.

Deterministic by construction: every method takes ``now`` explicitly, so
applications can drive it from an external cron/loop, and tests can replay
time. :meth:`ScheduleExecutor.serve` is the optional built-in polling loop.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from blackbox.core.policy import PolicyRequest
from blackbox.workspace_agents.runtime import run_workspace_agent
from blackbox.workspace_agents.schedules import ScheduledRunRef, ScheduleSpec, ScheduleTrigger
from blackbox.workspace_agents.spec import WorkspaceAgentSpec

DEFAULT_SCHEDULE_INPUT = "Run your scheduled task now."

_MINUTE = timedelta(minutes=1)
_SEARCH_LIMIT = timedelta(days=366 * 4)
_INTERVAL_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


class ScheduleExpressionError(ValueError):
    """Raised for unparseable cron or interval expressions."""


# --- expression parsing -----------------------------------------------------


def parse_interval(expression: str | None) -> timedelta:
    """Parse ``90``, ``90s``, ``15m``, ``2h``, or ``1d`` into a timedelta."""

    if expression is None or not expression.strip():
        raise ScheduleExpressionError("Interval trigger requires an expression.")
    text = expression.strip().lower()
    unit = 1
    if text and text[-1] in _INTERVAL_UNITS:
        unit = _INTERVAL_UNITS[text[-1]]
        text = text[:-1]
    try:
        value = float(text)
    except ValueError as exc:
        raise ScheduleExpressionError(f"Invalid interval expression: {expression!r}") from exc
    if value <= 0:
        raise ScheduleExpressionError(f"Interval must be positive: {expression!r}")
    return timedelta(seconds=value * unit)


def _parse_cron_field(field_text: str, minimum: int, maximum: int, *, name: str) -> set[int]:
    values: set[int] = set()
    for chunk in field_text.split(","):
        chunk = chunk.strip()
        if not chunk:
            raise ScheduleExpressionError(f"Empty {name} entry in cron field {field_text!r}.")
        step = 1
        if "/" in chunk:
            chunk, step_text = chunk.split("/", 1)
            try:
                step = int(step_text)
            except ValueError as exc:
                raise ScheduleExpressionError(f"Invalid step in {name}: {step_text!r}") from exc
            if step < 1:
                raise ScheduleExpressionError(f"Step must be >= 1 in {name}: {step}")
        if chunk in {"*", ""}:
            start, end = minimum, maximum
        elif "-" in chunk:
            start_text, end_text = chunk.split("-", 1)
            try:
                start, end = int(start_text), int(end_text)
            except ValueError as exc:
                raise ScheduleExpressionError(f"Invalid range in {name}: {chunk!r}") from exc
        else:
            try:
                start = end = int(chunk)
            except ValueError as exc:
                raise ScheduleExpressionError(f"Invalid value in {name}: {chunk!r}") from exc
        if start < minimum or end > maximum or start > end:
            raise ScheduleExpressionError(
                f"{name} value out of range [{minimum}, {maximum}]: {chunk!r}"
            )
        values.update(range(start, end + 1, step))
    return values


@dataclass(slots=True, frozen=True)
class _CronSchedule:
    minutes: set[int]
    hours: set[int]
    days_of_month: set[int]
    months: set[int]
    days_of_week: set[int]
    dom_restricted: bool
    dow_restricted: bool

    def matches(self, moment: datetime) -> bool:
        if moment.minute not in self.minutes or moment.hour not in self.hours:
            return False
        if moment.month not in self.months:
            return False
        dom_ok = moment.day in self.days_of_month
        dow_ok = moment.isoweekday() % 7 in self.days_of_week  # 0 = Sunday
        if self.dom_restricted and self.dow_restricted:
            return dom_ok or dow_ok
        return dom_ok and dow_ok


def parse_cron(expression: str | None) -> _CronSchedule:
    """Parse a five-field cron expression."""

    if expression is None or not expression.strip():
        raise ScheduleExpressionError("Cron trigger requires an expression.")
    fields = expression.split()
    if len(fields) != 5:
        raise ScheduleExpressionError(
            f"Cron expression must have 5 fields, got {len(fields)}: {expression!r}"
        )
    minute, hour, dom, month, dow = fields
    dow_values = _parse_cron_field(dow.replace("7", "0"), 0, 6, name="day-of-week")
    return _CronSchedule(
        minutes=_parse_cron_field(minute, 0, 59, name="minute"),
        hours=_parse_cron_field(hour, 0, 23, name="hour"),
        days_of_month=_parse_cron_field(dom, 1, 31, name="day-of-month"),
        months=_parse_cron_field(month, 1, 12, name="month"),
        days_of_week=dow_values,
        dom_restricted=dom.strip() != "*",
        dow_restricted=dow.strip() != "*",
    )


def next_cron_run(
    expression: str,
    *,
    after: datetime,
    timezone: str | None = None,
) -> datetime:
    """Return the first matching wall-clock minute strictly after ``after`` (UTC)."""

    schedule = parse_cron(expression)
    tz = ZoneInfo(timezone) if timezone else UTC
    after_utc = after if after.tzinfo is not None else after.replace(tzinfo=UTC)
    candidate = (after_utc + _MINUTE).astimezone(tz).replace(second=0, microsecond=0)
    limit = after_utc + _SEARCH_LIMIT
    while candidate.astimezone(UTC) <= limit:
        if candidate.month not in schedule.months:
            # Jump to the first minute of the next month.
            year, month = candidate.year, candidate.month + 1
            if month > 12:
                year, month = year + 1, 1
            candidate = candidate.replace(
                year=year, month=month, day=1, hour=0, minute=0
            )
            continue
        if schedule.matches(candidate):
            return candidate.astimezone(UTC)
        if candidate.hour not in schedule.hours:
            candidate = candidate.replace(minute=0) + timedelta(hours=1)
        else:
            candidate += _MINUTE
    raise ScheduleExpressionError(
        f"Cron expression never matches within the search window: {expression!r}"
    )


def next_run_at(
    trigger: ScheduleTrigger,
    *,
    after: datetime,
) -> datetime | None:
    """Next due time strictly after ``after``; ``None`` when never automatic."""

    if trigger.kind == "cron":
        return next_cron_run(trigger.expression or "", after=after, timezone=trigger.timezone)
    if trigger.kind == "interval":
        return after + parse_interval(trigger.expression)
    return None


# --- executor ----------------------------------------------------------------


@dataclass(slots=True)
class _ScheduleState:
    spec: WorkspaceAgentSpec
    schedule: ScheduleSpec
    next_due: datetime | None


@dataclass(slots=True)
class ScheduleExecutor:
    """Runs due workspace agent schedules through the high-level runtime."""

    runtime: Any
    registry: Any
    policy: Any | None = None
    default_input: str = DEFAULT_SCHEDULE_INPUT
    _states: dict[tuple[str, str], _ScheduleState] = field(default_factory=dict)
    _stop: asyncio.Event = field(default_factory=asyncio.Event)

    async def refresh(self, *, now: datetime) -> None:
        """Sync tracked schedules with the registry's current agents."""

        seen: set[tuple[str, str]] = set()
        for spec in await self.registry.list():
            for schedule in spec.schedules:
                key = (spec.id, schedule.name)
                seen.add(key)
                state = self._states.get(key)
                if state is None:
                    self._states[key] = _ScheduleState(
                        spec=spec,
                        schedule=schedule,
                        next_due=(
                            next_run_at(schedule.trigger, after=now)
                            if schedule.enabled
                            else None
                        ),
                    )
                else:
                    state.spec = spec
                    state.schedule = schedule
                    if not schedule.enabled:
                        state.next_due = None
                    elif state.next_due is None and schedule.enabled:
                        state.next_due = next_run_at(schedule.trigger, after=now)
        for key in set(self._states) - seen:
            del self._states[key]

    def unsupported_schedules(self) -> list[tuple[str, str, str]]:
        """Report (agent_id, schedule, kind) entries this executor cannot trigger."""

        return [
            (state.spec.id, state.schedule.name, state.schedule.trigger.kind)
            for state in self._states.values()
            if state.schedule.trigger.kind == "calendar" and state.schedule.enabled
        ]

    async def run_due(self, *, now: datetime | None = None) -> list[ScheduledRunRef]:
        """Refresh from the registry and execute every schedule due at ``now``."""

        moment = now if now is not None else datetime.now(UTC)
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=UTC)
        await self.refresh(now=moment)
        refs: list[ScheduledRunRef] = []
        for state in list(self._states.values()):
            if state.next_due is None or state.next_due > moment:
                continue
            scheduled_for = state.next_due
            state.next_due = next_run_at(state.schedule.trigger, after=moment)
            refs.append(
                await self._execute(state.spec, state.schedule, scheduled_for=scheduled_for)
            )
        return refs

    async def run_now(self, agent_id: str, schedule_name: str) -> ScheduledRunRef:
        """Trigger one schedule immediately (the path for ``manual`` triggers)."""

        spec = await self.registry.get(agent_id)
        for schedule in spec.schedules:
            if schedule.name == schedule_name:
                return await self._execute(spec, schedule, scheduled_for=datetime.now(UTC))
        raise KeyError(f"Agent {agent_id!r} has no schedule named {schedule_name!r}.")

    async def serve(self, *, poll_seconds: float = 30.0) -> None:
        """Polling loop for deployments without an external scheduler."""

        self._stop.clear()
        while not self._stop.is_set():
            await self.run_due()
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=poll_seconds)
            except TimeoutError:
                continue

    def stop(self) -> None:
        self._stop.set()

    async def _execute(
        self,
        spec: WorkspaceAgentSpec,
        schedule: ScheduleSpec,
        *,
        scheduled_for: datetime,
    ) -> ScheduledRunRef:
        base_metadata: dict[str, Any] = {
            "trigger_kind": schedule.trigger.kind,
            "expression": schedule.trigger.expression,
            "scheduled_for": scheduled_for.isoformat(),
        }
        if self.policy is not None:
            decision = await self.policy.check(
                PolicyRequest(
                    checkpoint="before_scheduled_run",
                    action=f"{spec.id}:{schedule.name}",
                    arguments={"agent": spec.name, "schedule": schedule.name},
                )
            )
            if decision.verdict != "allow":
                return ScheduledRunRef(
                    agent_id=spec.id,
                    schedule_name=schedule.name,
                    status="skipped",
                    metadata={**base_metadata, "reason": decision.reason or decision.verdict},
                )
        try:
            result: Any = await run_workspace_agent(
                self.runtime,
                spec,
                input=schedule.input or self.default_input,
            )
        except Exception as exc:
            return ScheduledRunRef(
                agent_id=spec.id,
                schedule_name=schedule.name,
                status="failed",
                metadata={**base_metadata, "error": str(exc), "error_type": type(exc).__name__},
            )
        run_id = result.events[0].run_id if result.events else None
        return ScheduledRunRef(
            agent_id=spec.id,
            schedule_name=schedule.name,
            status="completed",
            run_id=run_id,
            metadata={**base_metadata, "output_text": result.text},
        )
