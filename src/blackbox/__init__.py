"""Public SDK entrypoint for ``blackbox``.

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
    ``run_workspace_agent(...)``. See ``src/blackbox/workspace_agents/README.md``.
  * Hosted tools and MCP: ``WebSearch``, ``FileSearch``, ``RemoteMCP``,
    and ``MCPToolset``. See ``src/blackbox/tools/hosted/README.md`` and
    ``src/blackbox/mcp/README.md``.

Import guidance:
  * Prefer ``from blackbox import AgentRuntime, WebSearch, FileSearch``
    for public contracts.
  * Import concrete providers from ``blackbox.providers.model_adapters``
    and ``blackbox.providers.agent_adapters``.
  * Compatibility namespaces under ``blackbox.models`` and
    ``blackbox.agents`` exist for older imports.
"""

from importlib import import_module
from typing import Any

from blackbox.compat.chat import ChatMessage
from blackbox.compat.providers import (
    AgentProviderAlias,
    ModelRoute,
    create_runtime_with_default_providers,
    provider_ref_for_model,
    register_default_agent_providers,
    register_default_model_providers,
    resolve_model_route,
)
from blackbox.core.accounting import MarkupPolicy, ModelCatalog, ModelPricing, ModelUsage
from blackbox.core.approvals import ApprovalDecision, ApprovalRequest
from blackbox.core.artifacts import Artifact, ArtifactRef
from blackbox.core.cache import (
    InMemoryProviderCacheStore,
    ProviderCacheRecord,
    ProviderCacheStore,
    ProviderCacheUsage,
    SQLiteProviderCacheStore,
)
from blackbox.core.capabilities import (
    AgentCapabilities,
    CapabilityConstraint,
    CapabilityDetail,
    HostedToolSupport,
    ModelCapabilities,
    ModelCapabilityProfile,
)
from blackbox.core.content import (
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
from blackbox.core.errors import (
    AgentWebhookError,
    AgentWebhookVerificationError,
    OutputValidationError,
    SessionBusyError,
    SessionCursorError,
    SessionNotFoundError,
    SessionResumeError,
    SessionTerminalError,
)
from blackbox.core.events import AgentEvent, EventTypes
from blackbox.core.items import ItemTypes, RunItem
from blackbox.core.media import MediaRef
from blackbox.core.realtime import ToolMode, TransportKind
from blackbox.core.results import (
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
from blackbox.core.session_state import (
    AgentSessionState,
    PendingApprovalState,
    SessionEventCursor,
    SessionInvocationState,
)
from blackbox.core.sessions import AgentRef, AgentSession, SessionRef
from blackbox.core.state import ProviderState, RunState
from blackbox.core.stores import (
    InMemorySessionStore,
    JSONLSessionStore,
    SessionStore,
    SQLiteSessionStore,
)
from blackbox.mcp import (
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
from blackbox.observability import ObservabilityPreset
from blackbox.output.schema import OutputSchema
from blackbox.planning.prompts import (
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
    SectionStyle,
    SelectedPromptFragment,
    SkippedPromptFragment,
    assert_prompt_tool_parity,
)
from blackbox.planning.run_plan import (
    DataSourceRef,
    DynamicToolLoadingSpec,
    ResolvedHostedTool,
    ResolvedMCPToolset,
    ResolvedRunSpec,
    ResolvedTool,
)
from blackbox.pricing import (
    BUNDLED_PRICING_CATALOG_VERSION,
    bundled_model_catalog,
    bundled_provider_pricing,
)
from blackbox.providers.base import (
    AgentProvider,
    AgentSpec,
    AgentWebhookDelivery,
    AgentWebhookIngestResult,
    AgentWebhookIngressStatus,
    AgentWebhookProvider,
    CompactionControl,
    ModelCacheControl,
    ModelProvider,
    ModelRequestControls,
    TaskSpec,
    ToolSearchControl,
    TurnRequest,
    TurnResult,
)
from blackbox.providers.catalog import (
    BUNDLED_PROVIDER_MODEL_CATALOG_VERSION,
    bundled_provider_model_catalog,
    bundled_provider_models,
)
from blackbox.providers.model_catalog import ModelLifecycle, ProviderModel, ProviderModelCatalog
from blackbox.providers.registry import ProviderRef, ProviderRegistry
from blackbox.realtime import (
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
from blackbox.runtime import (
    AgentRuntime,
    AgentRuntimeFacade,
    ChatRuntimeFacade,
    ModelRuntime,
    PromptRuntimeFacade,
    ProviderCacheRuntime,
    RequiredConfigValue,
    RiskyActionApprovalPolicy,
    RuntimeConfig,
    WorkflowProfile,
    WorkspaceRuntimeFacade,
    get_workflow_profile,
    workflow_profile_docs,
    workflow_profiles,
)
from blackbox.tools.hosted.specs import (
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
from blackbox.tools.routing import ResolvedToolPlan, ToolCandidate, ToolRoutingSpec
from blackbox.tools.session import ToolSession
from blackbox.tools.toolsets import ToolBudget, ToolSelection, Toolset
from blackbox.workspace_agents import (
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
from blackbox.workspaces import WorkspaceSpec

__all__ = [
    "BUNDLED_PRICING_CATALOG_VERSION",
    "BUNDLED_PROVIDER_MODEL_CATALOG_VERSION",
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
    "AgentSessionState",
    "AgentSpec",
    "AgentWebhookDelivery",
    "AgentWebhookError",
    "AgentWebhookIngestResult",
    "AgentWebhookIngressStatus",
    "AgentWebhookProvider",
    "AgentWebhookVerificationError",
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
    "InMemorySessionStore",
    "InMemoryWorkspaceAgentRegistry",
    "ItemTypes",
    "JSONLSessionStore",
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
    "MarkupPolicy",
    "MediaRef",
    "Memory",
    "MemorySpec",
    "ModelCacheControl",
    "ModelCapabilities",
    "ModelCapabilityProfile",
    "ModelCatalog",
    "ModelLifecycle",
    "ModelPricing",
    "ModelProvider",
    "ModelRequestControls",
    "ModelRoute",
    "ModelRuntime",
    "ModelUsage",
    "ObservabilityPreset",
    "OpenAIVectorStoreDocument",
    "OpenAIVectorStoreHandle",
    "OutputSchema",
    "OutputSpec",
    "OutputValidationError",
    "PendingApprovalState",
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
    "ProviderModel",
    "ProviderModelCatalog",
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
    "RequiredConfigValue",
    "ResolvedHostedTool",
    "ResolvedMCPToolset",
    "ResolvedRunSpec",
    "ResolvedTool",
    "ResolvedToolPlan",
    "RiskyActionApprovalPolicy",
    "RunItem",
    "RunState",
    "RuntimeConfig",
    "SQLiteProviderCacheStore",
    "SQLiteSessionStore",
    "ScheduleSpec",
    "ScheduleTrigger",
    "ScheduledRunRef",
    "SectionStyle",
    "SelectedPromptFragment",
    "SessionBusyError",
    "SessionCursorError",
    "SessionEventCursor",
    "SessionInvocationState",
    "SessionNotFoundError",
    "SessionRef",
    "SessionResumeError",
    "SessionStore",
    "SessionTerminalError",
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
    "WorkflowProfile",
    "WorkspaceAgentMetadata",
    "WorkspaceAgentRegistry",
    "WorkspaceAgentSpec",
    "WorkspaceAgentVersion",
    "WorkspaceRuntimeFacade",
    "WorkspaceSpec",
    "agent_response_messages",
    "assert_prompt_tool_parity",
    "bundled_model_catalog",
    "bundled_provider_model_catalog",
    "bundled_provider_models",
    "bundled_provider_pricing",
    "conversational_response",
    "create_openai_vector_store",
    "create_runtime_with_default_providers",
    "dataclass_from_dict",
    "dataclass_to_dict",
    "get_workflow_profile",
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
    "workflow_profile_docs",
    "workflow_profiles",
    "workspace_agent_from_dict",
    "workspace_agent_to_dict",
]

_LAZY_EXPORTS: dict[str, tuple[str, str]] = {
    "GoogleBigQueryMCPAuth": (
        "blackbox.integrations",
        "GoogleBigQueryMCPAuth",
    ),
    "google_application_default_bigquery_auth": (
        "blackbox.integrations",
        "google_application_default_bigquery_auth",
    ),
    "google_bigquery_mcp_toolset": (
        "blackbox.integrations",
        "google_bigquery_mcp_toolset",
    ),
    "google_maps_mcp_toolset": (
        "blackbox.integrations",
        "google_maps_mcp_toolset",
    ),
    "OpenAIVectorStoreDocument": (
        "blackbox.integrations",
        "OpenAIVectorStoreDocument",
    ),
    "OpenAIVectorStoreHandle": (
        "blackbox.integrations",
        "OpenAIVectorStoreHandle",
    ),
    "create_openai_vector_store": (
        "blackbox.integrations",
        "create_openai_vector_store",
    ),
    "temporary_openai_file_search": (
        "blackbox.integrations",
        "temporary_openai_file_search",
    ),
    "temporary_openai_vector_store": (
        "blackbox.integrations",
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
