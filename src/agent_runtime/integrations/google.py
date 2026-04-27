from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Literal, cast

from agent_runtime.mcp import MCPServerSpec, MCPToolset

MAPS_MCP_URL = "https://mapstools.googleapis.com/mcp"
BIGQUERY_MCP_URL = "https://bigquery.googleapis.com/mcp"
BIGQUERY_SCOPE = "https://www.googleapis.com/auth/bigquery"

MCPRouteMode = Literal["auto", "local", "provider_native"]
MCPApprovalMode = Literal["always", "never", "auto"]


@dataclass(slots=True, frozen=True)
class GoogleBigQueryMCPAuth:
    access_token: str
    project_id: str


def google_maps_mcp_toolset(
    *,
    api_key: str | None = None,
    mode: MCPRouteMode = "provider_native",
    name: str = "maps",
    require_approval: MCPApprovalMode = "never",
    allowed_tools: list[str] | None = None,
    denied_tools: list[str] | None = None,
) -> MCPToolset:
    """Return a ready-to-use Google Maps Platform MCP toolset."""
    resolved_api_key = api_key or _required_env("MAPS_API_KEY")
    return MCPToolset(
        server=MCPServerSpec(
            name=name,
            transport="streamable_http",
            url=MAPS_MCP_URL,
            headers={"X-Goog-Api-Key": resolved_api_key},
            allowed_tools=allowed_tools,
            denied_tools=denied_tools,
            require_approval=require_approval,
            allow_remote_http=True,
        ),
        mode=mode,
        description="Google Maps tools for place search, geocoding, and routes.",
    )


def google_bigquery_mcp_toolset(
    *,
    access_token: str | None = None,
    project_id: str | None = None,
    mode: MCPRouteMode = "provider_native",
    name: str = "bigquery",
    require_approval: MCPApprovalMode = "never",
    allowed_tools: list[str] | None = None,
    denied_tools: list[str] | None = None,
    dataset: str | None = None,
) -> MCPToolset:
    """Return a ready-to-use Google BigQuery MCP toolset.

    If ``access_token`` or ``project_id`` is omitted, the helper resolves them
    through Google Application Default Credentials.
    """
    auth = (
        GoogleBigQueryMCPAuth(
            access_token=access_token,
            project_id=_project_id(project_id, None),
        )
        if access_token is not None
        else google_application_default_bigquery_auth(project_id=project_id)
    )
    metadata = {"dataset": dataset} if dataset is not None else {}
    return MCPToolset(
        server=MCPServerSpec(
            name=name,
            transport="streamable_http",
            url=BIGQUERY_MCP_URL,
            headers={"x-goog-user-project": auth.project_id},
            authorization=f"Bearer {auth.access_token}",
            allowed_tools=allowed_tools,
            denied_tools=denied_tools,
            require_approval=require_approval,
            allow_remote_http=True,
            metadata=metadata,
        ),
        mode=mode,
        description="Google BigQuery tools.",
        provider_extra={
            # Provider-native MCP expects the OAuth access token value; the
            # runtime-owned HTTP transport above expects a full Authorization header.
            "authorization": auth.access_token,
        },
    )


def google_application_default_bigquery_auth(
    *,
    project_id: str | None = None,
    scopes: list[str] | None = None,
) -> GoogleBigQueryMCPAuth:
    """Resolve a BigQuery OAuth token and project through Google ADC."""
    try:
        import google.auth
        from google.auth.transport.requests import Request
    except ImportError as exc:
        raise RuntimeError(
            "Google BigQuery MCP auth requires google-auth. Install with "
            "`pip install -e .[google]`."
        ) from exc

    credentials, auth_project_id = google.auth.default(scopes=scopes or [BIGQUERY_SCOPE])
    cast(Any, credentials).refresh(Request())
    token = credentials.token
    if not token:
        raise RuntimeError("Application Default Credentials did not return an access token.")
    resolved_project_id = _project_id(project_id, auth_project_id)
    return GoogleBigQueryMCPAuth(access_token=str(token), project_id=resolved_project_id)


def _project_id(explicit_project_id: str | None, auth_project_id: str | None) -> str:
    project_id = (
        explicit_project_id
        or os.getenv("GOOGLE_CLOUD_PROJECT")
        or os.getenv("GOOGLE_CLOUD_PROJECT_ID")
        or auth_project_id
    )
    if not project_id:
        raise RuntimeError(
            "GOOGLE_CLOUD_PROJECT is required, or Application Default Credentials "
            "must resolve a project id."
        )
    return project_id


def _required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"{name} is required.")
    return value
