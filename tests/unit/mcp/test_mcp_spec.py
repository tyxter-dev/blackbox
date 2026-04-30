from __future__ import annotations

import pytest

from blackbox.mcp import MCPServerSpec


def test_stdio_managed_validation_requires_command() -> None:
    spec = MCPServerSpec(name="tickets", transport="stdio")

    with pytest.raises(ValueError, match="requires command"):
        spec.validate_managed()


def test_http_managed_validation_requires_url() -> None:
    spec = MCPServerSpec(name="tickets", transport="streamable_http")

    with pytest.raises(ValueError, match="requires url"):
        spec.validate_managed()


def test_remote_http_local_dispatch_requires_explicit_allow() -> None:
    spec = MCPServerSpec(
        name="github",
        transport="streamable_http",
        url="https://mcp.example.com/mcp",
    )

    with pytest.raises(ValueError, match="allow_remote_http=True"):
        spec.validate_managed()

    MCPServerSpec(
        name="github",
        transport="streamable_http",
        url="https://mcp.example.com/mcp",
        allow_remote_http=True,
    ).validate_managed()


def test_allowed_and_denied_tools_cannot_overlap() -> None:
    with pytest.raises(ValueError, match="overlap"):
        MCPServerSpec(
            name="github",
            transport="streamable_http",
            url="https://mcp.example.com/mcp",
            allowed_tools=["list_issues"],
            denied_tools=["list_issues"],
        )


def test_secret_fields_are_redacted_from_repr_and_serialization() -> None:
    spec = MCPServerSpec(
        name="github",
        transport="streamable_http",
        url="https://mcp.example.com/mcp?token=secret",
        headers={"Authorization": "Bearer secret", "X-Plain": "ok"},
        env={"API_TOKEN": "secret"},
        authorization="Bearer secret",
        auth_identity="tenant-secret",
    )

    rendered = repr(spec)
    redacted = spec.to_redacted_dict()

    assert "Bearer secret" not in rendered
    assert "token=secret" not in rendered
    assert redacted["headers"]["Authorization"] == "<redacted>"
    assert redacted["env"]["API_TOKEN"] == "<redacted>"
    assert redacted["authorization"] == "<redacted>"
    assert redacted["auth_identity"] == "<redacted>"
