"""Maia-style CRM business case implemented with Agent Runtime.

This ports the useful shape of Maia Assistants' ``bella_cucina__returning_guest``
benchmark into this library without depending on Maia internals.

The case:

- David Park is a returning Bella Cucina guest.
- He asks over WhatsApp about the private dining room next Friday for 12 people.
- The app enables only ``query_calendar`` and ``create_task`` for this run.
- The prompt composer must include private-room/task guidance, and must not leak
  customer-creation instructions because ``create_customer`` is not enabled.

Run:

    python examples/maia_bella_cucina_business_case.py
"""
from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - optional example dependency
    def load_dotenv(*args: Any, **kwargs: Any) -> bool:
        return False

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if SRC_DIR.exists():
    sys.path.insert(0, str(SRC_DIR))

load_dotenv(REPO_ROOT / ".env")

from agent_runtime import (  # noqa: E402
    AgentRuntime,
    FragmentRequirements,
    FragmentSelector,
    PromptFragment,
    PromptSpec,
)
from agent_runtime.providers.model_adapters.echo import EchoModelProvider  # noqa: E402
from agent_runtime.tools import ToolResult  # noqa: E402

CASE_KEY = "bella_cucina__returning_guest"
CASE_INPUT = (
    "Hi Bella, it's been a while! We loved the osso buco last time. Can you "
    "check if the private room is available next Friday evening for about 12 "
    "people? We're planning a team dinner."
)
CASE_TOOLS = ["query_calendar", "create_task"]
CASE_CONTEXT_FLAGS = ["customer.exists", "channel.whatsapp"]
RUN_DATETIME = "2026-04-28T09:00:00-05:00"


@dataclass(slots=True)
class Customer:
    id: str
    full_name: str
    phone_number: str | None = None
    email: str | None = None
    notes: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class Task:
    title: str
    customer_id: str | None = None
    due_date: str | None = None
    notes: str | None = None


@dataclass(slots=True)
class BellaCucinaCRM:
    customers: dict[str, Customer] = field(default_factory=dict)
    tasks: list[Task] = field(default_factory=list)

    def seed_returning_guest(self) -> Customer:
        customer = Customer(
            id="cust_david_park",
            full_name="David Park",
            phone_number="+1-312-555-0142",
            email="david.park@example.com",
            notes={
                "status": "Active",
                "last_visit": "2025-11-14",
                "preferences": "Prefers red wine, gluten-free partner",
                "total_visits": "7",
                "lifetime_spend": "$2,840",
            },
        )
        self.customers[customer.id] = customer
        return customer


def bella_cucina_instructions(customer: Customer) -> str:
    return f"""
You are Bella, the virtual host for Bella Cucina, an Italian restaurant in downtown Chicago.

Business context:
Bella Cucina is an upscale-casual Italian restaurant at 742 Michigan Ave, Chicago.
Open Tue-Sun 11:30am-10pm, closed Mondays. Reservations are recommended for dinner.
Private dining room seats 20 and requires a $500 minimum.
Specialties include handmade pasta, wood-fired pizza, and osso buco.
Current business datetime: {RUN_DATETIME}. Resolve relative dates from this value.

Goals:
- Book reservations for new and returning guests.
- Capture dining preferences and special occasion details for returning guests.
- Create follow-up tasks for private dining and catering inquiries.
- Use only tools enabled for the current run when taking CRM actions.

Constraints:
- Never quote exact wine prices; refer guests to the sommelier.
- Do not accept reservations for Mondays.
- Do not promise specific tables; confirm with the host team.

Customer context:
Existing customer id: {customer.id}
Name: {customer.full_name}
Preferences: {customer.notes["preferences"]}
Total visits: {customer.notes["total_visits"]}
""".strip()


