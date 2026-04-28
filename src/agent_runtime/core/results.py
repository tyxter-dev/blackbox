from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import Any, Generic, Literal, TypeVar

from agent_runtime.core.accounting import ModelUsage
from agent_runtime.core.artifacts import Artifact
from agent_runtime.core.events import AgentEvent
from agent_runtime.core.items import RunItem
from agent_runtime.core.sessions import SessionRef
from agent_runtime.core.state import ProviderState

T = TypeVar("T")


OutputStrategy = Literal[
    "provider_native",
    "finalizer_tool",
    "posthoc_parse",
    "posthoc_parse_with_retry",
]
OutputFallback = Literal["error", "finalizer_tool", "posthoc_parse"]
AgentSessionResultStatus = Literal[
    "completed",
    "failed",
    "cancelled",
    "waiting_for_approval",
    "timeout",
]


@dataclass(slots=True)
class OutputSpec:
    """How the runtime should produce structured output.

    ``schema`` is the target type (Pydantic model, dataclass, or ``str``).
    ``strategy`` selects the validation pipeline:

    - ``provider_native``: provider supports strict schema output (e.g. OpenAI
      Responses ``text.format``); the runtime wires the schema down to the
      adapter and trusts the parsed result.
    - ``finalizer_tool``: the runtime exposes a hidden ``submit_final_output``
      tool whose arguments are validated against ``schema``; the model calls
      it to finish.
    - ``posthoc_parse``: the runtime parses the final text against ``schema``
      after generation completes.
    - ``posthoc_parse_with_retry``: same as ``posthoc_parse`` but on failure
      the runtime asks the model to repair its output, up to
      ``max_validation_retries`` times.
    """

    schema: type[Any] | dict[str, Any] | None = None
    strategy: OutputStrategy = "posthoc_parse"
    max_validation_retries: int = 1
    allow_partial: bool = False
    name: str | None = None
    description: str | None = None
    strict: bool = True
    fallback: OutputFallback = "error"


def structured_output(
    schema: type[Any] | dict[str, Any],
    *,
    name: str | None = None,
    description: str | None = None,
    strategy: OutputStrategy = "provider_native",
    fallback: OutputFallback = "posthoc_parse",
    max_validation_retries: int = 1,
    strict: bool = True,
    allow_partial: bool = False,
) -> OutputSpec:
    """Return the common structured-output configuration for app workflows.

    The default asks providers that support native schemas to enforce the
    structure, while retaining runtime post-hoc parsing as a practical fallback
    when a provider cannot honor native structured output.
    """

    return OutputSpec(
        schema=schema,
        strategy=strategy,
        max_validation_retries=max_validation_retries,
        allow_partial=allow_partial,
        name=name,
        description=description,
        strict=strict,
        fallback=fallback,
    )


@dataclass(slots=True)
class ToolPayload:
    """A single payload returned by a tool during a high-level run.

    ``content`` was already shown to the model. ``payload`` is the structured
    application-facing data and is never auto-fed back into the model.
    """

    tool_name: str
    payload: Any
    call_id: str | None = None


@dataclass(slots=True)
class AgentResult(Generic[T]):
    """Collected output of ``AgentRuntime.run(...)``.

    ``output`` carries the validated structured result when the caller passes
    an ``output_type``. Otherwise it falls back to the final text projection.
    """

    output: T
    text: str
    events: list[AgentEvent] = field(default_factory=list)
    items: list[RunItem] = field(default_factory=list)
    artifacts: list[Artifact] = field(default_factory=list)
    payloads: list[ToolPayload] = field(default_factory=list)
    provider_state: ProviderState | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def summary(self, *, include_output: bool = False) -> dict[str, Any]:
        """Return compact JSON-safe metadata for logs, examples, and demos."""

        return result_summary(self, include_output=include_output)


@dataclass(slots=True)
class AgentSessionResult(Generic[T]):
    """Collected output of ``runtime.agents.run(...)``.

    ``session_ref`` preserves the provider/session handle for follow-ups,
    replay, artifact listing, or cancellation. ``output`` is the validated
    projection of the final session text when an ``output_type`` is supplied;
    otherwise it is the same string as ``text``.
    """

    output: T
    text: str
    session_ref: SessionRef
    status: AgentSessionResultStatus = "completed"
    events: list[AgentEvent] = field(default_factory=list)
    artifacts: list[Artifact] = field(default_factory=list)
    provider_state: ProviderState | None = None
    usage: ModelUsage | None = None
    trace: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def summary(self, *, include_output: bool = False) -> dict[str, Any]:
        """Return compact JSON-safe metadata for logs, examples, and demos."""

        return result_summary(self, include_output=include_output)


def result_summary(result: Any, *, include_output: bool = False) -> dict[str, Any]:
    """Return a compact, JSON-safe summary of a runtime result.

    The summary intentionally excludes full text by default. It keeps the parts
    that are useful when reviewing examples or production logs: validation,
    fallback, usage, hosted/MCP metadata, payloads, artifacts, and provider
    source references.
    """

    metadata = getattr(result, "metadata", {}) or {}
    provider_state = getattr(result, "provider_state", None)
    tool_state = getattr(provider_state, "tool_state", {}) if provider_state is not None else {}
    summary: dict[str, Any] = {
        "validation_attempts": metadata.get("validation_attempts"),
        "provider_native_fallback": metadata.get("provider_native_fallback"),
        "usage": metadata.get("usage"),
    }
    for key in ("hosted_tools", "mcp", "cost", "cache"):
        if key in metadata:
            summary[key] = metadata[key]
    payloads = getattr(result, "payloads", None)
    if payloads:
        summary["payloads"] = [_payload_summary(payload) for payload in payloads]
    artifacts = getattr(result, "artifacts", None)
    if artifacts:
        summary["artifacts"] = [_artifact_summary(artifact) for artifact in artifacts]
    source_references = tool_state.get("source_references")
    if source_references:
        summary["source_references"] = _jsonable(source_references)
    if include_output:
        summary["output"] = _jsonable(getattr(result, "output", None))
    return {key: _jsonable(value) for key, value in summary.items()}


def _payload_summary(payload: Any) -> dict[str, Any]:
    return {
        "tool_name": getattr(payload, "tool_name", None),
        "call_id": getattr(payload, "call_id", None),
        "payload": _jsonable(getattr(payload, "payload", None)),
    }


def _artifact_summary(artifact: Any) -> dict[str, Any]:
    data = getattr(artifact, "data", None)
    return {
        "id": getattr(artifact, "id", None),
        "type": getattr(artifact, "type", None),
        "name": getattr(artifact, "name", None),
        "uri": getattr(artifact, "uri", None),
        "data_present": data is not None,
        "metadata": _jsonable(getattr(artifact, "metadata", {})),
    }


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return _jsonable(model_dump())
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        return _jsonable(to_dict())
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return _jsonable(dataclasses.asdict(value))
    return str(value)
