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


class AgentWebhookError(AgentRuntimeError):
    """Raised when an agent-provider webhook cannot be ingested."""


class AgentWebhookVerificationError(AgentWebhookError):
    """Raised when a provider webhook fails signature or replay verification."""


class ProviderExecutionError(AgentRuntimeError):
    """Raised when a provider call fails at runtime (network, server, parsing)."""


class ToolExecutionError(AgentRuntimeError):
    """Raised by local tool execution when configured to fail hard."""


class ApprovalError(AgentRuntimeError):
    """Raised when an approval flow fails or receives an invalid decision."""


class SessionError(AgentRuntimeError):
    """Raised when a session is in an invalid state for the requested operation."""


class SessionNotFoundError(SessionError):
    """Raised when a durable session state cannot be found."""

    def __init__(
        self,
        message: str,
        *,
        session_id: str | None = None,
        provider: str | None = None,
        status: str | None = None,
        operation: str | None = None,
        safe_to_retry: bool = False,
    ) -> None:
        super().__init__(message)
        self.session_id = session_id
        self.provider = provider
        self.status = status
        self.operation = operation
        self.safe_to_retry = safe_to_retry


class SessionCursorError(SessionError):
    """Raised when a session event replay cursor is unknown or invalid."""

    def __init__(
        self,
        message: str,
        *,
        session_id: str | None = None,
        provider: str | None = None,
        status: str | None = None,
        operation: str | None = None,
        safe_to_retry: bool = False,
    ) -> None:
        super().__init__(message)
        self.session_id = session_id
        self.provider = provider
        self.status = status
        self.operation = operation
        self.safe_to_retry = safe_to_retry


class SessionResumeError(SessionError):
    """Raised when a provider cannot reconnect from persisted session metadata."""

    def __init__(
        self,
        message: str,
        *,
        session_id: str | None = None,
        provider: str | None = None,
        status: str | None = None,
        operation: str | None = None,
        safe_to_retry: bool = True,
    ) -> None:
        super().__init__(message)
        self.session_id = session_id
        self.provider = provider
        self.status = status
        self.operation = operation
        self.safe_to_retry = safe_to_retry


class SessionBusyError(SessionError):
    """Raised when a session cannot accept another mutating operation yet."""

    def __init__(
        self,
        message: str,
        *,
        session_id: str | None = None,
        provider: str | None = None,
        status: str | None = None,
        operation: str | None = None,
        safe_to_retry: bool = True,
    ) -> None:
        super().__init__(message)
        self.session_id = session_id
        self.provider = provider
        self.status = status
        self.operation = operation
        self.safe_to_retry = safe_to_retry


class SessionTerminalError(SessionError):
    """Raised when a terminal session cannot accept a requested operation."""

    def __init__(
        self,
        message: str,
        *,
        session_id: str | None = None,
        provider: str | None = None,
        status: str | None = None,
        operation: str | None = None,
        safe_to_retry: bool = False,
    ) -> None:
        super().__init__(message)
        self.session_id = session_id
        self.provider = provider
        self.status = status
        self.operation = operation
        self.safe_to_retry = safe_to_retry


class ArtifactError(AgentRuntimeError):
    """Raised when an artifact cannot be created, read, or referenced."""


class WorkspaceError(AgentRuntimeError):
    """Raised when a workspace operation fails."""


class MCPError(AgentRuntimeError):
    """Raised by MCP connector or remote MCP failures."""


class MCPAuthenticationError(MCPError):
    """Raised when a remote MCP server rejects or requires authentication."""

    def __init__(
        self,
        message: str,
        *,
        server: str | None = None,
        status_code: int | None = None,
        www_authenticate: str | None = None,
        resource_metadata_url: str | None = None,
        scope: str | None = None,
        safe_to_retry: bool = True,
    ) -> None:
        super().__init__(message)
        self.server = server
        self.status_code = status_code
        self.www_authenticate = www_authenticate
        self.resource_metadata_url = resource_metadata_url
        self.scope = scope
        self.safe_to_retry = safe_to_retry


class RealtimeError(AgentRuntimeError):
    """Base class for realtime session failures."""


class RealtimeConnectionError(RealtimeError):
    """Raised when a realtime session cannot be established."""


class RealtimeSessionError(RealtimeError):
    """Raised when a realtime provider reports a session-level failure."""


class RealtimeTransportError(RealtimeError):
    """Raised for realtime transport failures after a session is established."""


class RealtimeUnsupportedFeatureError(CapabilityError):
    """Raised when a realtime request asks for an unsupported feature."""


class RealtimeMediaError(RealtimeError):
    """Raised when realtime media input is invalid or too large."""


class OutputValidationError(AgentRuntimeError):
    """Raised when a model's final output cannot be validated against the requested type.

    Carries the raw text and the underlying validator error so the application
    can decide whether to retry, log, or surface to the user.
    """

    def __init__(self, message: str, *, raw_text: str, cause: Exception | None = None) -> None:
        super().__init__(message)
        self.raw_text = raw_text
        self.cause = cause
