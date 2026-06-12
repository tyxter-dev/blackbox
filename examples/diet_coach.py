"""Diet Coach: a packaged personal agent running in the background.

The flagship workspace-agent example: a nutrition assistant for an athlete
who trains 5 days a week. It exercises the whole Horizon 2 surface in one
runnable story:

1. **Package contract** — a `WorkspaceAgentSpec` declaring tools, Cal.com and
   Slack connectors, permissions, a 9 AM cron schedule (timezone-aware), and
   an embedded nutrition skill bundle.
2. **Validation** — `validate_workspace_agent` lints the spec before it
   ships (the scripted model id produces the expected catalog warning).
3. **Distribution** — the spec is saved as an on-disk package, zipped, and
   installed from the archive into a `SQLiteWorkspaceAgentRegistry`.
4. **Background behavior** — `ScheduleExecutor` fires the 9 AM update; the
   agent reads the training schedule and preferences, then posts to Slack.
5. **Conversation** — "Not in the mood for chicken, I want beef today"
   updates the preference store through a tool call, and the next morning's
   Slack update reflects it.

The app layer (Cal.com, Slack, the preference store) is faked in-process and
the model is scripted, so the example runs fully offline and deterministic —
swap `ScriptedModel` for a real provider and the fakes for thin HTTP
wrappers (or MCP servers) to make it production-shaped. Credential handling
stays downstream by design: `ConnectorSpec.auth_mode="end_user"` declares
that each user brings their own Cal.com/Slack auth.

Run::

    python examples/diet_coach.py
"""
from __future__ import annotations

# ruff: noqa: E402
import asyncio
import json
import tempfile
from collections.abc import AsyncIterator, Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from _bootstrap import bootstrap

bootstrap()

from blackbox import (
    AgentRuntime,
    ConnectorSpec,
    ScheduleExecutor,
    ScheduleSpec,
    ScheduleTrigger,
    SkillBundleRef,
    SQLiteWorkspaceAgentRegistry,
    ToolPermission,
    WorkspaceAgentSpec,
    ensure_valid_workspace_agent,
    install_workspace_agent_package,
    pack_workspace_agent_package,
    run_workspace_agent,
    save_workspace_agent_package,
    validate_workspace_agent,
)
from blackbox.core.capabilities import ModelCapabilities
from blackbox.core.events import AgentEvent, EventTypes
from blackbox.core.state import ProviderState
from blackbox.providers.base import TurnRequest
from blackbox.tools import ToolResult

# 09:00 in Sao Paulo (UTC-3) is 12:00 UTC.
MONDAY_0800_LOCAL = datetime(2026, 6, 15, 11, 0, tzinfo=UTC)
MONDAY_0900_LOCAL = datetime(2026, 6, 15, 12, 0, tzinfo=UTC)
TUESDAY_0900_LOCAL = datetime(2026, 6, 16, 12, 0, tzinfo=UTC)


# --- the app layer: faked Cal.com, Slack, and the preference store -------------

TRAINING_WEEK = {
    "Monday": "Strength: lower body (90 min)",
    "Tuesday": "Intervals: track session (60 min)",
    "Wednesday": None,  # rest
    "Thursday": "Strength: upper body (75 min)",
    "Friday": "Tempo run (50 min)",
    "Saturday": "Long endurance ride (3 h)",
    "Sunday": None,  # rest
}

PREFERENCES: dict[str, Any] = {
    "default_protein": "chicken",
    "protein_today": None,
    "dislikes": ["cilantro"],
}

SLACK_OUTBOX: list[dict[str, str]] = []


def get_training_schedule(date: str) -> ToolResult:
    day = datetime.fromisoformat(date).strftime("%A")
    session = TRAINING_WEEK.get(day)
    record = {"date": date, "day": day, "session": session, "training_day": session is not None}
    return ToolResult(content=json.dumps(record), payload=record)


def get_preferences() -> ToolResult:
    return ToolResult(content=json.dumps(PREFERENCES), payload=dict(PREFERENCES))


def set_preference(key: str, value: Any) -> ToolResult:
    PREFERENCES[key] = value
    record = {"updated": key, "value": value}
    return ToolResult(content=json.dumps(record), payload=record)


def send_slack_message(channel: str, text: str) -> ToolResult:
    SLACK_OUTBOX.append({"channel": channel, "text": text})
    return ToolResult(content=json.dumps({"ok": True, "channel": channel}))


def todays_protein() -> str:
    return PREFERENCES["protein_today"] or PREFERENCES["default_protein"]


# --- the scripted model (swap for a real provider in production) ----------------