def install_bella_cucina_tools(runtime: AgentRuntime, crm: BellaCucinaCRM) -> None:
    runtime.tools.register(
        lambda name=None, phone_number=None: query_customers(
            crm,
            name=name,
            phone_number=phone_number,
        ),
        name="query_customers",
        description="Search existing guest records by name or phone number.",
        parameters={
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "phone_number": {"type": "string"},
            },
        },
        tags=["crm.customer"],
    )
    runtime.tools.register(
        lambda full_name, phone_number=None, email=None: create_customer(
            crm,
            full_name=full_name,
            phone_number=phone_number,
            email=email,
        ),
        name="create_customer",
        description="Create or update a guest CRM record.",
        parameters={
            "type": "object",
            "properties": {
                "full_name": {"type": "string"},
                "phone_number": {"type": "string"},
                "email": {"type": "string"},
            },
            "required": ["full_name"],
        },
        tags=["crm.customer"],
        prompt_fragments=[
            PromptFragment(
                id="crm.customer.lookup_before_create",
                text=(
                    "Before creating a new guest, call query_customers. "
                    "Only call create_customer when no existing guest matches."
                ),
                source="tool:create_customer",
                priority=90,
                applies_to=FragmentSelector(tools={"create_customer"}),
                requires=FragmentRequirements(
                    required_tools={"query_customers", "create_customer"}
                ),
                metadata={
                    "protocol": {
                        "precondition": "query_customers before create_customer",
                        "tools_in_order": ["query_customers", "create_customer"],
                    }
                },
            )
        ],
    )
    runtime.tools.register(
        lambda date, party_size=None, room_type=None: query_calendar(
            date=date,
            party_size=party_size,
            room_type=room_type,
        ),
        name="query_calendar",
        description="Check reservation or private-room availability.",
        parameters={
            "type": "object",
            "properties": {
                "date": {"type": "string"},
                "party_size": {"type": "integer"},
                "room_type": {"type": "string"},
            },
            "required": ["date"],
        },
        tags=["scheduling.calendar"],
        prompt_fragments=[
            PromptFragment(
                id="bella.private_room.check_availability",
                text=(
                    "For private dining room requests, call query_calendar before "
                    "saying whether the room is available."
                ),
                source="tool:query_calendar",
                priority=70,
                applies_to=FragmentSelector(tools={"query_calendar"}),
            )
        ],
    )
    runtime.tools.register(
        lambda title, customer_id=None, due_date=None, notes=None: create_task(
            crm,
            title=title,
            customer_id=customer_id,
            due_date=due_date,
            notes=notes,
        ),
        name="create_task",
        description="Create an internal follow-up task for the host team.",
        parameters={
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "customer_id": {"type": "string"},
                "due_date": {"type": "string"},
                "notes": {"type": "string"},
            },
            "required": ["title"],
        },
        tags=["crm.task"],
        prompt_fragments=[
            PromptFragment(
                id="bella.private_room.follow_up_task",
                text=(
                    "Use create_task for private dining requests that need host-team "
                    "confirmation, setup details, or catering follow-up. Create one "
                    "task per distinct guest plus action plus deadline combination."
                ),
                source="tool:create_task",
                priority=80,
                applies_to=FragmentSelector(tools={"create_task"}),
                requires=FragmentRequirements(required_tools={"query_calendar"}),
                metadata={
                    "protocol": {
                        "precondition": "query_calendar before private-room follow-up task"
                    }
                },
            )
        ],
    )


def install_bella_cucina_prompt_pack(runtime: AgentRuntime) -> None:
    runtime.prompt_fragments.register(
        PromptFragment(
            id="bella.returning_guest.no_duplicate_customer",
            text=(
                "When customer context identifies an existing guest, use that customer id "
                "for follow-up work and do not create a duplicate customer record."
            ),
            source="pack:bella_cucina",
            priority=100,
            applies_to=FragmentSelector(
                tool_tags={"crm.task"},
                context_flags={"customer.exists"},
            ),
            metadata={
                "protocol": {
                    "forbidden_when_context_customer_exists": ["create_customer"]
                }
            },
        )
    )
    runtime.prompt_fragments.register(
        PromptFragment(
            id="channel.whatsapp.no_phone_recollection",
            text=(
                "For WhatsApp conversations, do not ask the guest for a phone number "
                "unless the app explicitly says it is missing."
            ),
            source="pack:channel.whatsapp",
            priority=75,
            applies_to=FragmentSelector(channels={"whatsapp"}),
        )
    )
    runtime.prompt_fragments.register(
        PromptFragment(
            id="bella.private_room.minimum",
            text=(
                "For private dining room inquiries, mention that the room seats up to "
                "20 guests and has a $500 minimum when relevant."
            ),
            source="pack:bella_cucina",
            priority=60,
            applies_to=FragmentSelector(tool_tags={"scheduling.calendar"}),
        )
    )


