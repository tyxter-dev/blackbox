from __future__ import annotations

from dataclasses import dataclass, field

from agent_runtime.core.errors import UnsupportedFeatureError
from agent_runtime.hosted_tools import (
    ApplyPatch,
    CodeInterpreter,
    ComputerUse,
    FileSearch,
    HostedToolRaw,
    HostedToolSpec,
    ImageGeneration,
    RemoteMCP,
    Shell,
    ToolSearch,
    URLContext,
    WebFetch,
    WebSearch,
)


@dataclass(slots=True, frozen=True)
class HostedToolSupport:
    tool_type: str
    request_mapping: bool
    event_mapping: bool
    provider_server_execution: bool
    runtime_client_execution: bool
    requires_handler: bool = False
    requires_approval_default: bool = False
    models: list[str] = field(default_factory=list)
    notes: str | None = None


@dataclass(slots=True, frozen=True)
class ModelCapabilities:
    """Feature flags for a model-level provider.

    ``supports_structured_output`` means the adapter accepts and enforces a
    provider-native schema contract. Runtime-level posthoc validation does not
    set this flag.
    """

    supports_streaming_events: bool = True
    supports_function_tools: bool = False
    supports_parallel_tool_calls: bool = False
    supports_hosted_tools: bool = False
    supports_tool_search: bool = False
    supports_client_executed_tool_search: bool = False
    supports_remote_mcp: bool = False
    supports_reasoning_items: bool = False
    supports_provider_state: bool = False
    supports_structured_output: bool = False
    hosted_tools: dict[str, HostedToolSupport] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class AgentCapabilities:
    """Feature flags for a cloud/local agent provider."""

    supports_sessions: bool = True
    supports_streaming_events: bool = True
    supports_artifacts: bool = False
    supports_workspace: bool = False
    supports_approvals: bool = False
    supports_mcp: bool = False
    supports_deployments: bool = False
    supports_evals: bool = False
    supports_cancellation: bool = False
    supports_resume: bool = False


def hosted_tool_type(spec: HostedToolSpec) -> str:
    if isinstance(spec, WebSearch):
        return "web_search"
    if isinstance(spec, WebFetch):
        return "web_fetch"
    if isinstance(spec, URLContext):
        return "url_context"
    if isinstance(spec, FileSearch):
        return "file_search"
    if isinstance(spec, CodeInterpreter):
        return "code_interpreter"
    if isinstance(spec, Shell):
        return "shell"
    if isinstance(spec, ApplyPatch):
        return "apply_patch"
    if isinstance(spec, ComputerUse):
        return "computer"
    if isinstance(spec, ImageGeneration):
        return "image_generation"
    if isinstance(spec, ToolSearch):
        return "tool_search"
    if isinstance(spec, RemoteMCP):
        return "mcp"
    if isinstance(spec, HostedToolRaw):
        return "raw"
    return type(spec).__name__


def validate_hosted_tools(
    capabilities: ModelCapabilities,
    specs: list[HostedToolSpec],
    *,
    provider: str,
) -> None:
    if not specs:
        return
    if not capabilities.supports_hosted_tools:
        raise UnsupportedFeatureError(f"Provider '{provider}' does not support hosted tools.")
    for spec in specs:
        if isinstance(spec, HostedToolRaw):
            continue
        tool_type = hosted_tool_type(spec)
        if tool_type not in capabilities.hosted_tools:
            raise UnsupportedFeatureError(
                f"Provider '{provider}' does not support hosted tool '{tool_type}'."
            )
