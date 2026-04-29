from __future__ import annotations

import pytest

from agent_runtime.mcp import MCPServerSpec, trust_fingerprint


def test_stdio_command_rejects_shell_style_string() -> None:
    spec = MCPServerSpec(
        name="local",
        transport="stdio",
        command="python -m local_mcp",
    )

    with pytest.raises(ValueError, match="shell-style command string"):
        spec.validate_security()


def test_stdio_command_rejects_shell_executable() -> None:
    spec = MCPServerSpec(
        name="local",
        transport="stdio",
        command="bash",
        args=["-lc", "local-mcp"],
    )

    with pytest.raises(ValueError, match="shell executable"):
        spec.validate_security()


def test_remote_http_requires_https_in_production() -> None:
    spec = MCPServerSpec(
        name="remote",
        transport="streamable_http",
        url="http://mcp.example.com/mcp",
    )

    with pytest.raises(ValueError, match="https"):
        spec.validate_security()


def test_remote_http_rejects_private_ip_unless_dev_override() -> None:
    blocked = MCPServerSpec(
        name="local-dev",
        transport="streamable_http",
        url="https://127.0.0.1:3000/mcp",
    )
    allowed = MCPServerSpec(
        name="local-dev",
        transport="streamable_http",
        url="https://127.0.0.1:3000/mcp",
        allow_remote_http=True,
    )

    with pytest.raises(ValueError, match="private/reserved"):
        blocked.validate_security()

    allowed.validate_security()


def test_trust_fingerprint_changes_with_security_relevant_fields() -> None:
    base = MCPServerSpec(
        name="remote",
        transport="streamable_http",
        url="https://mcp.example.com/mcp",
        allowed_tools=["read_issue"],
    )
    changed = MCPServerSpec(
        name="remote",
        transport="streamable_http",
        url="https://mcp.example.com/mcp",
        allowed_tools=["create_issue"],
    )

    assert trust_fingerprint(base) != trust_fingerprint(changed)
