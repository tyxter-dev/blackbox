"""Fake external booking/calendar service — a Tier 0 MCP validation server.

Stands in for a third-party scheduling SaaS (slots, bookings, cancellations)
that would normally require an account and OAuth. Fully offline; state lives
in process memory for the lifetime of the server.

Run standalone::

    python examples/mcp_servers/fake_booking.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from blackbox.mcp import MCPServer
from blackbox.mcp.server import MCPToolError

SLOTS = {
    "2026-06-15": [
        {"slot_id": "slot_0915", "start": "09:15", "end": "10:00", "available": True},
        {"slot_id": "slot_1100", "start": "11:00", "end": "11:45", "available": True},
        {"slot_id": "slot_1430", "start": "14:30", "end": "15:15", "available": False},
    ],
    "2026-06-16": [
        {"slot_id": "slot_1000", "start": "10:00", "end": "10:45", "available": True},
    ],
}

BOOKINGS: dict[str, dict] = {}
_BOOKING_IDS = iter(range(7001, 8000))

server = MCPServer(
    "fake-booking",
    instructions="Fake booking service for offline validation of external MCP integrations.",
)


@server.tool(
    description="List appointment slots for a date (YYYY-MM-DD).",
    input_schema={
        "type": "object",
        "properties": {"date": {"type": "string"}},
        "required": ["date"],
    },
)
def list_slots(date: str) -> dict:
    slots = SLOTS.get(date, [])
    return {"date": date, "slots": slots, "total": len(slots)}


@server.tool(
    description="Book an available slot for a customer.",
    input_schema={
        "type": "object",
        "properties": {
            "slot_id": {"type": "string"},
            "customer_name": {"type": "string"},
        },
        "required": ["slot_id", "customer_name"],
    },
)
def book_slot(slot_id: str, customer_name: str) -> dict:
    for date, slots in SLOTS.items():
        for slot in slots:
            if slot["slot_id"] != slot_id:
                continue
            if not slot["available"]:
                raise MCPToolError("slot_unavailable", f"Slot {slot_id} is already taken.")
            slot["available"] = False
            booking = {
                "booking_id": f"bk_{next(_BOOKING_IDS)}",
                "slot_id": slot_id,
                "date": date,
                "start": slot["start"],
                "customer_name": customer_name,
                "status": "confirmed",
            }
            BOOKINGS[booking["booking_id"]] = booking
            return {"booking": booking}
    raise MCPToolError("slot_not_found", f"No slot with id {slot_id!r}.")


@server.tool(
    description="Cancel a confirmed booking.",
    input_schema={
        "type": "object",
        "properties": {"booking_id": {"type": "string"}},
        "required": ["booking_id"],
    },
)
def cancel_booking(booking_id: str) -> dict:
    booking = BOOKINGS.get(booking_id)
    if booking is None:
        raise MCPToolError("booking_not_found", f"No booking with id {booking_id!r}.")
    booking["status"] = "cancelled"
    for slots in SLOTS.values():
        for slot in slots:
            if slot["slot_id"] == booking["slot_id"]:
                slot["available"] = True
    return {"booking": booking}


if __name__ == "__main__":
    raise SystemExit(server.run_stdio())
