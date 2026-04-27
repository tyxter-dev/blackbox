from __future__ import annotations

import pytest

from agent_runtime import AgentRuntime
from agent_runtime.core.policy import PolicyRequest
from agent_runtime.core.results import AgentResult
from agent_runtime.mcp import MCPServerSpec, MCPToolset
from agent_runtime.models.echo import EchoModelProvider
from agent_runtime.workspace_agents import (
    ApprovalRequirement,
    ConnectorSpec,
    InMemoryWorkspaceAgentRegistry,
    ScheduleSpec,
    ScheduleTrigger,
    SkillBundleRef,
    ToolPermission,
    WorkspaceAgentSpec,
    prepare_agent_spec,
    run_workspace_agent,
    workspace_agent_from_dict,
    workspace_agent_to_dict,
)


def test_workspace_agent_spec_serializes_nested_contracts() -> None:
    spec = WorkspaceAgentSpec(
        name="release-prep",
        instructions="Prepare release notes.",
        model_provider="echo",
        model="echo-mini",
        tools=["lookup_issue"],
        mcp_servers=[
            MCPServerSpec(
                name="issues",
                transport="streamable_http",
                url="https://mcp.example.com/issues",
                allowed_tools=["lookup_issue"],
                require_approval="never",
            )
        ],
        mcp_toolsets=[
            MCPToolset(
                server=MCPServerSpec(
                    name="deployments",
                    transport="streamable_http",
                    url="https://mcp.example.com/deployments",
                    allowed_tools=["create_deployment"],
                    require_approval="always",
                ),
                mode="provider_native",
                description="Deployment operations.",
            )
        ],
        connectors=[
            ConnectorSpec(
                name="github",
                kind="github",
                auth_mode="end_user",
                scopes=["issues:read"],
                tool_refs=["lookup_issue"],
            )
        ],
        permissions=[
            ToolPermission(
                ref="lookup_issue",
                scopes=["read"],
                connector="github",
                approval=ApprovalRequirement(mode="never"),
            )
        ],
        schedules=[
            ScheduleSpec(
                name="weekday",
                trigger=ScheduleTrigger(
                    kind="cron",
                    expression="0 16 * * 1-5",
                    timezone="America/Sao_Paulo",
                ),
            )
        ],
        skills=[SkillBundleRef(name="release-notes", source="skills/release-notes")],
    )

    data = workspace_agent_to_dict(spec)
    restored = workspace_agent_from_dict(data)

    assert restored == spec
    assert restored.connectors[0].auth_mode == "end_user"
    assert restored.mcp_servers[0].name == "issues"
    assert restored.mcp_toolsets[0].server.allowed_tools == ["create_deployment"]
    assert restored.mcp_toolsets[0].mode == "provider_native"
    assert restored.permissions[0].approval.mode == "never"
    assert restored.schedules[0].trigger.expression == "0 16 * * 1-5"


def test_workspace_agent_converts_to_agent_spec_metadata() -> None:
    spec = WorkspaceAgentSpec(
        name="triage",
        instructions="Triage incoming reports.",
        model="echo-mini",
        tools=["search"],
        mcp_servers=[MCPServerSpec(name="support", transport="stdio", command="support-mcp")],
        mcp_toolsets=[
            MCPToolset(
                server=MCPServerSpec(name="crm", transport="stdio", command="crm-mcp"),
                mode="local",
            )
        ],
        skills=[SkillBundleRef(name="support-triage")],
    )

    agent_spec = spec.to_agent_spec()

    assert agent_spec.name == "triage"
    assert agent_spec.instructions == "Triage incoming reports."
    assert agent_spec.tools == ["search"]
    assert [server.name for server in agent_spec.mcp_servers] == ["support", "crm"]
    assert agent_spec.permissions["mcp_servers"] == ["support", "crm"]
    assert agent_spec.metadata["workspace_agent_id"] == spec.id
    assert agent_spec.metadata["skills"] == ["support-triage"]


def test_prepare_agent_spec_exposes_mcp_toolsets_to_runtime() -> None:
    spec = WorkspaceAgentSpec(
        name="support",
        model_provider="echo",
        model="echo-mini",
        mcp_servers=[MCPServerSpec(name="orders", transport="stdio", command="orders-mcp")],
        mcp_toolsets=[
            MCPToolset(
                server=MCPServerSpec(name="crm", transport="stdio", command="crm-mcp"),
                mode="local",
                allowed_tools=["create_task"],
            )
        ],
    )

    prepared = prepare_agent_spec(spec)

    assert prepared["provider"] == "echo"
    assert [toolset.server.name for toolset in prepared["toolsets"]] == ["orders", "crm"]
    assert prepared["toolsets"][0].mode == "auto"
    assert prepared["toolsets"][1].allowed_tools == ["create_task"]


def test_tool_permission_policy_request_carries_agent_context() -> None:
    permission = ToolPermission(
        ref="mcp:github.create_issue",
        scopes=["write"],
        connector="github",
        approval=ApprovalRequirement(mode="on_write"),
    )

    request = permission.to_policy_request(
        agent_id="agent_1",
        checkpoint="before_tool_call",
        arguments={"title": "Bug"},
    )

    assert isinstance(request, PolicyRequest)
    assert permission.allows("write")
    assert not permission.allows("delete")
    assert permission.approval.requires_approval_for("write")
    assert request.metadata["agent_id"] == "agent_1"
    assert request.metadata["tool_ref"] == "mcp:github.create_issue"
    assert request.metadata["approval_mode"] == "on_write"


@pytest.mark.asyncio
async def test_in_memory_workspace_agent_registry_publishes_and_lists() -> None:
    registry = InMemoryWorkspaceAgentRegistry()
    spec = WorkspaceAgentSpec(name="daily-summary")

    await registry.save(spec)
    published = await registry.publish(spec.id, visibility="workspace")
    listed = await registry.list(visibility="workspace")

    assert published.publication.visibility == "workspace"
    assert published.publication.directory_enabled is True
    assert listed == [published]

    deprecated = await registry.deprecate(spec.id, reason="replaced")
    assert deprecated.publication.visibility == "unlisted"
    assert deprecated.publication.metadata["deprecated"] is True
    assert deprecated.publication.metadata["deprecation_reason"] == "replaced"


def test_prepare_agent_spec_requires_model_provider() -> None:
    with pytest.raises(ValueError, match="model_provider"):
        prepare_agent_spec(WorkspaceAgentSpec(name="missing-provider"))


@pytest.mark.asyncio
async def test_run_workspace_agent_uses_existing_runtime_loop() -> None:
    runtime = AgentRuntime()
    runtime.registry.register_model(EchoModelProvider())
    spec = WorkspaceAgentSpec(name="echo-agent", model_provider="echo", model="echo-mini")

    result: AgentResult[str] = await run_workspace_agent(
        runtime, spec, input="hello from workspace agent"
    )

    assert result.text == "hello from workspace agent"
    assert result.provider_state is not None
    assert result.provider_state.provider == "echo"
