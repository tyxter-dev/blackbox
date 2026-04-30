from __future__ import annotations

from blackbox import EventTypes, ToolRoutingSpec
from blackbox.core.events import AgentEvent
from blackbox.core.serialization import event_from_dict, event_to_dict
from blackbox.runtime.tool_routing import RoutingContext, resolve_tool_routing
from blackbox.tools.registry import ToolRegistry
from blackbox.tools.routing import ResolvedToolPlan, ToolBudget, ToolSelectionResult


async def test_coding_defaults_build_expected_workspace_refs() -> None:
    spec = ToolRoutingSpec.coding_defaults(max_visible_tools=6, max_schema_tokens=4000)

    assert spec.mode == "hybrid"
    assert spec.budget.max_visible_tools == 6
    assert spec.budget.max_schema_tokens == 4000
    assert "workspace:list_files" in spec.always_include
    assert "workspace:read_file" in spec.always_include
    assert spec.allow_model_discovery_tools is True


async def test_local_registry_candidates_include_metadata_and_score() -> None:
    registry = ToolRegistry()
    registry.register(
        lambda ticket_id: f"ticket:{ticket_id}",
        name="lookup_ticket",
        description="Look up support ticket details.",
        parameters={
            "type": "object",
            "properties": {"ticket_id": {"type": "string"}},
            "required": ["ticket_id"],
        },
        category="support",
        tags=["ticket", "lookup"],
        metadata={"owner": "support"},
    )

    resolution = await resolve_tool_routing(
        RoutingContext(
            task="look up the ticket",
            current_input="look up the ticket",
            iteration=0,
            provider="scripted",
            model="test",
            registry=registry,
            routing=ToolRoutingSpec(mode="auto", include_categories=("support",)),
        )
    )

    assert resolution.plan.local_tools == ("lookup_ticket",)
    completed = next(
        event
        for event in resolution.events
        if event.type == EventTypes.TOOL_ROUTING_COMPLETED
    )
    assert completed.data["selected_refs"] == ["local:lookup_ticket"]


async def test_never_include_blocks_even_matching_tool() -> None:
    registry = ToolRegistry()
    registry.register(
        lambda: "danger",
        name="delete_ticket",
        description="Delete a support ticket.",
        category="support",
    )

    resolution = await resolve_tool_routing(
        RoutingContext(
            task="delete ticket",
            current_input="delete ticket",
            iteration=0,
            provider="scripted",
            model="test",
            registry=registry,
            routing=ToolRoutingSpec(mode="auto", never_include=("local:delete_ticket",)),
        )
    )

    assert resolution.plan.local_tools == ()
    assert resolution.plan.metadata["blocked_refs"] == ["local:delete_ticket"]


async def test_always_include_survives_low_score_and_budget() -> None:
    registry = ToolRegistry()
    registry.register(lambda: "a", name="alpha", description="Alpha")
    registry.register(lambda: "b", name="beta", description="Beta")

    resolution = await resolve_tool_routing(
        RoutingContext(
            task="nothing related",
            current_input="nothing related",
            iteration=0,
            provider="scripted",
            model="test",
            registry=registry,
            routing=ToolRoutingSpec(
                mode="auto",
                budget=ToolBudget(max_visible_tools=1),
                always_include=("local:beta",),
            ),
        )
    )

    assert resolution.plan.local_tools == ("beta",)


async def test_recent_failed_call_penalty_can_drop_tool() -> None:
    registry = ToolRegistry()
    registry.register(
        lambda: "bad",
        name="flaky_lookup",
        description="Lookup flaky service record.",
        category="service",
    )

    resolution = await resolve_tool_routing(
        RoutingContext(
            task="lookup service",
            current_input="lookup service",
            iteration=1,
            provider="scripted",
            model="test",
            registry=registry,
            routing=ToolRoutingSpec(mode="auto", min_score=0.5),
            recent_events=[
                AgentEvent(
                    type=EventTypes.TOOL_CALL_FAILED,
                    data={"name": "flaky_lookup"},
                )
            ],
        )
    )

    assert resolution.plan.local_tools == ()


async def test_selection_result_serializes_through_event_payload() -> None:
    registry = ToolRegistry()
    registry.register(lambda: "ok", name="lookup", description="Lookup records.")
    resolution = await resolve_tool_routing(
        RoutingContext(
            task="lookup",
            current_input="lookup",
            iteration=0,
            provider="scripted",
            model="test",
            registry=registry,
            routing=ToolRoutingSpec(mode="auto"),
        )
    )

    hydrated = event_from_dict(
        event_to_dict(
            AgentEvent(
                type=EventTypes.TOOL_ROUTING_COMPLETED,
                data={"selection": resolution.selection, "plan": resolution.plan},
            )
        )
    )

    assert isinstance(hydrated.data["selection"], ToolSelectionResult)
    assert isinstance(hydrated.data["plan"], ResolvedToolPlan)
    assert hydrated.data["plan"].selected_refs == ("local:lookup",)
