"""Scheduled workspace agent runs through the reference ScheduleExecutor.

Closes the "scheduled & proactive behavior" row from
``docs/USE_CASE_VALIDATION.md`` (~570/610 production agents use reminders or
scheduled initiation): a packaged agent declares cron and interval
schedules, and ``ScheduleExecutor`` turns due schedules into runs producing
``ScheduledRunRef``s.

Time is passed explicitly to ``run_due(now=...)``, so this example replays a
simulated weekend deterministically and offline. Real deployments either
call ``run_due()`` from an external cron or run ``executor.serve()``.

Run::

    python examples/scheduled_digest.py
"""
from __future__ import annotations

# ruff: noqa: E402
import asyncio
from datetime import UTC, datetime, timedelta

from _bootstrap import bootstrap

bootstrap()

from blackbox import (
    AgentRuntime,
    InMemoryWorkspaceAgentRegistry,
    ScheduleExecutor,
    ScheduleSpec,
    ScheduleTrigger,
    WorkspaceAgentSpec,
)
from blackbox.providers.model_adapters.echo import EchoModelProvider

FRIDAY_1500 = datetime(2026, 6, 12, 15, 0, tzinfo=UTC)


async def main() -> None:
    runtime = AgentRuntime()
    runtime.registry.register_model(EchoModelProvider())

    agent = WorkspaceAgentSpec(
        name="ops-digest",
        instructions="Prepare a concise operations digest.",
        model_provider="echo",
        model="echo-mini",
        schedules=[
            ScheduleSpec(
                name="weekday-brief",
                trigger=ScheduleTrigger(kind="cron", expression="0 16 * * 1-5"),
                input="Prepare the 16:00 weekday operations brief.",
            ),
            ScheduleSpec(
                name="heartbeat",
                trigger=ScheduleTrigger(kind="interval", expression="12h"),
                input="Post the half-day status heartbeat.",
            ),
            ScheduleSpec(name="on-demand", trigger=ScheduleTrigger(kind="manual")),
        ],
    )
    registry = InMemoryWorkspaceAgentRegistry()
    await registry.save(agent)
    executor = ScheduleExecutor(runtime=runtime, registry=registry)

    # Replay a simulated weekend, polling every ~6 hours like an external cron.
    print("polling a simulated weekend (executor tracked from Friday 15:00 UTC):")
    for hours in (0, 1, 13, 27, 49, 73, 76):
        now = FRIDAY_1500 + timedelta(hours=hours)
        refs = await executor.run_due(now=now)
        label = now.strftime("%a %H:%M")
        if not refs:
            print(f"  {label}  nothing due")
        for ref in refs:
            print(f"  {label}  {ref.schedule_name:14} {ref.status:9} "
                  f"(scheduled for {ref.metadata['scheduled_for'][:16]})")

    ref = await executor.run_now(agent.id, "on-demand")
    print(f"\nmanual trigger: {ref.schedule_name} -> {ref.status} (run {ref.run_id})")


if __name__ == "__main__":
    asyncio.run(main())
