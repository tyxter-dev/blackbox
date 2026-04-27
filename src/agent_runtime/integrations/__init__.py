from __future__ import annotations

from importlib import import_module
from typing import Any

_LAZY_EXPORTS: dict[str, tuple[str, str]] = {
    "BIGQUERY_MCP_URL": ("agent_runtime.integrations.google", "BIGQUERY_MCP_URL"),
    "BIGQUERY_SCOPE": ("agent_runtime.integrations.google", "BIGQUERY_SCOPE"),
    "MAPS_MCP_URL": ("agent_runtime.integrations.google", "MAPS_MCP_URL"),
    "GoogleBigQueryMCPAuth": (
        "agent_runtime.integrations.google",
        "GoogleBigQueryMCPAuth",
    ),
    "google_application_default_bigquery_auth": (
        "agent_runtime.integrations.google",
        "google_application_default_bigquery_auth",
    ),
    "google_bigquery_mcp_toolset": (
        "agent_runtime.integrations.google",
        "google_bigquery_mcp_toolset",
    ),
    "google_maps_mcp_toolset": (
        "agent_runtime.integrations.google",
        "google_maps_mcp_toolset",
    ),
}

__all__ = [
    "BIGQUERY_MCP_URL",
    "BIGQUERY_SCOPE",
    "MAPS_MCP_URL",
    "GoogleBigQueryMCPAuth",
    "google_application_default_bigquery_auth",
    "google_bigquery_mcp_toolset",
    "google_maps_mcp_toolset",
]


def __getattr__(name: str) -> Any:
    try:
        module_name, attr_name = _LAZY_EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc
    value = getattr(import_module(module_name), attr_name)
    globals()[name] = value
    return value
