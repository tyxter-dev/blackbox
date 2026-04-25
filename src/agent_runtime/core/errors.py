class AgentRuntimeError(Exception):
    """Base exception for the runtime."""


class ProviderNotFoundError(AgentRuntimeError):
    """Raised when no provider is registered for a provider key."""


class ProviderNotConfiguredError(AgentRuntimeError):
    """Raised when a provider adapter lacks credentials or optional dependencies."""


class UnsupportedFeatureError(AgentRuntimeError):
    """Raised when a provider does not support a requested feature."""


class ToolExecutionError(AgentRuntimeError):
    """Raised by local tool execution when configured to fail hard."""
