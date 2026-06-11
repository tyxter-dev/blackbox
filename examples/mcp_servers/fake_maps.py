"""Fake external maps/places service — a Tier 0 MCP validation server.

Stands in for a maps provider (geocoding + nearby search) that would
normally require an API key, mirroring the shape of the Google Maps MCP
usage in ``examples/launchmybakery.py``. Fully offline with canned data.

Run standalone::

    python examples/mcp_servers/fake_maps.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from blackbox.mcp import MCPServer
from blackbox.mcp.server import MCPToolError

ADDRESSES = {
    "avenida paulista 1578": {"lat": -23.5614, "lng": -46.6559, "city": "São Paulo"},
    "praca da se": {"lat": -23.5503, "lng": -46.6339, "city": "São Paulo"},
}

PLACES = [
    {"name": "Padaria Bella Massa", "type": "bakery", "lat": -23.5601, "lng": -46.6540, "rating": 4.7},
    {"name": "Café do Centro", "type": "cafe", "lat": -23.5610, "lng": -46.6571, "rating": 4.4},
    {"name": "Empório Paulista", "type": "grocery", "lat": -23.5622, "lng": -46.6548, "rating": 4.2},
    {"name": "Padaria Pão da Sé", "type": "bakery", "lat": -23.5508, "lng": -46.6347, "rating": 4.5},
]

server = MCPServer(
    "fake-maps",
    instructions="Fake maps service for offline validation of external MCP integrations.",
)


@server.tool(
    description="Geocode a street address to coordinates.",
    input_schema={
        "type": "object",
        "properties": {"address": {"type": "string"}},
        "required": ["address"],
    },
)
def geocode(address: str) -> dict:
    key = address.strip().lower().replace(",", "")
    location = ADDRESSES.get(key)
    if location is None:
        raise MCPToolError(
            "address_not_found",
            f"No canned geocoding result for {address!r}.",
            data={"known_addresses": sorted(ADDRESSES)},
        )
    return {"address": address, **location}


@server.tool(
    description="Find places of a given type near coordinates.",
    input_schema={
        "type": "object",
        "properties": {
            "lat": {"type": "number"},
            "lng": {"type": "number"},
            "type": {"type": "string"},
            "limit": {"type": "integer"},
        },
        "required": ["lat", "lng", "type"],
    },
)
def nearby_places(lat: float, lng: float, type: str, limit: int = 5) -> dict:
    matches = [place for place in PLACES if place["type"] == type]
    matches.sort(key=lambda p: abs(p["lat"] - lat) + abs(p["lng"] - lng))
    return {"places": matches[:limit], "total": len(matches)}


if __name__ == "__main__":
    raise SystemExit(server.run_stdio())
