"""Public SDK entrypoint for ``agent_runtime``.

Coding agents should treat this module as the first integration map for the
library. The top-level exports below are the stable public API; implementation
details live under the matching subpackages.

Start paths:
  * High-level task loop: ``AgentRuntime.run(...)``. Minimal offline example:
    ``examples/minimal_runtime_run.py``.
  * Direct model turn: ``runtime.models.run(...)``. Minimal offline example:
    ``examples/minimal_model_turn.py``.
  * Managed agent session: ``runtime.agents.run(...)``. Minimal offline
    example: ``examples/minimal_local_agent.py``.
  * Workspace/coding-agent files: ``runtime.workspaces`` and
    ``WorkspaceSpec``. Minimal offline example: ``examples/minimal_workspace.py``.
  * Packaged workspace agents: ``WorkspaceAgentSpec`` and
    ``run_workspace_agent(...)``. See ``src/agent_runtime/workspace_agents/README.md``.
  * Hosted tools and MCP: ``WebSearch``, ``FileSearch``, ``RemoteMCP``,
    and ``MCPToolset``. See ``src/agent_runtime/tools/hosted/README.md`` and
    ``src/agent_runtime/mcp/README.md``.

Import guidance:
  * Prefer ``from agent_runtime import AgentRuntime, WebSearch, FileSearch``
    for public contracts.
  * Import concrete providers from ``agent_runtime.providers.model_adapters``
    and ``agent_runtime.providers.agent_adapters``.
  * Compatibility namespaces under ``agent_runtime.models`` and
    ``agent_runtime.agents`` exist for older imports.
"""

from importlib import import_module
from typing import Any

from agent_runtime.compat.chat import ChatMessage
from agent_runtime.compat.providers import (
    AgentProviderAlias,
    ModelRoute,
    create_runtime_with_default_providers,
    provider_ref_for_model,
    register_default_agent_providers,
    register_default_model_providers,
    resolve_model_route,
)
from agent_runtime.core.accounting import ModelCatalog, ModelPricing, ModelUsage
from agent_runtime.core.approvals import ApprovalDecision, ApprovalRequest
from agent_runtime.core.artifacts import Artifact, ArtifactRef
from agent_runtime.core.cache import (
    InMemoryProviderCacheStore,
    ProviderCacheRecord,
    ProviderCacheStore,
    ProviderCacheUsage,
    SQLiteProviderCacheStore,
)
from agent_runtime.core.capabilities import (
    AgentCapabilities,
    CapabilityConstraint,
    CapabilityDetail,
    HostedToolSupport,
    ModelCapabilities,
    ModelCapabilityProfile,
)
from agent_runtime.core.content import (
    AudioPart,
    ContentItem,
    ContentPart,
    FilePart,
    ImagePart,
    ProviderNativePart,
    TextPart,
    ToolResultPart,
    VideoFramePart,
)
from agent_runtime.core.errors import OutputValidationError
from agent_runtime.core.events import AgentEvent, EventTypes
from agent_runtime.core.items import ItemTypes, RunItem
from agent_runtime.core.media import MediaRef
from agent_runtime.core.realtime import ToolMode, TransportKind
from agent_runtime.core.results import (
    AgentMessage,
    AgentResponseMode,
    AgentResponseSpec,
    AgentResult,
    AgentSessionResult,
    AgentSessionResultStatus,
    OutputSpec,
    ToolPayload,
    agent_response_messages,
    conversational_response,
    result_summary,
    structured_output,
)
from agent_runtime.core.sessions import AgentRef, AgentSession, SessionRef
from agent_runtime.core.state import ProviderState, RunState
from agent_runtime.mcp import (
    MCPApprovalMode,
    MCPCapabilityRisk,
    MCPRouteMode,
    MCPServerRiskProfile,
    MCPServerSpec,
    MCPServerTrustPolicy,
    MCPTaint,
    MCPToolset,
    MCPToolTrustPolicy,
    MCPTrustDecision,
    MCPTrustLevel,
    trust_fingerprint,
)
from agent_runtime.output.schema import OutputSchema
from agent_runtime.planning.prompts import (
    CacheSection,
    FragmentRequirements,
    FragmentSelector,
    PromptBundle,
    PromptComposer,
    PromptFragment,
    PromptFragmentRegistry,
    PromptParityIssue,
    PromptSection,
    PromptSpec,
    SelectedPromptFragment,
    SkippedPromptFragment,
    assert_prompt_tool_parity,
)
from agent_runtime.planning.run_plan import (
    DataSourceRef,
    DynamicToolLoadingSpec,
    ResolvedHostedTool,
    ResolvedMCPToolset,
    ResolvedRunSpec,
    ResolvedTool,
)
from agent_runtime.providers.base import (
    AgentProvider,
    AgentSpec,
    CompactionControl,
    ModelCacheControl,
    ModelProvider,
    ModelRequestControls,
    TaskSpec,
    ToolSearchControl,
    TurnRequest,
    TurnResult,
)
from agent_runtime.providers.registry import ProviderRef, ProviderRegistry
from agent_runtime.realtime import (
    AudioConfig,
    FakeRealtimeProvider,
    ManagedRealtimeSession,
    RealtimeCapabilities,
    RealtimeCapabilityProfile,
    RealtimeClientCommand,
    RealtimeConnectRequest,
    RealtimeProvider,
    RealtimeRuntime,
    RealtimeSessionConfig,
    RealtimeSessionRef,
    TurnDetectionConfig,
    register_default_realtime_providers,
)
from agent_runtime.runtime import (
    AgentRuntime,
    AgentRuntimeFacade,
    ChatRuntimeFacade,
    ModelRuntime,
    PromptRuntimeFacade,
    ProviderCacheRuntime,
    WorkspaceRuntimeFacade,
)
from agent_runtime.tools.hosted.specs import (
    ApplyPatch,
    CodeInterpreter,
    ComputerUse,
    ContainerSpec,
    FileSearch,
    HostedToolHandlers,
    HostedToolRaw,
    ImageGeneration,
    Memory,
    RemoteMCP,
    Shell,
    TextEditor,
    ToolNamespace,
    ToolSearch,
    URLContext,
    WebFetch,
    WebSearch,
)
from agent_runtime.tools.routing import ResolvedToolPlan, ToolCandidate, ToolRoutingSpec
from agent_runtime.tools.session import ToolSession
from agent_runtime.tools.toolsets import ToolBudget, ToolSelection, Toolset
from agent_runtime.workspace_agents import (
    ApprovalRequirement,
    ConnectorSpec,
    InMemoryWorkspaceAgentRegistry,
    MemorySpec,
    PublicationSpec,
    ScheduledRunRef,
    ScheduleSpec,
    ScheduleTrigger,
    SkillBundleRef,
    ToolPermission,
    WorkspaceAgentMetadata,
    WorkspaceAgentRegistry,
    WorkspaceAgentSpec,
    WorkspaceAgentVersion,
    dataclass_from_dict,
    dataclass_to_dict,
    prepare_agent_spec,
    run_workspace_agent,
    workspace_agent_from_dict,
    workspace_agent_to_dict,
)

