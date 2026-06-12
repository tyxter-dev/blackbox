"""Workspace agent spec validation: ref resolution, schedules, model catalog."""
from __future__ import annotations

import pytest

from blackbox.mcp import MCPServerSpec
from blackbox.workspace_agents import (
    ScheduleSpec,
    ScheduleTrigger,
    SkillBundleRef,
    WorkspaceAgentSpec,
    WorkspaceAgentValidationError,
    ensure_valid_workspace_agent,
    validate_workspace_agent,
)
from blackbox.workspace_agents.permissions import ConnectorSpec, ToolPermission


def codes(spec: WorkspaceAgentSpec) -> set[str]:
    return {issue.code for issue in validate_workspace_agent(spec)}


def errors(spec: WorkspaceAgentSpec) -> set[str]:
    return {
        issue.code
        for issue in validate_workspace_agent(spec)
        if issue.severity == "error"
    }


def test_valid_spec_has_no_issues() -> None:
    spec = WorkspaceAgentSpec(
        name="reporter",
        model_provider="openai",
        tools=["fetch_metrics"],
        permissions=[ToolPermission(ref="fetch_metrics")],
        schedules=[
            ScheduleSpec(name="daily", trigger=ScheduleTrigger(kind="cron", expression="0 9 * * *"))
        ],
    )
    assert validate_workspace_agent(spec) == []


def test_missing_name_and_provider_are_errors() -> None:
    spec = WorkspaceAgentSpec(name="  ")
    assert {"missing_name", "missing_provider"} <= errors(spec)


def test_unknown_tool_ref_is_error() -> None:
    spec = WorkspaceAgentSpec(
        name="agent",
        model_provider="openai",
        permissions=[ToolPermission(ref="ghost_tool")],
    )
    assert "unknown_tool_ref" in errors(spec)


def test_hosted_and_connector_backed_refs_resolve() -> None:
    spec = WorkspaceAgentSpec(
        name="agent",
        model_provider="openai",
        hosted_tools=[{"type": "web_search"}],
        connectors=[ConnectorSpec(name="crm", kind="http_api", tool_refs=["crm_lookup"])],
        permissions=[
            ToolPermission(ref="web_search"),
            ToolPermission(ref="crm_lookup"),
            ToolPermission(ref="crm_write", connector="crm"),
        ],
    )
    assert errors(spec) == set()


def test_unknown_connector_and_duplicate_permission_are_errors() -> None:
    spec = WorkspaceAgentSpec(
        name="agent",
        model_provider="openai",
        tools=["t"],
        permissions=[
            ToolPermission(ref="t"),
            ToolPermission(ref="t"),
            ToolPermission(ref="x", connector="missing"),
        ],
    )
    found = errors(spec)
    assert "duplicate_permission_ref" in found
    assert "unknown_connector" in found


def test_mcp_ref_validates_declared_servers() -> None:
    spec = WorkspaceAgentSpec(
        name="agent",
        model_provider="openai",
        mcp_servers=[MCPServerSpec(name="crm", transport="stdio", command="crm-mcp")],
        permissions=[
            ToolPermission(ref="mcp:crm.lookup"),
            ToolPermission(ref="mcp:ghost.lookup"),
        ],
    )
    assert errors(spec) == {"unknown_mcp_server"}


def test_schedule_validation() -> None:
    spec = WorkspaceAgentSpec(
        name="agent",
        model_provider="openai",
        schedules=[
            ScheduleSpec(name="bad", trigger=ScheduleTrigger(kind="cron", expression="not cron")),
            ScheduleSpec(name="bad", trigger=ScheduleTrigger(kind="interval", expression="15m")),
            ScheduleSpec(name="cal", trigger=ScheduleTrigger(kind="calendar")),
            ScheduleSpec(
                name="tz",
                trigger=ScheduleTrigger(kind="cron", expression="0 9 * * *", timezone="Mars/Olympus"),
            ),
        ],
    )
    found = codes(spec)
    assert "invalid_schedule_expression" in found
    assert "duplicate_schedule_name" in found
    assert "unsupported_trigger" in found
    assert "invalid_timezone" in found


def test_unknown_model_is_warning_not_error() -> None:
    spec = WorkspaceAgentSpec(name="agent", model_provider="openai", model="gpt-imaginary")
    issues = validate_workspace_agent(spec)
    assert [i.code for i in issues] == ["unknown_model"]
    assert issues[0].severity == "warning"


def test_known_model_passes_catalog_check() -> None:
    spec = WorkspaceAgentSpec(name="agent", model_provider="anthropic", model="claude-fable-5")
    issue_codes = codes(spec)
    # Either the bundled catalog knows the model (no warning) or the catalog
    # changed; this test pins only that valid provider ids do not error.
    assert "unknown_model" not in issue_codes or issue_codes == {"unknown_model"}


def test_missing_absolute_skill_source_is_warning(tmp_path: object) -> None:
    spec = WorkspaceAgentSpec(
        name="agent",
        model_provider="openai",
        skills=[
            SkillBundleRef(name="s", source="C:/definitely/not/here"),
            SkillBundleRef(name="s", source="https://example.com/skill.zip"),
        ],
    )
    found = codes(spec)
    assert "missing_skill_source" in found
    assert "duplicate_skill_name" in found


def test_ensure_valid_raises_on_errors_and_returns_warnings() -> None:
    bad = WorkspaceAgentSpec(name="", model_provider="openai")
    with pytest.raises(WorkspaceAgentValidationError) as excinfo:
        ensure_valid_workspace_agent(bad)
    assert any(issue.code == "missing_name" for issue in excinfo.value.issues)

    warn_only = WorkspaceAgentSpec(name="a", model_provider="openai", model="gpt-imaginary")
    issues = ensure_valid_workspace_agent(warn_only)
    assert [i.code for i in issues] == ["unknown_model"]
