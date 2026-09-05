from __future__ import annotations

from typing import Any

import pytest

from blackbox.core.errors import ConfigurationError
from blackbox.core.policy import PolicyRequest
from blackbox.core.tool_permissions import permission_boundary, tool_request
from blackbox.tools import ToolRegistry, ToolRuntime
from blackbox.workspace_agents.permissions import (
    ApprovalRequirement,
    ConnectorSpec,
    ToolPermission,
    compile_package_permissions,
)
from blackbox.workspace_agents.serialization import (
    workspace_agent_from_dict,
    workspace_agent_to_dict,
)
from blackbox.workspace_agents.spec import WorkspaceAgentSpec


def test_permission_mode_roundtrip_and_validation() -> None:
    assert workspace_agent_from_dict({"name": "old"}).permission_mode == "inherit"
    package = WorkspaceAgentSpec("new", permission_mode="allowlist_v1")
    assert workspace_agent_from_dict(workspace_agent_to_dict(package)) == package
    with pytest.raises(ConfigurationError):
        workspace_agent_from_dict({"name": "bad", "permission_mode": "allow"})


@pytest.mark.parametrize(
    "permissions,connectors",
    [
        ([ToolPermission("a"), ToolPermission("local:a")], []),
        ([], [ConnectorSpec("x", "test"), ConnectorSpec("x", "test")]),
        ([ToolPermission("a", approval=ApprovalRequirement("invalid"))], []),
        ([ToolPermission("a", connector="missing")], []),
        ([ToolPermission("a", connector="x")], [ConnectorSpec("x", "test", tool_refs=["b"])]),
    ],
)
def test_invalid_grants_fail_closed(permissions: Any, connectors: Any) -> None:
    with pytest.raises(ConfigurationError):
        compile_package_permissions(permissions, connectors)


async def test_connector_scopes_and_snapshot_are_authoritative() -> None:
    effects: list[str] = []
    registry = ToolRegistry()
    definition = registry.register(
        lambda: effects.append("yes"),
        name="lookup",
        scopes=["read"],
        metadata={"connector": "crm", "connector_scopes": ["tickets.read"], "ref": "local:evil"},
    )
    permission = ToolPermission(
        "lookup", connector="crm", metadata={"connector": "wrong", "scopes": ["admin"]}
    )
    connector = ConnectorSpec("crm", "test", scopes=["tickets.read"], tool_refs=["lookup"])
    boundary = compile_package_permissions([permission], [connector])
    permission.scopes.clear()
    connector.scopes.clear()
    with permission_boundary((boundary,)):
        assert (await ToolRuntime(registry).call("lookup")).ok
        definition.metadata["connector_scopes"] = ["tickets.delete"]
        assert not (await ToolRuntime(registry).call("lookup")).ok
    assert effects == ["yes"]
    request = permission.to_policy_request(agent_id="a", checkpoint="before_tool_call")
    assert request.metadata["connector"] == "crm"
    assert request.metadata["scopes"] == []
    assert tool_request(definition).metadata["tool_ref"] == "local:lookup"


def test_unannotated_execute_fallback_and_metadata_compatibility() -> None:
    registry = ToolRegistry()
    definition = registry.register(lambda: None, name="unknown", latency="slow", cost="high")
    request = tool_request(definition)
    assert request.metadata["scopes"] == []
    assert request.metadata["permission_scopes"] == ["execute"]
    mcp = registry.register(lambda: None, name="mcp:server.lookup")
    assert tool_request(mcp).metadata["server"] == "server"
    assert tool_request(mcp).metadata["tool"] == "lookup"
    assert request.metadata["latency"] == "slow" and request.metadata["cost"] == "high"
    assert (
        compile_package_permissions([ToolPermission("unknown")], []).decide(request).verdict
        == "deny"
    )
    assert (
        compile_package_permissions([ToolPermission("unknown", scopes=["execute"])], [])
        .decide(request)
        .verdict
        == "allow"
    )
    assert (
        compile_package_permissions([], [])
        .decide(PolicyRequest("before_tool_call", "unknown"))
        .verdict
        == "deny"
    )


def test_restricted_prepare_cannot_drop_boundary() -> None:
    from blackbox.workspace_agents.runtime import prepare_agent_spec

    with pytest.raises(ConfigurationError, match="run_workspace_agent"):
        prepare_agent_spec(
            WorkspaceAgentSpec("restricted", model_provider="echo", permission_mode="allowlist_v1")
        )


def test_restricted_agent_spec_cannot_drop_boundary() -> None:
    with pytest.raises(ConfigurationError, match="run_workspace_agent"):
        WorkspaceAgentSpec(
            "restricted", agent_provider="local", permission_mode="allowlist_v1"
        ).to_agent_spec()


def test_additive_fields_preserve_positional_construction() -> None:
    from blackbox.core.capabilities import AgentCapabilities

    assert not AgentCapabilities(False).supports_sessions
    package = WorkspaceAgentSpec(
        "positional", "", "id", None, None, None, None, [], [], [], [], [], [], []
    )
    assert package.schedules == [] and package.permission_mode == "inherit"