async def build_returning_guest_plan(runtime: AgentRuntime, *, provider: str) -> Any:
    customer = _david_park(runtime)
    return await runtime.plan_run(
        provider=provider,
        input=CASE_INPUT,
        instructions=bella_cucina_instructions(customer),
        tools=CASE_TOOLS,
        prompt=PromptSpec(mode="tool_aware", channel="whatsapp", parity="error"),
        context_flags=CASE_CONTEXT_FLAGS,
    )


def create_bella_cucina_runtime() -> tuple[AgentRuntime, BellaCucinaCRM]:
    runtime = AgentRuntime()
    runtime.registry.register_model(EchoModelProvider())
    crm = BellaCucinaCRM()
    crm.seed_returning_guest()
    install_bella_cucina_tools(runtime, crm)
    install_bella_cucina_prompt_pack(runtime)
    return runtime, crm


def query_customers(
    crm: BellaCucinaCRM,
    *,
    name: str | None = None,
    phone_number: str | None = None,
) -> ToolResult:
    matches = [
        customer
        for customer in crm.customers.values()
        if (name and name.lower() in customer.full_name.lower())
        or (phone_number and phone_number == customer.phone_number)
    ]
    if not matches:
        return ToolResult(content="Found 0 guest(s).", payload={"matches": []})
    lines = [f"{customer.id}: {customer.full_name}" for customer in matches]
    return ToolResult(content="\n".join(lines), payload={"matches": matches})


def create_customer(
    crm: BellaCucinaCRM,
    *,
    full_name: str,
    phone_number: str | None = None,
    email: str | None = None,
) -> ToolResult:
    customer_id = f"cust_{full_name.lower().replace(' ', '_')}"
    customer = crm.customers.get(customer_id) or Customer(
        id=customer_id,
        full_name=full_name,
    )
    customer.phone_number = phone_number or customer.phone_number
    customer.email = email or customer.email
    crm.customers[customer.id] = customer
    return ToolResult(
        content=f"Guest record saved for {customer.full_name}.",
        payload={"customer_id": customer.id},
    )


def query_calendar(
    *,
    date: str,
    party_size: int | None = None,
    room_type: str | None = None,
) -> ToolResult:
    details = {
        "date": date,
        "party_size": party_size,
        "room_type": room_type,
        "available_slots": ["6:30pm", "7:30pm", "8:00pm"],
        "private_room_capacity": 20,
        "private_room_minimum": "$500",
    }
    return ToolResult(
        content=(
            "Private dining room availability found: 6:30pm, 7:30pm, or 8:00pm. "
            "Capacity is 20 guests; minimum spend is $500."
        ),
        payload=details,
    )


def create_task(
    crm: BellaCucinaCRM,
    *,
    title: str,
    customer_id: str | None = None,
    due_date: str | None = None,
    notes: str | None = None,
) -> ToolResult:
    task = Task(title=title, customer_id=customer_id, due_date=due_date, notes=notes)
    crm.tasks.append(task)
    return ToolResult(content=f"Task created: {title}", payload={"task": task})


async def main() -> None:
    runtime, _ = create_bella_cucina_runtime()
    plan = await build_returning_guest_plan(runtime, provider="echo:echo-mini")
    assert plan.prompt is not None

    print(f"Case: {CASE_KEY}")
    print(f"Effective tools: {', '.join(plan.effective_tool_ids)}")
    print(f"Parity: {plan.prompt.metadata['parity']}")
    print("Selected fragments:")
    for fragment_id in plan.prompt.metadata["fragment_ids"]:
        print(f"- {fragment_id}")
    print("Skipped fragments:")
    for fragment in plan.prompt.metadata["skipped_fragments"]:
        print(f"- {fragment['id']}: {fragment['reason']}")
    print("Cache sections:")
    for section in plan.prompt.cache_sections:
        print(f"- {section.id}: cacheable={section.cacheable}")
    print("\nComposed instructions:\n")
    print(plan.prompt.instructions)


def _david_park(runtime: AgentRuntime) -> Customer:
    for tool in runtime.tools.all_tools():
        if tool.name == "query_customers":
            result = tool.function(name="David Park")
            if isinstance(result, ToolResult) and result.payload:
                matches = result.payload.get("matches")
                if matches:
                    return matches[0]
    raise RuntimeError("David Park seed customer is missing.")


if __name__ == "__main__":
    asyncio.run(main())
