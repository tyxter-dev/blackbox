"""Dynamic tool loading over a CRM-sized catalog with a tool budget.

This mirrors the most common production agent shape observed in
``docs/USE_CASE_VALIDATION.md``: an assistant carrying a 30+ tool CRM catalog
that the model browses at run time instead of receiving every schema up
front. The runtime exposes only ``search_tools``/``load_tools`` plus loaded
tools, enforces a ``ToolBudget``, and emits tool-choice telemetry.

The model is scripted, so the example is fully offline and deterministic:

1. The model sees only the two meta-tools and searches the catalog.
2. It loads five tools; the budget admits four and rejects the fifth.
3. It calls two loaded tools in one turn (deal + follow-up task).
4. It produces the final answer.

Run::

    python examples/dynamic_toolset_crm.py
"""
from __future__ import annotations

# ruff: noqa: E402
import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

from _bootstrap import bootstrap

bootstrap()

from blackbox import AgentRuntime, EventTypes, ToolBudget, Toolset
from blackbox.core.capabilities import ModelCapabilities
from blackbox.core.events import AgentEvent
from blackbox.core.state import ProviderState
from blackbox.providers.base import TurnRequest
from blackbox.tools import ToolResult

# (name, description, category, tags, risk, params) — one row per catalog tool,
# mirroring the tool names seen across real CRM-integrated deployments.
CATALOG: list[tuple[str, str, str, list[str], str, dict[str, str]]] = [
    ("get_customer_context", "Fetch CRM context for the active customer.", "customers", ["customer", "context"], "low", {"customer_id": "string"}),
    ("create_customer", "Create a new customer record.", "customers", ["customer", "create"], "medium", {"name": "string", "phone": "string"}),
    ("update_customer_profile", "Update fields on a customer profile.", "customers", ["customer", "update"], "medium", {"customer_id": "string", "fields": "object"}),
    ("query_customers", "Search customers by name, phone, or tag.", "customers", ["customer", "search"], "low", {"query": "string"}),
    ("get_crm_summary", "Summarize pipeline, tasks, and recent activity.", "customers", ["summary", "report"], "low", {}),
    ("create_deal", "Create a sales deal in the pipeline.", "deals", ["deal", "pipeline", "create"], "medium", {"customer_id": "string", "title": "string", "value": "number"}),
    ("update_deal", "Update stage or value of an existing deal.", "deals", ["deal", "pipeline", "update"], "medium", {"deal_id": "string", "fields": "object"}),
    ("query_deals", "List deals filtered by stage or customer.", "deals", ["deal", "pipeline", "search"], "low", {"stage": "string"}),
    ("create_task", "Create a follow-up task.", "tasks", ["task", "follow-up", "create"], "medium", {"title": "string", "due_date": "string"}),
    ("update_task", "Update or complete an existing task.", "tasks", ["task", "update"], "medium", {"task_id": "string", "fields": "object"}),
    ("query_tasks", "List open tasks, optionally by assignee.", "tasks", ["task", "search"], "low", {"assignee": "string"}),
    ("create_calendar_event", "Schedule a calendar event.", "calendar", ["calendar", "schedule", "create"], "medium", {"title": "string", "start": "string", "end": "string"}),
    ("update_calendar_event", "Move or edit a calendar event.", "calendar", ["calendar", "update"], "medium", {"event_id": "string", "fields": "object"}),
    ("delete_calendar_event", "Cancel a calendar event.", "calendar", ["calendar", "delete"], "high", {"event_id": "string"}),
    ("query_calendar", "List events in a date range.", "calendar", ["calendar", "search"], "low", {"start": "string", "end": "string"}),
    ("schedule_reminder", "Schedule a reminder message to a customer.", "reminders", ["reminder", "schedule"], "medium", {"customer_id": "string", "message": "string", "send_at": "string"}),
    ("cancel_reminder", "Cancel a scheduled reminder.", "reminders", ["reminder", "cancel"], "medium", {"reminder_id": "string"}),
    ("reactivate_reminder", "Reactivate a cancelled reminder.", "reminders", ["reminder", "update"], "medium", {"reminder_id": "string"}),
    ("query_reminders", "List scheduled reminders for a customer.", "reminders", ["reminder", "search"], "low", {"customer_id": "string"}),
    ("create_automation", "Create a triggered automation flow.", "automations", ["automation", "create"], "high", {"name": "string", "trigger": "string"}),
    ("manage_automation", "Enable, disable, or edit an automation.", "automations", ["automation", "update"], "high", {"automation_id": "string", "action": "string"}),
    ("query_automations", "List configured automations.", "automations", ["automation", "search"], "low", {}),
    ("create_email_template", "Create a reusable email template.", "templates", ["template", "email", "create"], "medium", {"name": "string", "body": "string"}),
    ("manage_email_template", "Edit or archive an email template.", "templates", ["template", "email", "update"], "medium", {"template_id": "string", "action": "string"}),
    ("query_email_templates", "List email templates.", "templates", ["template", "email", "search"], "low", {}),
    ("create_message_template", "Create a chat message template.", "templates", ["template", "message", "create"], "medium", {"name": "string", "body": "string"}),
    ("manage_message_template", "Edit or archive a message template.", "templates", ["template", "message", "update"], "medium", {"template_id": "string", "action": "string"}),
    ("query_message_templates", "List chat message templates.", "templates", ["template", "message", "search"], "low", {}),
    ("generate_report", "Generate an activity or pipeline report.", "reporting", ["report", "analytics"], "low", {"kind": "string", "period": "string"}),
    ("query_csv_data", "Query an uploaded CSV dataset.", "reporting", ["csv", "data", "search"], "low", {"query": "string"}),
    ("send_media", "Send an image or document to the customer.", "messaging", ["media", "send"], "medium", {"media_id": "string", "caption": "string"}),
]


