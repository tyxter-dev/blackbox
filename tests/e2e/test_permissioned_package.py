"""Offline public package roundtrip and execution across both controlled surfaces."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from blackbox import ToolPermission
from blackbox.providers.agent_adapters.local import LocalAgentProvider
from blackbox.tools import ToolRuntime
from blackbox.workspace_agents.package import (
    load_workspace_agent_package,
    save_workspace_agent_package,
)
from blackbox.workspace_agents.runtime import run_workspace_agent
from tests.fixtures.scripted_model import text_only_turn, tool_call_turn
from tests.runtime.test_package_permissions import setup, spec


@pytest.mark.parametrize("surface", ["model", "local"])
async def test_permissioned_package_roundtrip_walkthrough(tmp_path: Path, surface: str) -> None:
    runtime, scripted, effects = setup()
    package = spec()
    if surface == "local":
        runtime.registry.register_agent(
            LocalAgentProvider(runtime.models, tools=ToolRuntime(runtime.tools.registry))
        )
        package = replace(package, agent_provider="local")
    save_workspace_agent_package(package, tmp_path / "package")
    loaded = load_workspace_agent_package(tmp_path / "package")
    assert loaded.permission_mode == "allowlist_v1"
    assert loaded.permissions == [ToolPermission("read")]
    scripted.queue(tool_call_turn(call_id="read", name="read", arguments={}))
    scripted.queue(tool_call_turn(call_id="write", name="write", arguments={}))
    scripted.queue(text_only_turn("package complete"))
    result = await run_workspace_agent(runtime, loaded, input="read then attempt write")
    assert result.text == "package complete"
    assert effects == ["read"]
    assert scripted.calls[2].input[0].status == "failed"
    assert all({tool["name"] for tool in turn.tools} == {"read"} for turn in scripted.calls)
