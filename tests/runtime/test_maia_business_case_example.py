from __future__ import annotations

from typing import Any

from agent_runtime import AgentEvent, EventTypes, PromptSpec, ProviderState
from examples.maia_bella_cucina_business_case import (
    CASE_CONTEXT_FLAGS,
    CASE_INPUT,
    CASE_TOOLS,
    bella_cucina_instructions,
    build_returning_guest_plan,
    create_bella_cucina_runtime,
)
from tests.fixtures.scripted_model import ScriptedModelProvider, text_only_turn


async def test_maia_bella_cucina_example_keeps_prompt_and_tools_in_parity() -> None:
    runtime, _ = create_bella_cucina_runtime()

    plan = await build_returning_guest_plan(runtime, provider="echo:echo-mini")

    assert plan.effective_tool_ids == CASE_TOOLS
    assert "create_customer" in plan.available_tool_ids
    assert "create_customer" not in plan.effective_tool_ids
    assert plan.prompt is not None
    assert plan.prompt.metadata["parity"] == "pass"
    assert plan.prompt.metadata["disabled_tool_mentions"] == []

    fragment_ids = set(plan.prompt.metadata["fragment_ids"])
    assert {
        "bella.private_room.check_availability",
        "bella.private_room.follow_up_task",
        "bella.private_room.minimum",
        "bella.returning_guest.no_duplicate_customer",
        "channel.whatsapp.no_phone_recollection",
    } <= fragment_ids

    skipped_fragment_ids = set(plan.prompt.metadata["skipped_fragment_ids"])
    assert "crm.customer.lookup_before_create" in skipped_fragment_ids
    assert "crm.customer.lookup_before_create" not in fragment_ids
    assert "Before creating a new guest" not in plan.prompt.instructions
    assert "create_customer" not in plan.prompt.instructions
    assert "query_calendar" in plan.prompt.instructions
    assert "create_task" in plan.prompt.instructions
    assert any(section.id == "base.instructions" for section in plan.prompt.cache_sections)


async def test_maia_bella_cucina_example_executes_scripted_tool_trace() -> None:
    runtime, crm = create_bella_cucina_runtime()
    scripted = ScriptedModelProvider()
    runtime.registry.register_model(scripted)
    customer = next(iter(crm.customers.values()))

    def private_room_turn(request: Any) -> Any:
        """Emit the scripted calendar and task tool calls for the private room scenario."""
        assert [tool["name"] for tool in request.tools] == CASE_TOOLS
        assert request.controls.instructions is not None
        assert "create_customer" not in request.controls.instructions
        yield AgentEvent(
            type=EventTypes.MODEL_REQUEST_STARTED,
            provider="scripted",
            data={"model": request.model},
        )
        yield AgentEvent(
            type=EventTypes.TOOL_CALL_REQUESTED,
            provider="scripted",
            item_id="call_calendar",
            data={
                "call_id": "call_calendar",
                "name": "query_calendar",
                "arguments": {
                    "date": "2026-05-01 evening",
                    "party_size": 12,
                    "room_type": "private",
                },
            },
        )
        yield AgentEvent(
            type=EventTypes.TOOL_CALL_REQUESTED,
            provider="scripted",
            item_id="call_task",
            data={
                "call_id": "call_task",
                "name": "create_task",
                "arguments": {
                    "title": "Follow up with David Park about private dining room",
                    "customer_id": customer.id,
                    "due_date": "2026-04-29",
                    "notes": (
                        "Team dinner for 12 next Friday evening. Confirm private "
                        "room setup, gluten-free accommodation, and red wine preference."
                    ),
                },
            },
        )
        yield AgentEvent(
            type=EventTypes.MODEL_COMPLETED,
            provider="scripted",
            data={"provider_state": ProviderState(provider="scripted")},
        )

    scripted.queue(private_room_turn)
    scripted.queue(
        text_only_turn(
            "David, I found private room options next Friday evening. The room seats "
            "up to 20 and has a $500 minimum; our host team will follow up."
        )
    )

    result = await runtime.run(
        provider="scripted:test",
        input=CASE_INPUT,
        instructions=bella_cucina_instructions(customer),
        tools=CASE_TOOLS,
        prompt=PromptSpec(mode="tool_aware", channel="whatsapp", parity="error"),
        context_flags=CASE_CONTEXT_FLAGS,
    )

    completed_tool_names = [
        event.data["name"]
        for event in result.events
        if event.type == EventTypes.TOOL_CALL_COMPLETED
    ]
    assert completed_tool_names == ["query_calendar", "create_task"]
    assert len(crm.customers) == 1
    assert len(crm.tasks) == 1
    assert crm.tasks[0].customer_id == customer.id
    assert "private room" in result.text
