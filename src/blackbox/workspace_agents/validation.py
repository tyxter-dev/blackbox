"""Validation for workspace agent packages.

``prepare_agent_spec`` checks only what it needs to build run kwargs; this
module validates the whole package contract before it is saved, published,
or installed: provider/model presence, tool-ref resolvability,
permission/connector consistency, schedule sanity, and model availability
against the bundled provider model catalog.

Severity semantics:

- ``error`` — the package is internally inconsistent and will misbehave at
  run time (unknown refs, unparseable schedules, duplicates).
- ``warning`` — the package may still work, but something deserves a look
  (model not in the bundled catalog snapshot, calendar triggers the
  reference executor cannot fire, skill sources that do not exist locally).

``validate_workspace_agent`` returns every issue found;
``ensure_valid_workspace_agent`` raises on the first error-severity batch so
install/publish pipelines can gate on it.
"""
from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field as dataclass_field
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo

from blackbox.providers.catalog import bundled_provider_model_catalog
from blackbox.providers.model_catalog import ProviderModelCatalog
from blackbox.workspace_agents.executor import (
    ScheduleExpressionError,
    parse_cron,
    parse_interval,
)
from blackbox.workspace_agents.spec import WorkspaceAgentSpec

ValidationSeverity = Literal["error", "warning"]


@dataclass(slots=True, frozen=True)
class ValidationIssue:
    """One finding from package validation."""

    severity: ValidationSeverity
    code: str
    message: str
    # Named ``field`` for users ("which spec field is wrong"); it shadows
    # dataclasses.field inside the class body, hence the aliased import.
    field: str | None = None
    metadata: dict[str, Any] = dataclass_field(default_factory=dict)


class WorkspaceAgentValidationError(ValueError):
    """Raised by :func:`ensure_valid_workspace_agent` when errors are present."""

    def __init__(self, issues: list[ValidationIssue]) -> None:
        self.issues = issues
        errors = [issue for issue in issues if issue.severity == "error"]
        lines = "; ".join(f"[{issue.code}] {issue.message}" for issue in errors)
        super().__init__(f"Workspace agent spec has {len(errors)} validation error(s): {lines}")


def validate_workspace_agent(
    spec: WorkspaceAgentSpec,
    *,
    model_catalog: ProviderModelCatalog | None = None,
) -> list[ValidationIssue]:
    """Validate ``spec`` and return all issues, errors and warnings alike.

    ``model_catalog`` defaults to the bundled snapshot; pass your own to
    validate against a refreshed or restricted model set.
    """

    issues: list[ValidationIssue] = []
    _check_identity(spec, issues)
    _check_tool_refs(spec, issues)
    _check_connectors(spec, issues)
    _check_schedules(spec, issues)
    _check_skills(spec, issues)
    _check_model(spec, issues, model_catalog)
    return issues


def ensure_valid_workspace_agent(
    spec: WorkspaceAgentSpec,
    *,
    model_catalog: ProviderModelCatalog | None = None,
) -> list[ValidationIssue]:
    """Validate and raise :class:`WorkspaceAgentValidationError` on errors.

    Returns the full issue list (warnings included) when no errors exist.
    """

    issues = validate_workspace_agent(spec, model_catalog=model_catalog)
    if any(issue.severity == "error" for issue in issues):
        raise WorkspaceAgentValidationError(issues)
    return issues


# --- checks --------------------------------------------------------------------


def _check_identity(spec: WorkspaceAgentSpec, issues: list[ValidationIssue]) -> None:
    if not spec.name.strip():
        issues.append(
            ValidationIssue(
                severity="error",
                code="missing_name",
                message="Workspace agent name is empty.",
                field="name",
            )
        )
    if spec.model_provider is None and spec.agent_provider is None:
        issues.append(
            ValidationIssue(
                severity="error",
                code="missing_provider",
                message=(
                    "Neither model_provider nor agent_provider is set; "
                    "the package cannot be routed to a runtime."
                ),
                field="model_provider",
            )
        )


def _mcp_server_names(spec: WorkspaceAgentSpec) -> set[str]:
    names = {server.name for server in spec.mcp_servers}
    names.update(toolset.server.name for toolset in spec.mcp_toolsets)
    return names


def _hosted_tool_names(spec: WorkspaceAgentSpec) -> set[str]:
    names: set[str] = set()
    for tool in spec.hosted_tools:
        for attr in ("name", "type"):
            value = tool.get(attr) if isinstance(tool, dict) else getattr(tool, attr, None)
            if isinstance(value, str) and value:
                names.add(value)
    return names