__all__ = [
    "AgentCapabilities",
    "AgentEvent",
    "AgentMessage",
    "AgentProvider",
    "AgentProviderAlias",
    "AgentRef",
    "AgentResponseMode",
    "AgentResponseSpec",
    "AgentResult",
    "AgentRuntime",
    "AgentRuntimeFacade",
    "AgentSession",
    "AgentSessionResult",
    "AgentSessionResultStatus",
    "AgentSpec",
    "ApplyPatch",
    "ApprovalDecision",
    "ApprovalRequest",
    "ApprovalRequirement",
    "Artifact",
    "ArtifactRef",
    "AudioConfig",
    "AudioPart",
    "CacheSection",
    "CapabilityConstraint",
    "CapabilityDetail",
    "ChatMessage",
    "ChatRuntimeFacade",
    "CodeInterpreter",
    "CompactionControl",
    "ComputerUse",
    "ConnectorSpec",
    "ContainerSpec",
    "ContentItem",
    "ContentPart",
    "DataSourceRef",
    "DynamicToolLoadingSpec",
    "EventTypes",
    "FakeRealtimeProvider",
    "FilePart",
    "FileSearch",
    "FragmentRequirements",
    "FragmentSelector",
    "GoogleBigQueryMCPAuth",
    "HostedToolHandlers",
    "HostedToolRaw",
    "HostedToolSupport",
    "ImageGeneration",
    "ImagePart",
    "InMemoryProviderCacheStore",
    "InMemoryWorkspaceAgentRegistry",
    "ItemTypes",
    "MCPApprovalMode",
    "MCPCapabilityRisk",
    "MCPRouteMode",
    "MCPServerRiskProfile",
    "MCPServerSpec",
    "MCPServerTrustPolicy",
    "MCPTaint",
    "MCPToolTrustPolicy",
    "MCPToolset",
    "MCPTrustDecision",
    "MCPTrustLevel",
    "ManagedRealtimeSession",
    "MediaRef",
    "Memory",
    "MemorySpec",
    "ModelCacheControl",
    "ModelCapabilities",
    "ModelCapabilityProfile",
    "ModelCatalog",
    "ModelPricing",
    "ModelProvider",
    "ModelRequestControls",
    "ModelRoute",
    "ModelRuntime",
    "ModelUsage",
    "OpenAIVectorStoreDocument",
    "OpenAIVectorStoreHandle",
    "OutputSchema",
    "OutputSpec",
    "OutputValidationError",
    "PromptBundle",
    "PromptComposer",
    "PromptFragment",
    "PromptFragmentRegistry",
    "PromptParityIssue",
    "PromptRuntimeFacade",
    "PromptSection",
    "PromptSpec",
    "ProviderCacheRecord",
    "ProviderCacheRuntime",
    "ProviderCacheStore",
    "ProviderCacheUsage",
    "ProviderNativePart",
    "ProviderRef",
    "ProviderRegistry",
    "ProviderState",
    "PublicationSpec",
    "RealtimeCapabilities",
    "RealtimeCapabilityProfile",
    "RealtimeClientCommand",
    "RealtimeConnectRequest",
    "RealtimeProvider",
    "RealtimeRuntime",
    "RealtimeSessionConfig",
    "RealtimeSessionRef",
    "RemoteMCP",
    "ResolvedHostedTool",
    "ResolvedMCPToolset",
    "ResolvedRunSpec",
    "ResolvedTool",
    "ResolvedToolPlan",
    "RunItem",
    "RunState",
    "SQLiteProviderCacheStore",
    "ScheduleSpec",
    "ScheduleTrigger",
    "ScheduledRunRef",
    "SelectedPromptFragment",
    "SessionRef",
    "Shell",
    "SkillBundleRef",
    "SkippedPromptFragment",
    "TaskSpec",
    "TextEditor",
    "TextPart",
    "ToolBudget",
    "ToolCandidate",
    "ToolMode",
    "ToolNamespace",
    "ToolPayload",
    "ToolPermission",
    "ToolResultPart",
    "ToolRoutingSpec",
    "ToolSearch",
    "ToolSearchControl",
    "ToolSelection",
    "ToolSession",
    "Toolset",
    "TransportKind",
    "TurnDetectionConfig",
    "TurnRequest",
    "TurnResult",
    "URLContext",
    "VideoFramePart",
    "WebFetch",
    "WebSearch",
    "WorkspaceAgentMetadata",
    "WorkspaceAgentRegistry",
    "WorkspaceAgentSpec",
    "WorkspaceAgentVersion",
    "WorkspaceRuntimeFacade",
    "agent_response_messages",
    "assert_prompt_tool_parity",
    "conversational_response",
    "create_openai_vector_store",
    "create_runtime_with_default_providers",
    "dataclass_from_dict",
    "dataclass_to_dict",
    "google_application_default_bigquery_auth",
    "google_bigquery_mcp_toolset",
    "google_maps_mcp_toolset",
    "prepare_agent_spec",
    "provider_ref_for_model",
    "register_default_agent_providers",
    "register_default_model_providers",
    "register_default_realtime_providers",
    "resolve_model_route",
    "result_summary",
    "run_workspace_agent",
    "structured_output",
    "temporary_openai_file_search",
    "temporary_openai_vector_store",
    "trust_fingerprint",
    "workspace_agent_from_dict",
    "workspace_agent_to_dict",
]

