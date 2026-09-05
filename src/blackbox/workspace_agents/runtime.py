from __future__ import annotations

from typing import Any, TypeVar, cast

from blackbox.core.errors import ConfigurationError, UnsupportedFeatureError
from blackbox.core.results import AgentResult, AgentSessionResult, OutputSpec
from blackbox.core.tool_permissions import active_permissions, permission_boundary
from blackbox.providers.base import AgentSpec
from blackbox.providers.registry import ProviderRef
from blackbox.skills.specs import normalize_skills
from blackbox.skills.staging import ClaudeCodeSkillStager, ensure_project_setting_source
from blackbox.workspace_agents.permissions import compile_package_permissions
from blackbox.workspace_agents.spec import WorkspaceAgentSpec
from blackbox.workspaces.spec import WorkspaceRef, WorkspaceSpec

T = TypeVar("T")


def prepare_agent_spec(spec: WorkspaceAgentSpec) -> dict[str, Any]:
    """Prepare inherited packages; restricted packages require run_workspace_agent."""
    if spec.permission_mode != "inherit":
        raise ConfigurationError(
            "Use run_workspace_agent for allowlist_v1; plain kwargs cannot carry its boundary."
        )
    return _prepare_agent_spec(spec)


def _prepare_agent_spec(spec: WorkspaceAgentSpec) -> dict[str, Any]:
    """Return keyword arguments suitable for ``AgentRuntime.run``."""

    if spec.model_provider is None:
        raise ValueError("WorkspaceAgentSpec.model_provider is required for model runtime runs.")
    return {
        "provider": spec.model_provider,
        "model": spec.model,
        "tools": list(spec.tools),
        "hosted_tools": list(spec.hosted_tools),
        "toolsets": spec.resolved_mcp_toolsets(),
        "skills": list(spec.skills),
    }


async def run_workspace_agent(
    runtime: Any,
    spec: WorkspaceAgentSpec,
    *,
    input: str,
    output_type: type[T] | None = None,
    output_spec: OutputSpec | None = None,
    **kwargs: Any,
) -> AgentResult[T] | AgentSessionResult[T]:
    """Run a package with an immutable, invocation-scoped permission boundary."""
    permissions = active_permissions()
    if spec.permission_mode == "allowlist_v1":
        permissions = (*permissions, compile_package_permissions(spec.permissions, spec.connectors))
    if permissions and spec.agent_provider is not None:
        adapter = runtime.agents.registry.get_agent(
            ProviderRef.parse(spec.agent_provider).provider_key
        )
        if not adapter.capabilities().supports_package_permissions:
            raise UnsupportedFeatureError(
                "Agent provider cannot enforce allowlist_v1 package permissions."
            )
        if spec.agent_id is not None:
            raise UnsupportedFeatureError("allowlist_v1 requires a new package-backed local agent.")
        if spec.resolved_mcp_toolsets() and not adapter.capabilities().supports_mcp:
            raise UnsupportedFeatureError(
                "This agent provider cannot materialize package MCP toolsets; use a model run."
            )
        if kwargs.get("workspace") is not None and not adapter.capabilities().supports_workspace:
            raise UnsupportedFeatureError(
                "This agent provider cannot expose package workspace tools; use a model run."
            )
        from blackbox.providers.agent_adapters.local import LocalAgentProvider
        from blackbox.tools.hosted.specs import WebSearch

        if isinstance(adapter, LocalAgentProvider) and any(
            not isinstance(tool, WebSearch) for tool in spec.hosted_tools
        ):
            raise UnsupportedFeatureError(
                "Local sessions have no client-hosted handler configuration; use a model run."
            )
    with permission_boundary(permissions):
        return await _run_workspace_agent(
            runtime, spec, input=input, output_type=output_type, output_spec=output_spec, **kwargs
        )


