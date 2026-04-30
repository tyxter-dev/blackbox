from __future__ import annotations

import pytest

from blackbox.core.errors import UnsupportedFeatureError
from blackbox.hosted_tools import (
    ApplyPatch,
    CodeInterpreter,
    ComputerUse,
    ContainerSpec,
    FileSearch,
    ImageGeneration,
    Memory,
    Shell,
    TextEditor,
    ToolNamespace,
    ToolSearch,
    URLContext,
    WebFetch,
    WebSearch,
    to_anthropic_tool,
    to_gemini_tool,
    to_openai_tool,
)


def test_openai_v2_hosted_tools_serialize() -> None:
    assert to_openai_tool(
        Shell(execution="hosted", container=ContainerSpec(type="container_auto"))
    ) == {
        "type": "shell",
        "execution": "hosted",
        "container": {"type": "container_auto"},
    }
    assert to_openai_tool(Shell(execution="local")) == {
        "type": "shell",
        "execution": "local",
    }
    assert to_openai_tool(ApplyPatch(workspace_id="ws_1")) == {
        "type": "apply_patch",
        "workspace_id": "ws_1",
    }
    assert to_openai_tool(ComputerUse(display_width=1024, display_height=768)) == {
        "type": "computer",
        "environment": "browser",
        "display_width": 1024,
        "display_height": 768,
    }
    assert to_openai_tool(ImageGeneration(size="1024x1024")) == {
        "type": "image_generation",
        "size": "1024x1024",
    }
    assert to_openai_tool(ToolSearch(max_results=5)) == {
        "type": "tool_search",
        "max_results": 5,
    }


def test_code_interpreter_container_spec_preserves_shortcut() -> None:
    assert to_openai_tool(CodeInterpreter(container_id="cntr_1")) == {
        "type": "code_interpreter",
        "container": {"type": "container_id", "container_id": "cntr_1"},
    }
    assert to_openai_tool(
        CodeInterpreter(container=ContainerSpec(type="container_reference", container_id="cntr_2"))
    ) == {
        "type": "code_interpreter",
        "container": {"type": "container_reference", "container_id": "cntr_2"},
    }


def test_anthropic_v2_hosted_tools_serialize() -> None:
    assert to_anthropic_tool(
        WebSearch(max_uses=2, allowed_domains=["example.com"])
    ) == {
        "type": "web_search_20260209",
        "name": "web_search",
        "max_uses": 2,
        "allowed_domains": ["example.com"],
    }
    assert to_anthropic_tool(WebFetch(urls=["https://example.com"])) == {
        "type": "web_fetch_20260209",
        "name": "web_fetch",
        "urls": ["https://example.com"],
    }
    assert to_anthropic_tool(CodeInterpreter()) == {
        "type": "code_execution_20260120",
        "name": "code_execution",
    }
    assert to_anthropic_tool(ToolSearch(variant="bm25")) == {
        "type": "tool_search_tool_bm25_20251119",
        "name": "tool_search",
    }
    assert to_anthropic_tool(TextEditor(max_characters=10000), model="claude-opus-4-7") == {
        "type": "text_editor_20250728",
        "name": "str_replace_based_edit_tool",
        "max_characters": 10000,
    }
    assert to_anthropic_tool(Memory()) == {
        "type": "memory_20250818",
        "name": "memory",
    }


def test_gemini_v2_hosted_tools_serialize() -> None:
    assert to_gemini_tool(CodeInterpreter()) == {"code_execution": {}}
    assert to_gemini_tool(URLContext(urls=["https://example.com"], max_pages=2)) == {
        "url_context": {"urls": ["https://example.com"], "max_pages": 2}
    }
    assert to_gemini_tool(FileSearch(corpus_ids=["corpus_1"])) == {
        "file_search": {"corpus_ids": ["corpus_1"]}
    }
    with pytest.raises(UnsupportedFeatureError):
        to_gemini_tool(FileSearch(vector_store_ids=["vs_1"]))


def test_tool_namespace_is_deferred_by_default() -> None:
    spec = ToolSearch(
        namespaces=[
            ToolNamespace(
                name="crm",
                description="CRM tools",
                tools=[{"type": "function", "name": "lookup_customer"}],
            )
        ]
    )

    from blackbox.hosted_tools import openai_namespace_tools

    assert openai_namespace_tools([spec]) == [
        {
            "type": "function",
            "name": "lookup_customer",
            "namespace": "crm",
            "namespace_description": "CRM tools",
            "defer_loading": True,
        }
    ]
