from __future__ import annotations

import pytest

from agent_runtime.core.errors import UnsupportedFeatureError
from agent_runtime.hosted_tools import (
    CodeInterpreter,
    FileSearch,
    HostedToolRaw,
    WebSearch,
    openai_include_values,
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


def test_gemini_web_search_and_unsupported_tools() -> None:
    assert to_gemini_tool(WebSearch()) == {"google_search": {}}
    with pytest.raises(UnsupportedFeatureError):
        to_gemini_tool(FileSearch(vector_store_ids=["vs_1"]))
