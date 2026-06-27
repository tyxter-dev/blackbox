from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from blackbox.core.errors import ConfigurationError
from blackbox.core.policy import Policy, PolicyDecision, PolicyRequest
from blackbox.mcp import MCPServerSpec, MCPToolset
from blackbox.planning.prompts import (
    FragmentRequirements,
    FragmentSelector,
    PromptFragment,
)
from blackbox.runtime.config import workflow_policy
from blackbox.skills.specs import SkillExpansion, SkillSpec
from blackbox.tools.hosted.specs import hosted_tool_kind
from blackbox.tools.registry import ToolRegistry
from blackbox.workspaces.spec import WorkspaceSpec

if TYPE_CHECKING:
    from blackbox.workspace_agents.permissions import PermissionScope, ToolPermission


def compile_skills(
    skills: Sequence[SkillSpec],
    *,
    registry: ToolRegistry,
    mcp_servers: Mapping[str, MCPServerSpec] | None = None,
) -> SkillExpansion:
    """Compile active skills into primitives already understood by runtime.run."""

    del registry  # Tool refs are validated by runtime exposure after all run toolsets register.
    names = [skill.name for skill in skills]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        raise ConfigurationError(f"Duplicate skill names: {', '.join(duplicates)}.")

    expansion = SkillExpansion()
    policies: list[Policy] = []
    for skill in sorted(skills, key=lambda item: item.name):
        context_flag = skill.context_flag or f"skill:{skill.name}"
        expansion.context_flags.append(context_flag)
        expansion.local_tools.extend(skill.tools)
        expansion.hosted_tools.extend(skill.hosted_tools)
        expansion.tool_permissions.extend(skill.permissions)
        if skill.workspace is not None:
            expansion.workspace = _merge_workspace_requirements(
                expansion.workspace,
                skill.workspace,
                skill_name=skill.name,
            )
        if skill.output is not None:
            if expansion.output_spec is not None and expansion.output_spec != skill.output:
                raise ConfigurationError("Multiple active skills declare conflicting output specs.")
            expansion.output_spec = skill.output
        expansion.mcp_toolsets.extend(
            _mcp_toolsets_for_skill(skill, mcp_servers=mcp_servers or {})
        )
        expansion.prompt_fragments.extend(_skill_fragments(skill, context_flag=context_flag))
        if skill.policy is not None:
            resolved = workflow_policy(skill.policy)
            if resolved is not None:
                policies.append(resolved)
        if skill.permissions:
            policies.append(SkillPermissionPolicy(skill.name, skill.permissions))

    expansion.local_tools = _dedupe(expansion.local_tools)
    expansion.hosted_tools = _dedupe_hosted(expansion.hosted_tools)
    expansion.context_flags = _dedupe(expansion.context_flags)
    expansion.mcp_toolsets = _dedupe_mcp_toolsets(expansion.mcp_toolsets)
    expansion.prompt_mode = "tool_aware" if skills else None
    expansion.policy = compose_policies(*policies)
    expansion.metadata["skills"] = names
    return expansion


@dataclass(slots=True)
class SkillPermissionPolicy:
    """Policy adapter for declarative skill `ToolPermission` entries."""

    skill_name: str
    permissions: Sequence[ToolPermission]

    async def check(self, request: PolicyRequest) -> PolicyDecision:
        if request.checkpoint == "before_tool_exposure":
            return PolicyDecision.allow()
        for permission in self.permissions:
            if not _permission_matches(permission, request):
                continue
            scopes = _request_scopes(request, permission)
            if any(permission.approval.requires_approval_for(scope) for scope in scopes):
                return PolicyDecision.require_approval(
                    permission.approval.reason
                    or f"Skill {self.skill_name!r} requires approval for {permission.ref!r}."
                )
        return PolicyDecision.allow()


@dataclass(slots=True)
class CompositePolicy:
    """Compose policies with deny, then approval, then allow precedence."""

    policies: Sequence[Policy]

    async def check(self, request: PolicyRequest) -> PolicyDecision:
        approval: PolicyDecision | None = None
        for policy in self.policies:
            decision = await policy.check(request)
            if decision.verdict == "deny":
                return decision
            if decision.verdict == "require_approval" and approval is None:
                approval = decision
        return approval or PolicyDecision.allow()


def compose_policies(*policies: Policy | None) -> Policy | None:
    active = [policy for policy in policies if policy is not None]
    if not active:
        return None
    if len(active) == 1:
        return active[0]
    return CompositePolicy(active)