_LAZY_EXPORTS: dict[str, tuple[str, str]] = {
    "GoogleBigQueryMCPAuth": (
        "agent_runtime.integrations",
        "GoogleBigQueryMCPAuth",
    ),
    "google_application_default_bigquery_auth": (
        "agent_runtime.integrations",
        "google_application_default_bigquery_auth",
    ),
    "google_bigquery_mcp_toolset": (
        "agent_runtime.integrations",
        "google_bigquery_mcp_toolset",
    ),
    "google_maps_mcp_toolset": (
        "agent_runtime.integrations",
        "google_maps_mcp_toolset",
    ),
    "OpenAIVectorStoreDocument": (
        "agent_runtime.integrations",
        "OpenAIVectorStoreDocument",
    ),
    "OpenAIVectorStoreHandle": (
        "agent_runtime.integrations",
        "OpenAIVectorStoreHandle",
    ),
    "create_openai_vector_store": (
        "agent_runtime.integrations",
        "create_openai_vector_store",
    ),
    "temporary_openai_file_search": (
        "agent_runtime.integrations",
        "temporary_openai_file_search",
    ),
    "temporary_openai_vector_store": (
        "agent_runtime.integrations",
        "temporary_openai_vector_store",
    ),
}


def __getattr__(name: str) -> Any:
    try:
        module_name, attr_name = _LAZY_EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc
    value = getattr(import_module(module_name), attr_name)
    globals()[name] = value
    return value