async def _run_workspace_agent(
    runtime: Any,
    spec: WorkspaceAgentSpec,
    *,
    input: str,
    output_type: type[T] | None = None,
    output_spec: OutputSpec | None = None,
    **kwargs: Any,
) -> AgentResult[T] | AgentSessionResult[T]:
    """Run a packaged workspace agent through the existing high-level loop."""

    if spec.agent_provider is not None:
        result = await _run_agent_provider_workspace_agent(
            runtime,
            spec,
            input=input,
            output_type=output_type,
            output_spec=output_spec,
            **kwargs,
        )
        return result

    run_kwargs = _prepare_agent_spec(spec)
    run_kwargs.update(kwargs)
    result = await runtime.run(
        input=input,
        output_type=output_type,
        output_spec=output_spec,
        **run_kwargs,
    )
    return cast(AgentResult[T], result)


async def _run_agent_provider_workspace_agent(
    runtime: Any,
    spec: WorkspaceAgentSpec,
    *,
    input: str,
    output_type: type[T] | None,
    output_spec: OutputSpec | None,
    **kwargs: Any,
) -> AgentSessionResult[T]:
    provider = spec.agent_provider
    if provider is None:
        raise ConfigurationError("WorkspaceAgentSpec.agent_provider is required.")
    skills = normalize_skills(spec.skills)
    workspace = kwargs.pop("workspace", None)
    workspace_provider = kwargs.pop("workspace_provider", None)
    workspace_policy = kwargs.pop("workspace_policy", None)
    workspace_preserve = bool(kwargs.pop("workspace_preserve", False))
    extra = dict(spec.extra)
    extra.update(dict(kwargs.pop("extra", {}) or {}))
    provider_key = ProviderRef.parse(provider).provider_key

    resolved_workspace: tuple[Any, Any, bool] | None = None
    if provider_key == "claude-code" and skills:
        workspace = _workspace_with_skill_requirement(workspace, skills)
        if workspace is None:
            raise ConfigurationError("Claude Code skill staging requires a workspace.")
        if not hasattr(runtime, "workspaces"):
            raise ConfigurationError("Runtime does not expose a workspace facade.")
        workspace_ref, provider_obj, opened = await runtime.workspaces.resolve(
            workspace,
            provider=workspace_provider,
            policy=None,
        )
        if workspace_ref is None or provider_obj is None:
            raise ConfigurationError("workspace could not be resolved for skill staging.")
        await ClaudeCodeSkillStager().prepare(
            skills,
            workspace=workspace_ref,
            provider=provider_obj,
        )
        resolved_workspace = (workspace_ref, provider_obj, opened)
        workspace = workspace_ref
        ensure_project_setting_source(extra)
    elif skills:
        raise UnsupportedFeatureError(
            f"Agent provider {provider_key!r} does not support portable skill packs yet."
        )

    try:
        result = await runtime.agents.run(
            provider=provider,
            agent=spec.agent_id or _agent_spec_for_provider(spec),
            task=input,
            model=spec.model,
            workspace=workspace,
            workspace_provider=workspace_provider,
            workspace_policy=workspace_policy,
            workspace_preserve=workspace_preserve or resolved_workspace is not None,
            hosted_tools=list(spec.hosted_tools),
            extra=extra,
            output_type=output_type,
            output_spec=output_spec,
            **kwargs,
        )
        return cast(AgentSessionResult[T], result)
    finally:
        if resolved_workspace is not None and not workspace_preserve:
            workspace_ref, provider_obj, opened = resolved_workspace
            if opened:
                await provider_obj.close(workspace_ref)


def _agent_spec_for_provider(spec: WorkspaceAgentSpec) -> AgentSpec:
    return spec._to_agent_spec()


def _workspace_with_skill_requirement(
    workspace: Any | None,
    skills: list[Any],
) -> Any | None:
    required: WorkspaceSpec | None = None
    for skill in skills:
        if skill.workspace is None:
            continue
        if required is not None and required.kind != skill.workspace.kind:
            raise ConfigurationError("Active skills require conflicting workspace kinds.")
        required = skill.workspace
    if workspace is None:
        return required
    if required is None:
        return workspace
    kind = workspace.kind if isinstance(workspace, WorkspaceSpec | WorkspaceRef) else None
    if isinstance(kind, str) and kind != required.kind:
        raise ConfigurationError(
            f"Skill requires workspace kind {required.kind!r}, but run workspace kind is {kind!r}."
        )
    return workspace
