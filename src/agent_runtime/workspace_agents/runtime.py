from __future__ import annotations

from typing import Any, TypeVar, cast

from agent_runtime.core.results import AgentResult, OutputSpec
from agent_runtime.workspace_agents.spec import WorkspaceAgentSpec

T = TypeVar("T")


def prepare_agent_spec(spec: WorkspaceAgentSpec) -> dict[str, Any]:
    """Return keyword arguments suitable for ``AgentRuntime.run``."""

    if spec.model_provider is None:
        raise ValueError("WorkspaceAgentSpec.model_provider is required for model runtime runs.")
    return {
        "provider": spec.model_provider,
        "model": spec.model,
        "tools": list(spec.tools),
        "hosted_tools": list(spec.hosted_tools),
        "toolsets": spec.resolved_mcp_toolsets(),
    }


async def run_workspace_agent(
    runtime: Any,
    spec: WorkspaceAgentSpec,
    *,
    input: str,
    output_type: type[T] | None = None,
    output_spec: OutputSpec | None = None,
    **kwargs: Any,
) -> AgentResult[T]:
    """Run a packaged workspace agent through the existing high-level loop."""

    run_kwargs = prepare_agent_spec(spec)
    run_kwargs.update(kwargs)
    result = await runtime.run(
        input=input,
        output_type=output_type,
        output_spec=output_spec,
        **run_kwargs,
    )
    return cast(AgentResult[T], result)