def _check_tool_refs(spec: WorkspaceAgentSpec, issues: list[ValidationIssue]) -> None:
    local_tools = set(spec.tools)
    hosted = _hosted_tool_names(spec)
    mcp_servers = _mcp_server_names(spec)
    connector_names = {connector.name for connector in spec.connectors}
    connector_tool_refs = {
        ref for connector in spec.connectors for ref in connector.tool_refs
    }

    seen_refs: set[str] = set()
    for permission in spec.permissions:
        ref = permission.ref
        if ref in seen_refs:
            issues.append(
                ValidationIssue(
                    severity="error",
                    code="duplicate_permission_ref",
                    message=f"Permission declared more than once for {ref!r}.",
                    field="permissions",
                )
            )
        seen_refs.add(ref)

        if permission.connector is not None and permission.connector not in connector_names:
            issues.append(
                ValidationIssue(
                    severity="error",
                    code="unknown_connector",
                    message=(
                        f"Permission {ref!r} names connector {permission.connector!r}, "
                        "which the package does not declare."
                    ),
                    field="permissions",
                )
            )

        if ref.startswith("mcp:"):
            server = ref[len("mcp:") :].split(".", 1)[0]
            if server not in mcp_servers:
                issues.append(
                    ValidationIssue(
                        severity="error",
                        code="unknown_mcp_server",
                        message=(
                            f"Permission {ref!r} targets MCP server {server!r}, "
                            "which the package does not declare."
                        ),
                        field="permissions",
                    )
                )
            continue

        if permission.connector is not None:
            # Connector-backed tools resolve through the connector at run time.
            continue
        if ref not in local_tools and ref not in hosted and ref not in connector_tool_refs:
            issues.append(
                ValidationIssue(
                    severity="error",
                    code="unknown_tool_ref",
                    message=(
                        f"Permission {ref!r} matches no declared tool, hosted tool, "
                        "or connector tool ref."
                    ),
                    field="permissions",
                )
            )


def _check_connectors(spec: WorkspaceAgentSpec, issues: list[ValidationIssue]) -> None:
    seen: set[str] = set()
    known_tools = set(spec.tools) | _hosted_tool_names(spec)
    permission_refs = {permission.ref for permission in spec.permissions}
    for connector in spec.connectors:
        if connector.name in seen:
            issues.append(
                ValidationIssue(
                    severity="error",
                    code="duplicate_connector",
                    message=f"Connector {connector.name!r} is declared more than once.",
                    field="connectors",
                )
            )
        seen.add(connector.name)
        for ref in connector.tool_refs:
            if ref not in known_tools and ref not in permission_refs:
                issues.append(
                    ValidationIssue(
                        severity="warning",
                        code="connector_tool_ref_unknown",
                        message=(
                            f"Connector {connector.name!r} lists tool ref {ref!r}, "
                            "which matches no declared tool or permission."
                        ),
                        field="connectors",
                    )
                )


def _check_schedules(spec: WorkspaceAgentSpec, issues: list[ValidationIssue]) -> None:
    seen: set[str] = set()
    for schedule in spec.schedules:
        if schedule.name in seen:
            issues.append(
                ValidationIssue(
                    severity="error",
                    code="duplicate_schedule_name",
                    message=f"Schedule {schedule.name!r} is declared more than once.",
                    field="schedules",
                )
            )
        seen.add(schedule.name)

        trigger = schedule.trigger
        try:
            if trigger.kind == "cron":
                parse_cron(trigger.expression)
            elif trigger.kind == "interval":
                parse_interval(trigger.expression)
        except ScheduleExpressionError as exc:
            issues.append(
                ValidationIssue(
                    severity="error",
                    code="invalid_schedule_expression",
                    message=f"Schedule {schedule.name!r}: {exc}",
                    field="schedules",
                )
            )
        if trigger.kind == "calendar" and schedule.enabled:
            issues.append(
                ValidationIssue(
                    severity="warning",
                    code="unsupported_trigger",
                    message=(
                        f"Schedule {schedule.name!r} uses a calendar trigger, which the "
                        "reference executor cannot fire; a downstream scheduler must own it."
                    ),
                    field="schedules",
                )
            )
        if trigger.timezone is not None:
            try:
                ZoneInfo(trigger.timezone)
            except Exception:
                issues.append(
                    ValidationIssue(
                        severity="error",
                        code="invalid_timezone",
                        message=(
                            f"Schedule {schedule.name!r} declares unknown timezone "
                            f"{trigger.timezone!r}."
                        ),
                        field="schedules",
                    )
                )


def _check_skills(spec: WorkspaceAgentSpec, issues: list[ValidationIssue]) -> None:
    seen: set[str] = set()
    for skill in spec.skills:
        if skill.name in seen:
            issues.append(
                ValidationIssue(
                    severity="error",
                    code="duplicate_skill_name",
                    message=f"Skill bundle {skill.name!r} is declared more than once.",
                    field="skills",
                )
            )
        seen.add(skill.name)
        if skill.source and Path(skill.source).is_absolute() and not Path(skill.source).exists():
            issues.append(
                ValidationIssue(
                    severity="warning",
                    code="missing_skill_source",
                    message=(
                        f"Skill bundle {skill.name!r} points at {skill.source!r}, "
                        "which does not exist on this machine."
                    ),
                    field="skills",
                )
            )


def _check_model(
    spec: WorkspaceAgentSpec,
    issues: list[ValidationIssue],
    model_catalog: ProviderModelCatalog | None,
) -> None:
    if spec.model_provider is None or spec.model is None:
        return
    catalog = model_catalog if model_catalog is not None else bundled_provider_model_catalog()
    if catalog.get(provider=spec.model_provider, model=spec.model) is None:
        issues.append(
            ValidationIssue(
                severity="warning",
                code="unknown_model",
                message=(
                    f"Model {spec.model!r} on provider {spec.model_provider!r} is not in "
                    "the model catalog; the catalog is a snapshot, so verify the id "
                    "before publishing."
                ),
                field="model",
            )
        )
