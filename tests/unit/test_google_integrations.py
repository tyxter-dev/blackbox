from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from agent_runtime.integrations.google import (
    BIGQUERY_MCP_URL,
    MAPS_MCP_URL,
    google_bigquery_mcp_toolset,
    google_maps_mcp_toolset,
)
from agent_runtime.mcp import to_remote_mcp

ROOT = Path(__file__).resolve().parents[2]


def test_google_maps_mcp_toolset_uses_api_key_header() -> None:
    toolset = google_maps_mcp_toolset(api_key="maps-key")

    assert toolset.server.name == "maps"
    assert toolset.server.url == MAPS_MCP_URL
    assert toolset.server.headers == {"X-Goog-Api-Key": "maps-key"}
    assert toolset.server.require_approval == "never"
    assert toolset.server.allow_remote_http is True
    assert toolset.mode == "provider_native"


def test_google_maps_mcp_toolset_can_read_api_key_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAPS_API_KEY", "maps-key")

    toolset = google_maps_mcp_toolset()

    assert toolset.server.headers == {"X-Goog-Api-Key": "maps-key"}


def test_google_bigquery_mcp_toolset_uses_bearer_for_runtime_and_token_for_provider() -> None:
    toolset = google_bigquery_mcp_toolset(
        access_token="oauth-token",
        project_id="project-1",
    )
    remote = to_remote_mcp(toolset)

    assert toolset.server.name == "bigquery"
    assert toolset.server.url == BIGQUERY_MCP_URL
    assert toolset.server.headers == {"x-goog-user-project": "project-1"}
    assert toolset.server.authorization == "Bearer oauth-token"
    assert remote.authorization == "oauth-token"
    assert remote.extra == {}


def test_google_bigquery_mcp_toolset_can_read_project_from_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "project-env")

    toolset = google_bigquery_mcp_toolset(access_token="oauth-token")

    assert toolset.server.headers == {"x-goog-user-project": "project-env"}


def test_root_package_lazily_exposes_integrations() -> None:
    code = """
import sys
import agent_runtime
print("agent_runtime.integrations" in sys.modules)
from agent_runtime import google_maps_mcp_toolset
print(google_maps_mcp_toolset.__name__)
print("agent_runtime.integrations.google" in sys.modules)
"""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT / "src")
    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )

    assert completed.stdout.splitlines() == [
        "False",
        "google_maps_mcp_toolset",
        "True",
    ]
