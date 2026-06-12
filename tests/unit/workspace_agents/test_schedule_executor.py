"""Reference schedule executor: cron/interval parsing and due-run execution."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from blackbox import AgentRuntime
from blackbox.core.policy import PolicyDecision, PolicyRequest
from blackbox.providers.model_adapters.echo import EchoModelProvider
from blackbox.workspace_agents import (
    InMemoryWorkspaceAgentRegistry,
    ScheduleExecutor,
    ScheduleExpressionError,
    ScheduleSpec,
    ScheduleTrigger,
    WorkspaceAgentSpec,
    next_cron_run,
    parse_interval,
)

# Friday 2026-06-12 15:00 UTC.
FRIDAY_1500 = datetime(2026, 6, 12, 15, 0, tzinfo=UTC)


# --- expression parsing -----------------------------------------------------

def test_next_cron_run_weekday_window() -> None:
    # "0 16 * * 1-5": weekdays at 16:00.
    assert next_cron_run("0 16 * * 1-5", after=FRIDAY_1500) == FRIDAY_1500.replace(hour=16)
    after_window = FRIDAY_1500.replace(hour=17)
    monday_1600 = datetime(2026, 6, 15, 16, 0, tzinfo=UTC)
    assert next_cron_run("0 16 * * 1-5", after=after_window) == monday_1600


def test_next_cron_run_steps_and_lists() -> None:
    assert next_cron_run("*/15 * * * *", after=FRIDAY_1500) == FRIDAY_1500.replace(minute=15)
    assert next_cron_run("5,35 9 1 7 *", after=FRIDAY_1500) == datetime(
        2026, 7, 1, 9, 5, tzinfo=UTC
    )


def test_next_cron_run_honors_timezone() -> None:
    # 09:00 in Sao Paulo (UTC-3) is 12:00 UTC.
    result = next_cron_run("0 9 * * *", after=FRIDAY_1500, timezone="America/Sao_Paulo")
    assert result == datetime(2026, 6, 13, 12, 0, tzinfo=UTC)


def test_cron_dom_dow_or_semantics() -> None:
    # Both restricted: matches when either the 13th OR a Monday arrives first.
    result = next_cron_run("0 0 13 * 1", after=FRIDAY_1500)
    assert result == datetime(2026, 6, 13, 0, 0, tzinfo=UTC)


@pytest.mark.parametrize("expression", ["", "* * * *", "61 * * * *", "* * * * 1-99", "a b c d e"])
def test_invalid_cron_expressions_raise(expression: str) -> None:
    with pytest.raises(ScheduleExpressionError):
        next_cron_run(expression, after=FRIDAY_1500)


def test_parse_interval_units() -> None:
    assert parse_interval("90") == timedelta(seconds=90)
    assert parse_interval("90s") == timedelta(seconds=90)
    assert parse_interval("15m") == timedelta(minutes=15)
    assert parse_interval("2h") == timedelta(hours=2)
    assert parse_interval("1d") == timedelta(days=1)
    with pytest.raises(ScheduleExpressionError):
        parse_interval("soon")
    with pytest.raises(ScheduleExpressionError):
        parse_interval("-5m")


# --- executor ----------------------------------------------------------------

def _agent(schedules: list[ScheduleSpec]) -> WorkspaceAgentSpec:
    return WorkspaceAgentSpec(
        name="digest",
        instructions="Prepare the daily digest.",
        model_provider="echo",
        model="echo-mini",
        schedules=schedules,
    )


async def _executor(schedules: list[ScheduleSpec], policy: object | None = None
                    ) -> tuple[ScheduleExecutor, WorkspaceAgentSpec]:
    runtime = AgentRuntime()
    runtime.registry.register_model(EchoModelProvider())
    registry = InMemoryWorkspaceAgentRegistry()
    spec = _agent(schedules)
    await registry.save(spec)
    return ScheduleExecutor(runtime=runtime, registry=registry, policy=policy), spec


async def test_interval_schedule_runs_once_per_interval() -> None:
    executor, spec = await _executor([
        ScheduleSpec(name="hourly", trigger=ScheduleTrigger(kind="interval", expression="1h"),
                     input="Summarize the last hour."),
    ])
    t0 = FRIDAY_1500
    assert await executor.run_due(now=t0) == []  # anchor: first due one interval later
    assert await executor.run_due(now=t0 + timedelta(minutes=59)) == []
    refs = await executor.run_due(now=t0 + timedelta(hours=1))
    assert len(refs) == 1
    ref = refs[0]
    assert ref.status == "completed"
    assert ref.agent_id == spec.id
    assert ref.run_id is not None
    assert ref.metadata["trigger_kind"] == "interval"
    # Not due again until another interval passes.
    assert await executor.run_due(now=t0 + timedelta(hours=1, minutes=30)) == []
    assert len(await executor.run_due(now=t0 + timedelta(hours=2))) == 1


async def test_cron_schedule_runs_at_window_and_collapses_missed_runs() -> None:
    executor, _ = await _executor([
        ScheduleSpec(name="weekday", trigger=ScheduleTrigger(kind="cron", expression="0 16 * * 1-5")),
    ])
    assert await executor.run_due(now=FRIDAY_1500) == []
    # Poll long after several windows passed: exactly one run, stamped with
    # the originally scheduled time.
    refs = await executor.run_due(now=FRIDAY_1500 + timedelta(days=4))
    assert len(refs) == 1
    assert refs[0].metadata["scheduled_for"] == "2026-06-12T16:00:00+00:00"


async def test_disabled_and_manual_schedules_never_run_automatically() -> None:
    executor, spec = await _executor([
        ScheduleSpec(name="off", enabled=False,
                     trigger=ScheduleTrigger(kind="interval", expression="1s")),
        ScheduleSpec(name="manual", trigger=ScheduleTrigger(kind="manual")),
    ])
    assert await executor.run_due(now=FRIDAY_1500) == []
    assert await executor.run_due(now=FRIDAY_1500 + timedelta(days=1)) == []
    ref = await executor.run_now(spec.id, "manual")
    assert ref.status == "completed"
    assert ref.schedule_name == "manual"


class _DenyPolicy:
    async def check(self, request: PolicyRequest) -> PolicyDecision:
        assert request.checkpoint == "before_scheduled_run"
        return PolicyDecision.deny("maintenance window")


async def test_policy_denial_marks_run_skipped() -> None:
    executor, _ = await _executor(
        [ScheduleSpec(name="hourly", trigger=ScheduleTrigger(kind="interval", expression="1h"))],
        policy=_DenyPolicy(),
    )
    await executor.run_due(now=FRIDAY_1500)
    refs = await executor.run_due(now=FRIDAY_1500 + timedelta(hours=1))
    assert [ref.status for ref in refs] == ["skipped"]
    assert refs[0].metadata["reason"] == "maintenance window"


async def test_failed_run_is_reported_not_raised() -> None:
    runtime = AgentRuntime()  # no provider registered -> run fails
    registry = InMemoryWorkspaceAgentRegistry()
    spec = _agent(
        [ScheduleSpec(name="hourly", trigger=ScheduleTrigger(kind="interval", expression="1h"))]
    )
    await registry.save(spec)
    executor = ScheduleExecutor(runtime=runtime, registry=registry)
    await executor.run_due(now=FRIDAY_1500)
    refs = await executor.run_due(now=FRIDAY_1500 + timedelta(hours=1))
    assert [ref.status for ref in refs] == ["failed"]
    assert refs[0].metadata["error_type"]


async def test_calendar_schedules_are_reported_unsupported() -> None:
    executor, spec = await _executor([
        ScheduleSpec(name="holidays", trigger=ScheduleTrigger(kind="calendar", expression="ics")),
    ])
    assert await executor.run_due(now=FRIDAY_1500) == []
    assert executor.unsupported_schedules() == [(spec.id, "holidays", "calendar")]
