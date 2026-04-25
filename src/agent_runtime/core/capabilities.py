from __future__ import annotations

from dataclasses import dataclass


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
