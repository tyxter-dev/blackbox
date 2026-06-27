from __future__ import annotations

from collections.abc import Iterable

from blackbox.skills.specs import SkillSpec
from blackbox.tools.registry import ToolRegistry
from blackbox.workspace_agents.validation import ValidationIssue


def validate_skill_spec(
    skill: SkillSpec,
    *,
    registry: ToolRegistry | None = None,
    known_permission_refs: Iterable[str] = (),
) -> list[ValidationIssue]:
    """Return typed validation issues for a loaded skill spec."""

    issues: list[ValidationIssue] = []
    if not skill.name.strip():
        issues.append(
            ValidationIssue(
                severity="error",
                code="missing_skill_name",
                message="Skill name is required.",
                field="skills",
            )
        )
    if skill.source is not None and not skill.instructions:
        issues.append(
            ValidationIssue(
                severity="warning",
                code="empty_skill_instructions",
                message=f"Skill '{skill.name}' has no instruction body.",
                field="skills",
            )
        )
    if registry is not None:
        known_tools = {tool.name for tool in registry.all_tools()}
        for tool in skill.tools:
            if tool not in known_tools:
                issues.append(
                    ValidationIssue(
                        severity="warning",
                        code="unresolved_skill_tool",
                        message=f"Skill '{skill.name}' references unresolved tool '{tool}'.",
                        field="skills",
                    )
                )
    known_refs = set(known_permission_refs)
    for permission in skill.permissions:
        if known_refs and permission.ref not in known_refs and permission.ref not in skill.tools:
            issues.append(
                ValidationIssue(
                    severity="warning",
                    code="unresolved_skill_permission_ref",
                    message=(
                        f"Skill '{skill.name}' permission references unknown ref "
                        f"'{permission.ref}'."
                    ),
                    field="skills",
                )
            )
    return issues