def _schema(params: dict[str, str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {name: {"type": kind} for name, kind in params.items()},
        "required": list(params),
    }


_RECORD_COUNTER = iter(range(100, 10_000))


def _make_handler(name: str):
    def handler(**arguments: Any) -> ToolResult:
        record = {"id": f"{name.split('_')[0]}_{next(_RECORD_COUNTER)}", **arguments}
        return ToolResult(
            content=json.dumps({"ok": True, "tool": name, "record": record}),
            payload={"tool": name, "record": record},
        )

    return handler


class CRMToolset(Toolset):
    """Full CRM catalog: customers, deals, tasks, calendar, reminders, more."""

    def __init__(self) -> None:
        super().__init__(name="crm", description="CRM tools for a customer-facing assistant.")
        for name, description, category, tags, risk, params in CATALOG:
            self.register(
                _make_handler(name),
                name=name,
                description=description,
                parameters=_schema(params),
                category=category,
                tags=tags,
                risk=risk,
                scopes=[f"crm:{'write' if risk != 'low' else 'read'}"],
            )


class ScriptedModel:
    """Deterministic model: replays queued turn generators."""

    provider_id = "scripted"

    def __init__(self) -> None:
        self._turns: list[Any] = []

    def queue(self, turn: Any) -> None:
        self._turns.append(turn)

    def capabilities(self, model: str | None = None) -> ModelCapabilities:
        return ModelCapabilities(supports_streaming_events=True, supports_function_tools=True)

    async def stream_turn(self, request: TurnRequest) -> AsyncIterator[AgentEvent]:
        turn = self._turns.pop(0)
        for event in turn(request):
            yield event


def _tool_calls(turn_label: str, calls: list[tuple[str, str, dict[str, Any]]]):
    def turn(request: TurnRequest):
        visible = [tool["name"] for tool in request.tools]
        print(f"[model] {turn_label}: sees {len(visible)} tools -> {', '.join(visible)}")
        yield AgentEvent(type=EventTypes.MODEL_REQUEST_STARTED, provider="scripted")
        for call_id, name, arguments in calls:
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


def _final_text(turn_label: str, text: str):
    def turn(request: TurnRequest):
        visible = [tool["name"] for tool in request.tools]
        print(f"[model] {turn_label}: sees {len(visible)} tools -> {', '.join(visible)}")
        yield AgentEvent(type=EventTypes.MODEL_REQUEST_STARTED, provider="scripted")
        yield AgentEvent(
            type=EventTypes.MODEL_TEXT_DELTA, provider="scripted", data={"delta": text}
        )
        yield AgentEvent(
            type=EventTypes.MODEL_COMPLETED,
            provider="scripted",
            data={"provider_state": ProviderState(provider="scripted")},
        )

    return turn


async def main() -> None:
    toolset = CRMToolset()
    budget = ToolBudget(max_tools_visible=6, search_result_limit=5)
    print(f"catalog: {len(toolset.all_tools())} tools in toolset {toolset.name!r}")
    print(f"budget:  max_tools_visible={budget.max_tools_visible}, "
          f"search_result_limit={budget.search_result_limit}\n")

    scripted = ScriptedModel()
    scripted.queue(_tool_calls("turn 1 (search)", [
        ("c1", "search_tools", {"query": "deal pipeline follow-up"}),
    ]))
    scripted.queue(_tool_calls("turn 2 (load)", [
        ("c2", "load_tools", {"tool_names": [
            "query_deals", "create_deal", "create_task",
            "query_customers", "update_customer_profile",
        ]}),
    ]))
    scripted.queue(_tool_calls("turn 3 (work)", [
        ("c3", "create_deal", {"customer_id": "cus_042", "title": "Renovation quote", "value": 18500}),
        ("c4", "create_task", {"title": "Send proposal to cus_042", "due_date": "2026-06-15"}),
    ]))
    scripted.queue(_final_text(
        "turn 4 (final)",
        "Created the Renovation quote deal (R$18,500) and a follow-up task for June 15.",
    ))

    runtime = AgentRuntime()
    runtime.registry.register_model(scripted)

    result = await runtime.run(
        provider="scripted:crm-demo",
        input="Open a deal for customer cus_042 and schedule the follow-up.",
        toolsets=[toolset],
        tool_selection="dynamic",
        tool_budget=budget,
    )

    print("\ntool-choice telemetry:")
    for event in result.events:
        if event.type == EventTypes.TOOL_SEARCH_COMPLETED:
            print(f"  search: query={event.data.get('query')!r} "
                  f"returned={event.data.get('result_count')} "
                  f"matched={event.data.get('total_matched')}")
        elif event.type == EventTypes.TOOL_CHOICE_LOADED:
            print(f"  loaded: {event.data.get('name')}")
        elif event.type == EventTypes.TOOL_CHOICE_REJECTED:
            print(f"  rejected: {event.data.get('name')} "
                  f"({event.data.get('reason')}, limit={event.data.get('limit')})")

    print("\napplication payloads (deferred payload pattern):")
    for payload in result.payloads:
        if payload.payload and payload.payload.get("tool") in {"create_deal", "create_task"}:
            print(f"  {payload.tool_name}: {payload.payload['record']}")

    print(f"\nfinal output: {result.output}")


if __name__ == "__main__":
    asyncio.run(main())