Turn = Callable[[TurnRequest], Iterator[AgentEvent]]


class ScriptedModel:
    """Deterministic model: replays queued turn generators."""

    provider_id = "scripted"

    def __init__(self) -> None:
        self._turns: list[Turn] = []

    def queue(self, turn: Turn) -> None:
        self._turns.append(turn)

    def capabilities(self, model: str | None = None) -> ModelCapabilities:
        return ModelCapabilities(supports_streaming_events=True, supports_function_tools=True)

    async def stream_turn(self, request: TurnRequest) -> AsyncIterator[AgentEvent]:
        turn = self._turns.pop(0)
        for event in turn(request):
            yield event


def _tool_calls(calls: Callable[[], list[tuple[str, str, dict[str, Any]]]]) -> Turn:
    """A model turn issuing tool calls; arguments are computed at stream time,
    so later turns can react to state earlier turns changed."""

    def turn(request: TurnRequest) -> Iterator[AgentEvent]:
        yield AgentEvent(type=EventTypes.MODEL_REQUEST_STARTED, provider="scripted")
        for call_id, name, arguments in calls():
            yield AgentEvent(
                type=EventTypes.TOOL_CALL_REQUESTED,
                provider="scripted",
                item_id=call_id,
                data={"call_id": call_id, "name": name, "arguments": arguments},
            )
        yield AgentEvent(
            type=EventTypes.MODEL_COMPLETED,
            provider="scripted",
            data={"provider_state": ProviderState(provider="scripted")},
        )

    return turn


def _final_text(text: Callable[[], str]) -> Turn:
    def turn(request: TurnRequest) -> Iterator[AgentEvent]:
        yield AgentEvent(type=EventTypes.MODEL_REQUEST_STARTED, provider="scripted")
        yield AgentEvent(
            type=EventTypes.MODEL_TEXT_DELTA, provider="scripted", data={"delta": text()}
        )
        yield AgentEvent(
            type=EventTypes.MODEL_COMPLETED,
            provider="scripted",
            data={"provider_state": ProviderState(provider="scripted")},
        )

    return turn


def _morning_plan(date: str) -> str:
    day = datetime.fromisoformat(date).strftime("%A")
    session = TRAINING_WEEK.get(day)
    protein = todays_protein()
    if session:
        return (
            f"Good morning! {day}'s session: {session}. Training-day plan — "
            f"breakfast: oats + eggs; lunch: {protein} with rice and greens "
            f"(extra carbs pre-session); dinner: {protein}, sweet potato, salad. "
            f"Hydrate well before the session."
        )
    return (
        f"Good morning! {day} is a rest day. Lighter plan — "
        f"breakfast: yogurt + fruit; lunch: {protein} salad bowl; "
        f"dinner: grilled fish and vegetables."
    )


def queue_scripted_day(scripted: ScriptedModel, date: str) -> None:
    """One 9 AM run = read schedule + prefs, post to Slack, summarize."""

    scripted.queue(_tool_calls(lambda: [
        ("c1", "get_training_schedule", {"date": date}),
        ("c2", "get_preferences", {}),
    ]))
    scripted.queue(_tool_calls(lambda: [
        ("c3", "send_slack_message", {"channel": "#diet", "text": _morning_plan(date)}),
    ]))
    scripted.queue(_final_text(lambda: f"Posted the {date} plan to Slack."))


# --- the packaged agent ----------------------------------------------------------


def build_diet_coach(skill_source: str) -> WorkspaceAgentSpec:
    return WorkspaceAgentSpec(
        name="diet-coach",
        instructions=(
            "You are a personal nutrition assistant for an athlete training 5 days/week. "
            "Always read the training schedule before planning meals: heavier carbs on "
            "training days, lighter on rest days. Respect stored food preferences and "
            "update them when the user states a new one. Post the daily plan to Slack."
        ),
        model_provider="scripted",
        model="coach-v1",
        tools=[
            "get_training_schedule",
            "get_preferences",
            "set_preference",
            "send_slack_message",
        ],
        connectors=[
            ConnectorSpec(name="calcom", kind="http_api", auth_mode="end_user",
                          tool_refs=["get_training_schedule"]),
            ConnectorSpec(name="slack", kind="http_api", auth_mode="end_user",
                          tool_refs=["send_slack_message"]),
        ],
        permissions=[
            ToolPermission(ref="get_training_schedule", scopes=["read"], connector="calcom"),
            ToolPermission(ref="get_preferences", scopes=["read"]),
            ToolPermission(ref="set_preference", scopes=["write"]),
            ToolPermission(ref="send_slack_message", scopes=["write"], connector="slack"),
        ],
        schedules=[
            ScheduleSpec(
                name="morning-update",
                trigger=ScheduleTrigger(
                    kind="cron", expression="0 9 * * *", timezone="America/Sao_Paulo"
                ),
                input=(
                    "Plan today's meals from the training schedule and preferences, "
                    "then send the summary to Slack."
                ),
            ),
        ],
        skills=[SkillBundleRef(name="nutrition", source=skill_source, version="1.0")],
    )


