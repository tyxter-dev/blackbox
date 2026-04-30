from __future__ import annotations

from blackbox.core import errors


def test_all_errors_descend_from_runtime_base() -> None:
    base = errors.AgentRuntimeError
    for name in (
        "ConfigurationError",
        "ProviderNotFoundError",
        "ProviderNotConfiguredError",
        "CapabilityError",
        "UnsupportedFeatureError",
        "ProviderExecutionError",
        "ToolExecutionError",
        "ApprovalError",
        "SessionError",
        "ArtifactError",
        "WorkspaceError",
        "MCPError",
    ):
        cls = getattr(errors, name)
        assert issubclass(cls, base), name


def test_configuration_subclasses() -> None:
    assert issubclass(errors.ProviderNotConfiguredError, errors.ConfigurationError)
    assert issubclass(errors.UnsupportedFeatureError, errors.CapabilityError)
