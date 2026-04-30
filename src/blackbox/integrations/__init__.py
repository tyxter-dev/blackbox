from __future__ import annotations

from importlib import import_module
from typing import Any

_LAZY_EXPORTS: dict[str, tuple[str, str]] = {
    "BIGQUERY_MCP_URL": ("blackbox.integrations.google", "BIGQUERY_MCP_URL"),
    "BIGQUERY_SCOPE": ("blackbox.integrations.google", "BIGQUERY_SCOPE"),
    "MAPS_MCP_URL": ("blackbox.integrations.google", "MAPS_MCP_URL"),
    "GoogleBigQueryMCPAuth": (
        "blackbox.integrations.google",
        "GoogleBigQueryMCPAuth",
    ),
    "google_application_default_bigquery_auth": (
        "blackbox.integrations.google",
        "google_application_default_bigquery_auth",
    ),
    "google_bigquery_mcp_toolset": (
        "blackbox.integrations.google",
        "google_bigquery_mcp_toolset",
    ),
    "google_maps_mcp_toolset": (
        "blackbox.integrations.google",
        "google_maps_mcp_toolset",
    ),
    "OpenAIVectorStoreDocument": (
        "blackbox.integrations.openai",
        "OpenAIVectorStoreDocument",
    ),
    "OpenAIVectorStoreHandle": (
        "blackbox.integrations.openai",
        "OpenAIVectorStoreHandle",
    ),
    "create_openai_vector_store": (
        "blackbox.integrations.openai",
        "create_openai_vector_store",
    ),
    "temporary_openai_file_search": (
        "blackbox.integrations.openai",
        "temporary_openai_file_search",
    ),
    "temporary_openai_vector_store": (
        "blackbox.integrations.openai",
        "temporary_openai_vector_store",
    ),
}

__all__ = [
    "BIGQUERY_MCP_URL",
    "BIGQUERY_SCOPE",
    "MAPS_MCP_URL",
    "GoogleBigQueryMCPAuth",
    "OpenAIVectorStoreDocument",
    "OpenAIVectorStoreHandle",
    "create_openai_vector_store",
    "google_application_default_bigquery_auth",
    "google_bigquery_mcp_toolset",
    "google_maps_mcp_toolset",
    "temporary_openai_file_search",
    "temporary_openai_vector_store",
]


def __getattr__(name: str) -> Any:
    try:
        module_name, attr_name = _LAZY_EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc
    value = getattr(import_module(module_name), attr_name)
    globals()[name] = value
    return value
