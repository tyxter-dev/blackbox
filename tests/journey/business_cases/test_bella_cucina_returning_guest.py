from __future__ import annotations

from examples.maia_bella_cucina_business_case import (
    CASE_TOOLS,
    build_returning_guest_plan,
    create_bella_cucina_runtime,
)


async def test_bella_cucina_returning_guest_prompt_plan_journey() -> None:
    runtime, _ = create_bella_cucina_runtime()

    plan = await build_returning_guest_plan(runtime, provider="echo:echo-mini")

    assert plan.effective_tool_ids == CASE_TOOLS
    assert plan.prompt is not None
    assert plan.prompt.metadata["parity"] == "pass"

    fragment_ids = set(plan.prompt.metadata["fragment_ids"])
    assert "bella.private_room.check_availability" in fragment_ids
    assert "bella.private_room.follow_up_task" in fragment_ids
    assert "bella.private_room.minimum" in fragment_ids
    assert "channel.whatsapp.no_phone_recollection" in fragment_ids
    assert "bella.returning_guest.no_duplicate_customer" in fragment_ids

    assert "crm.customer.lookup_before_create" not in fragment_ids
    assert "crm.customer.lookup_before_create" in set(
        plan.prompt.metadata["skipped_fragment_ids"]
    )
    assert "create_customer" not in plan.prompt.instructions
