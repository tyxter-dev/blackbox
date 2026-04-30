from __future__ import annotations

import pytest

from blackbox.core.errors import UnsupportedFeatureError
from blackbox.hosted_tools import (
    CodeInterpreter,
    FileSearch,
    HostedToolRaw,
    RemoteMCP,
    WebSearch,
    anthropic_beta_values,
    anthropic_mcp_servers,
    openai_include_values,
    to_anthropic_tool,
    to_gemini_tool,
    to_openai_tool,
    to_raw_hosted_tool,
)


def test_web_search_serializes_for_openai() -> None:
    assert to_openai_tool(WebSearch()) == {"type": "web_search"}
    assert to_openai_tool(WebSearch(search_context_size="low")) == {
        "type": "web_search",
        "search_context_size": "low",
    }


def test_file_search_serializes_for_openai_and_requests_include() -> None:
    spec = FileSearch(
        vector_store_ids=["vs_1"],
        max_num_results=3,
        filters={"type": "eq", "key": "kind", "value": "doc"},
        include_results=True,
    )

    assert to_openai_tool(spec) == {
        "type": "file_search",
        "vector_store_ids": ["vs_1"],
        "max_num_results": 3,
        "filters": {"type": "eq", "key": "kind", "value": "doc"},
    }
    assert openai_include_values([spec]) == ["file_search_call.results"]


def test_code_interpreter_serializes_for_openai() -> None:
    assert to_openai_tool(CodeInterpreter(container_id="cntr_1")) == {
        "type": "code_interpreter",
        "container": {"type": "container_id", "container_id": "cntr_1"},
    }


def test_raw_hosted_tool_passthrough() -> None:
    raw = HostedToolRaw({"type": "future_tool", "x": 1})

    assert to_openai_tool(raw) == {"type": "future_tool", "x": 1}
    assert to_raw_hosted_tool(raw, provider="xAI") == {"type": "future_tool", "x": 1}


def test_remote_mcp_serializes_for_openai() -> None:
    spec = RemoteMCP(
        server_label="github",
        server_url="https://mcp.example.com/sse",
        server_description="GitHub MCP server",
        authorization="token",
        headers={"X-Trace": "trace-1"},
        allowed_tools=["list_issues"],
        require_approval="never",
    )

    assert to_openai_tool(spec) == {
        "type": "mcp",
        "server_label": "github",
        "server_url": "https://mcp.example.com/sse",
        "server_description": "GitHub MCP server",
        "authorization": "token",
        "headers": {"X-Trace": "trace-1"},
        "allowed_tools": ["list_issues"],
        "require_approval": "never",
    }


def test_remote_mcp_serializes_connector_id_for_openai() -> None:
    spec = RemoteMCP(
        server_label="github",
        connector_id="conn_github",
        allowed_tools={"tool_names": ["list_issues"]},
    )

    assert to_openai_tool(spec) == {
        "type": "mcp",
        "server_label": "github",
        "connector_id": "conn_github",
        "allowed_tools": {"tool_names": ["list_issues"]},
    }


def test_remote_mcp_rejects_openai_missing_or_ambiguous_endpoint() -> None:
    with pytest.raises(UnsupportedFeatureError):
        to_openai_tool(RemoteMCP(server_label="github"))

    with pytest.raises(UnsupportedFeatureError):
        to_openai_tool(
            RemoteMCP(
                server_label="github",
                server_url="https://mcp.example.com/sse",
                connector_id="conn_github",
            )
        )


def test_remote_mcp_rejects_openai_denylist() -> None:
    spec = RemoteMCP(
        server_label="github",
        server_url="https://mcp.example.com/sse",
        denied_tools=["delete_repo"],
    )

    with pytest.raises(UnsupportedFeatureError):
        to_openai_tool(spec)


def test_remote_mcp_rejects_openai_duplicate_authorization_header() -> None:
    spec = RemoteMCP(
        server_label="github",
        server_url="https://mcp.example.com/sse",
        authorization="token",
        headers={"Authorization": "Bearer token"},
    )

    with pytest.raises(UnsupportedFeatureError, match=r"headers\.Authorization"):
        to_openai_tool(spec)


def test_remote_mcp_serializes_for_anthropic() -> None:
    spec = RemoteMCP(
        server_label="github",
        server_url="https://mcp.example.com/sse",
        authorization="token",
        allowed_tools=["list_issues"],
        denied_tools=["delete_repo"],
        defer_loading=True,
        tool_configs={"list_issues": {"defer_loading": False}},
        cache_control={"type": "ephemeral"},
    )

    assert anthropic_mcp_servers([spec]) == [
        {
            "type": "url",
            "url": "https://mcp.example.com/sse",
            "name": "github",
            "authorization_token": "token",
        }
    ]
    assert to_anthropic_tool(spec) == {
        "type": "mcp_toolset",
        "mcp_server_name": "github",
        "default_config": {"enabled": False, "defer_loading": True},
        "configs": {
            "list_issues": {"defer_loading": False, "enabled": True},
            "delete_repo": {"enabled": False},
        },
        "cache_control": {"type": "ephemeral"},
    }
    assert anthropic_beta_values([spec]) == ["mcp-client-2025-11-20"]


def test_remote_mcp_rejects_anthropic_headers() -> None:
    spec = RemoteMCP(
        server_label="github",
        server_url="https://mcp.example.com/sse",
        headers={"X-Trace": "trace-1"},
    )

    with pytest.raises(UnsupportedFeatureError, match="custom headers"):
        anthropic_mcp_servers([spec])
    with pytest.raises(UnsupportedFeatureError, match="custom headers"):
        to_anthropic_tool(spec)


def test_gemini_web_search_and_unsupported_tools() -> None:
    assert to_gemini_tool(WebSearch()) == {"google_search": {}}
    with pytest.raises(UnsupportedFeatureError):
        to_gemini_tool(FileSearch(vector_store_ids=["vs_1"]))