def write_nutrition_skill(base: Path) -> Path:
    skill_dir = base / "nutrition_skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "# Nutrition guidelines\n\n"
        "- Training days: +30% carbohydrates, protein at every meal.\n"
        "- Rest days: reduce carbs, keep protein constant.\n"
        "- Respect the dislikes list without being asked twice.\n",
        encoding="utf-8",
    )
    return skill_dir


async def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)

        # 1. Build the spec, pointing it at the local nutrition skill bundle.
        skill_dir = write_nutrition_skill(base)
        spec = build_diet_coach(str(skill_dir))

        # 2. Validate before shipping. The scripted model id is (correctly)
        #    not in the bundled catalog, which surfaces as a warning.
        print("validation:")
        for issue in validate_workspace_agent(spec):
            print(f"  [{issue.severity}] {issue.code}: {issue.message}")
        ensure_valid_workspace_agent(spec)  # raises only on errors

        # 3. Distribute: save -> zip -> install from the archive.
        package_dir = save_workspace_agent_package(spec, base / "diet-coach-pkg")
        archive = pack_workspace_agent_package(package_dir, base / "diet-coach.zip")
        registry = SQLiteWorkspaceAgentRegistry(base / "agents.db")
        installed = await install_workspace_agent_package(
            archive, registry, unpack_dir=base / "installed" / "diet-coach"
        )
        print("\npackage:")
        for path in sorted(package_dir.rglob("*")):
            if path.is_file():
                print(f"  {path.relative_to(package_dir).as_posix()}")
        print(f"installed into SQLite registry: {installed.name} "
              f"(skill at .../{Path(installed.skills[0].source or '').name})")

        # 4. Wire the runtime: scripted model + the app-layer tools.
        scripted = ScriptedModel()
        runtime = AgentRuntime()
        runtime.registry.register_model(scripted)
        runtime.tools.register(get_training_schedule, name="get_training_schedule",
                               description="Read the training session for a date (Cal.com).")
        runtime.tools.register(get_preferences, name="get_preferences",
                               description="Read stored food preferences.")
        runtime.tools.register(set_preference, name="set_preference",
                               description="Store a food preference.")
        runtime.tools.register(send_slack_message, name="send_slack_message",
                               description="Post a message to a Slack channel.")

        executor = ScheduleExecutor(runtime=runtime, registry=registry)

        # 5. Monday: the 9 AM background run fires from the cron schedule.
        queue_scripted_day(scripted, "2026-06-15")
        await executor.run_due(now=MONDAY_0800_LOCAL)  # starts tracking; nothing due
        refs = await executor.run_due(now=MONDAY_0900_LOCAL)
        print("\nMonday 09:00 (America/Sao_Paulo) scheduled run:")
        for ref in refs:
            print(f"  {ref.schedule_name}: {ref.status} (run {ref.run_id})")
        print(f"  slack -> {SLACK_OUTBOX[-1]['text']}")

        # 6. Later that day, a conversational change request through the
        #    same packaged agent.
        scripted.queue(_tool_calls(lambda: [
            ("c10", "set_preference", {"key": "protein_today", "value": "beef"}),
        ]))
        scripted.queue(_final_text(
            lambda: "Done — swapped chicken for beef. Say the word to switch back."
        ))
        result = await run_workspace_agent(
            runtime,
            installed,
            input="Not in the mood for chicken, I want beef today.",
        )
        print("\nuser: Not in the mood for chicken, I want beef today.")
        print(f"agent: {result.text}")

        # 7. Tuesday: the next 9 AM run reflects the stored preference.
        queue_scripted_day(scripted, "2026-06-16")
        refs = await executor.run_due(now=TUESDAY_0900_LOCAL)
        print("\nTuesday 09:00 scheduled run:")
        for ref in refs:
            print(f"  {ref.schedule_name}: {ref.status}")
        print(f"  slack -> {SLACK_OUTBOX[-1]['text']}")

        registry.close()


if __name__ == "__main__":
    asyncio.run(main())
