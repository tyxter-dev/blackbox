"""Fake external CRM — a Tier 0 MCP validation server.

Stands in for a third-party CRM SaaS that would normally require an account
and API key. Speaks blackbox's line-delimited stdio MCP transport, so MCP
examples and tests can exercise the full connector path (initialize,
tools/list, tools/call, namespacing, trust, events) completely offline.

Run standalone::

    python examples/mcp_servers/fake_crm.py

or point an ``MCPServerSpec`` at it::

    MCPServerSpec(
        name="fake-crm",
        transport="stdio",
        command=sys.executable,
        args=["examples/mcp_servers/fake_crm.py"],
    )
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from blackbox.mcp import MCPServer
from blackbox.mcp.server import MCPToolError

CUSTOMERS = {
    "cus_042": {
        "id": "cus_042",
        "name": "Helena Prado",
        "company": "Prado Arquitetura",
        "phone": "+55 11 99999-0042",
        "tags": ["architecture", "returning"],
    },
    "cus_107": {
        "id": "cus_107",
        "name": "Marcos Lima",
        "company": "Lima & Filhos",
        "phone": "+55 21 98888-0107",
        "tags": ["construction"],
    },
}

DEALS = [
    {"id": "deal_9001", "customer_id": "cus_042", "title": "Office renovation", "stage": "proposal", "value": 18500},
    {"id": "deal_9002", "customer_id": "cus_042", "title": "Annual retainer", "stage": "negotiation", "value": 42000},
    {"id": "deal_9003", "customer_id": "cus_107", "title": "Warehouse build-out", "stage": "qualified", "value": 130000},
]

_TASK_IDS = iter(range(5001, 6000))

server = MCPServer(
    "fake-crm",
    instructions="Fake CRM for offline validation of external MCP integrations.",
)


@server.tool(
    description="Look up a customer record by id.",
    input_schema={
        "type": "object",
        "properties": {"customer_id": {"type": "string"}},
        "required": ["customer_id"],
    },
)
def lookup_customer(customer_id: str) -> dict:
    customer = CUSTOMERS.get(customer_id)
    if customer is None:
        raise MCPToolError(
            "customer_not_found",
            f"No customer with id {customer_id!r}.",
            data={"known_ids": sorted(CUSTOMERS)},
        )
    return customer


@server.tool(
    description="List open deals, optionally filtered by customer id.",
    input_schema={
        "type": "object",
        "properties": {"customer_id": {"type": ["string", "null"]}},
        "required": [],
    },
)
def list_open_deals(customer_id: str | None = None) -> dict:
    deals = [d for d in DEALS if customer_id is None or d["customer_id"] == customer_id]
    return {"deals": deals, "total": len(deals)}


@server.tool(
    description="Create a follow-up task for a customer.",
    input_schema={
        "type": "object",
        "properties": {
            "customer_id": {"type": "string"},
            "title": {"type": "string"},
            "due_date": {"type": "string"},
        },
        "required": ["customer_id", "title", "due_date"],
    },
)
def create_followup_task(customer_id: str, title: str, due_date: str) -> dict:
    if customer_id not in CUSTOMERS:
        raise MCPToolError("customer_not_found", f"No customer with id {customer_id!r}.")
    return {
        "task": {
            "id": f"task_{next(_TASK_IDS)}",
            "customer_id": customer_id,
            "title": title,
            "due_date": due_date,
            "status": "open",
        }
    }


if __name__ == "__main__":
    raise SystemExit(server.run_stdio())
