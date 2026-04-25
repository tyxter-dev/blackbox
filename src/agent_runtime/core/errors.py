class AgentRuntimeError(Exception):
    """Base exception for the runtime."""


class ConfigurationError(AgentRuntimeError):
    """Raised when runtime or provider configuration is invalid."""


class ProviderNotFoundError(AgentRuntimeError):
    """Raised when no provider is registered for a provider key."""


class ProviderNotConfiguredError(ConfigurationError):
    """Raised when a provider adapter lacks credentials or optional dependencies."""


class CapabilityError(AgentRuntimeError):
    """Raised when a feature is requested that the provider does not advertise."""


class UnsupportedFeatureError(CapabilityError):
    """Alias for CapabilityError kept for backward compatibility."""


class ProviderExecutionError(AgentRuntimeError):
    """Raised when a provider call fails at runtime (network, server, parsing)."""


class ToolExecutionError(AgentRuntimeError):
    """Raised by local tool execution when configured to fail hard."""


class ApprovalError(AgentRuntimeError):
    """Raised when an approval flow fails or receives an invalid decision."""


class SessionError(AgentRuntimeError):
    """Raised when a session is in an invalid state for the requested operation."""


class ArtifactError(AgentRuntimeError):
    """Raised when an artifact cannot be created, read, or referenced."""


class WorkspaceError(AgentRuntimeError):
    """Raised when a workspace operation fails."""


class MCPError(AgentRuntimeError):
    """Raised by MCP connector or remote MCP failures."""