def _skill_fragments(skill: SkillSpec, *, context_flag: str) -> list[PromptFragment]:
    fragments: list[PromptFragment] = []
    if skill.description:
        fragments.append(
            PromptFragment(
                id=f"skill.{skill.name}.summary",
                text=f"The {skill.name} skill is active for this run. {skill.description}",
                source="skill",
                priority=80,
                placement="system",
                conflict_group=f"skill:{skill.name}:summary",
                metadata={
                    "skill": skill.name,
                    "kind": "summary",
                    "examples": list(skill.examples),
                },
            )
        )
    if skill.instructions:
        selector = FragmentSelector(
            tools=frozenset(skill.tools),
            context_flags=frozenset({context_flag}),
            hosted_tool_kinds=frozenset(hosted_tool_kind(tool) for tool in skill.hosted_tools),
            mcp_servers=frozenset(
                server.name if isinstance(server, MCPServerSpec) else server
                for server in skill.mcp_servers
            ),
            workspace_kinds=(
                frozenset({skill.workspace.kind}) if skill.workspace is not None else frozenset()
            ),
        )
        fragments.append(
            PromptFragment(
                id=f"skill.{skill.name}.instructions",
                text=skill.instructions,
                source="skill",
                priority=70,
                applies_to=selector,
                requires=FragmentRequirements(required_tools=frozenset(skill.tools)),
                placement="tool_guidance",
                conflict_group=f"skill:{skill.name}:instructions",
                metadata={
                    "skill": skill.name,
                    "kind": "instructions",
                    "source": skill.source,
                    "version": skill.version,
                },
            )
        )
    return fragments


def _mcp_toolsets_for_skill(
    skill: SkillSpec,
    *,
    mcp_servers: Mapping[str, MCPServerSpec],
) -> list[MCPToolset]:
    toolsets: list[MCPToolset] = []
    for entry in skill.mcp_servers:
        if isinstance(entry, MCPServerSpec):
            toolsets.append(MCPToolset(server=entry))
            continue
        try:
            server = mcp_servers[entry]
        except KeyError as exc:
            raise ConfigurationError(
                f"Skill {skill.name!r} references unknown MCP server {entry!r}."
            ) from exc
        toolsets.append(MCPToolset(server=server))
    return toolsets


def _merge_workspace_requirements(
    current: WorkspaceSpec | None,
    required: WorkspaceSpec,
    *,
    skill_name: str,
) -> WorkspaceSpec:
    if current is None:
        return required
    if current == required:
        return current
    if current.kind != required.kind:
        raise ConfigurationError(
            f"Skill {skill_name!r} requires workspace kind {required.kind!r}, "
            f"but another skill requires {current.kind!r}."
        )
    merged = _merge_mapping_like(current, required)
    if merged is None:
        raise ConfigurationError(
            f"Skill {skill_name!r} declares a conflicting {required.kind!r} workspace."
        )
    return merged


def _merge_mapping_like(current: WorkspaceSpec, required: WorkspaceSpec) -> WorkspaceSpec | None:
    if current == required:
        return current
    current_data = {
        field.name: getattr(current, field.name)
        for field in current.__dataclass_fields__.values()
    }
    required_data = {
        field.name: getattr(required, field.name)
        for field in required.__dataclass_fields__.values()
    }
    merged: dict[str, Any] = {}
    for key, current_value in current_data.items():
        required_value = required_data[key]
        if _is_empty_value(current_value):
            merged[key] = required_value
            continue
        if _is_empty_value(required_value) or current_value == required_value:
            merged[key] = current_value
            continue
        return None
    return WorkspaceSpec(**merged)


def _permission_matches(permission: ToolPermission, request: PolicyRequest) -> bool:
    if request.action == permission.ref:
        return True
    tool_ref = request.metadata.get("tool_ref")
    if isinstance(tool_ref, str) and tool_ref == permission.ref:
        return True
    if permission.ref.startswith("mcp:") and request.checkpoint == "before_mcp_call":
        return request.action == permission.ref
    return False


def _request_scopes(
    request: PolicyRequest,
    permission: ToolPermission,
) -> list[PermissionScope]:
    raw_scopes = request.metadata.get("scopes")
    if isinstance(raw_scopes, list):
        scopes = [scope for scope in raw_scopes if isinstance(scope, str)]
        if scopes:
            return [
                cast("PermissionScope", scope)
                for scope in scopes
                if _is_permission_scope(scope)
            ]
    checkpoint_scope = _scope_for_checkpoint(request.checkpoint)
    if checkpoint_scope is not None:
        return [checkpoint_scope]
    return list(permission.scopes)


def _scope_for_checkpoint(checkpoint: str) -> PermissionScope | None:
    if checkpoint in {"before_workspace_write", "before_artifact_export"}:
        return "write"
    if checkpoint in {"before_command", "before_tool_call", "before_mcp_call"}:
        return "execute"
    if checkpoint in {"before_workspace_read", "before_tool_exposure"}:
        return "read"
    return None


def _is_permission_scope(value: str) -> bool:
    return value in {"read", "write", "delete", "execute", "admin", "custom"}


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _dedupe_hosted(values: list[Any]) -> list[Any]:
    seen: set[str] = set()
    result: list[Any] = []
    for value in values:
        key = repr(value)
        if key in seen:
            continue
        seen.add(key)
        result.append(value)
    return result


def _dedupe_mcp_toolsets(values: list[MCPToolset]) -> list[MCPToolset]:
    seen: set[tuple[str, str]] = set()
    result: list[MCPToolset] = []
    for toolset in values:
        key = (toolset.server.name, toolset.mode)
        if key in seen:
            continue
        seen.add(key)
        result.append(toolset)
    return result


def _is_empty_value(value: Any) -> bool:
    return value is None or value == {} or value == [] or value == ()
