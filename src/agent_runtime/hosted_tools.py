from __future__ import annotations

from dataclasses import dataclass
from typing import Any, TypeAlias

from agent_runtime.core.errors import UnsupportedFeatureError


@dataclass(slots=True, frozen=True)
class WebSearch:
    search_context_size: str | None = None
    user_location: dict[str, Any] | None = None


@dataclass(slots=True, frozen=True)
class FileSearch:
    vector_store_ids: list[str]
    max_num_results: int | None = None
    filters: dict[str, Any] | None = None
    include_results: bool = False


@dataclass(slots=True, frozen=True)
class CodeInterpreter:
    container_id: str | None = None


@dataclass(slots=True, frozen=True)
class HostedToolRaw:
    payload: dict[str, Any]


HostedToolSpec: TypeAlias = WebSearch | FileSearch | CodeInterpreter | HostedToolRaw


def to_openai_tool(spec: HostedToolSpec) -> dict[str, Any]:
    if isinstance(spec, WebSearch):
        payload: dict[str, Any] = {"type": "web_search"}
        if spec.search_context_size is not None:
            payload["search_context_size"] = spec.search_context_size
        if spec.user_location is not None:
            payload["user_location"] = dict(spec.user_location)
        return payload
    if isinstance(spec, FileSearch):
        payload = {"type": "file_search", "vector_store_ids": list(spec.vector_store_ids)}
        if spec.max_num_results is not None:
            payload["max_num_results"] = spec.max_num_results
        if spec.filters is not None:
            payload["filters"] = dict(spec.filters)
        return payload
    if isinstance(spec, CodeInterpreter):
        payload = {"type": "code_interpreter"}
        if spec.container_id is not None:
            payload["container"] = {"type": "container_id", "container_id": spec.container_id}
        return payload
    return dict(spec.payload)


def to_gemini_tool(spec: HostedToolSpec) -> dict[str, Any]:
    if isinstance(spec, WebSearch):
        return {"google_search": {}}
    if isinstance(spec, HostedToolRaw):
        return dict(spec.payload)
    raise UnsupportedFeatureError(
        f"Gemini hosted tool mapping is not implemented for {type(spec).__name__}."
    )


def to_anthropic_tool(spec: HostedToolSpec) -> dict[str, Any]:
    if isinstance(spec, HostedToolRaw):
        return dict(spec.payload)
    raise UnsupportedFeatureError(
        f"Anthropic hosted tool mapping is not implemented for {type(spec).__name__}."
    )


def to_raw_hosted_tool(spec: HostedToolSpec, *, provider: str) -> dict[str, Any]:
    if isinstance(spec, HostedToolRaw):
        return dict(spec.payload)
    raise UnsupportedFeatureError(
        f"{provider} hosted tool mapping is not implemented for {type(spec).__name__}."
    )


def openai_include_values(specs: list[HostedToolSpec]) -> list[str]:
    include: list[str] = []
    if any(isinstance(spec, FileSearch) and spec.include_results for spec in specs):
        include.append("file_search_call.results")
    return include
