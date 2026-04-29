from __future__ import annotations

import dataclasses
import re
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
AgentResponseMode = Literal["single", "conversational"]
AgentSessionResultStatus = Literal[
    "completed",
    "failed",
    "cancelled",
    "waiting_for_approval",
    "timeout",
]


@dataclass(slots=True, frozen=True)
class AgentMessage:
    """One assistant-facing message produced by an agent turn.

    Agent providers remain event/session based internally. This is an output
    projection for UIs that want chat-bubble style responses without forcing
    callers to model a list of messages as structured output.
    """

    content: str
    role: Literal["assistant"] = "assistant"
    index: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class AgentResponseSpec:
    """Configure how a provider-managed agent should project final replies."""

    mode: AgentResponseMode = "single"
    min_messages: int = 1
    max_messages: int | None = None
    max_chars_per_message: int | None = None
    separator: str = "\n\n"
    instruction: str | None = None


def conversational_response(
    *,
    min_messages: int = 2,
    max_messages: int | None = 3,
    max_chars_per_message: int | None = 240,
    instruction: str | None = None,
) -> AgentResponseSpec:
    """Return the common multi-message response profile for chat UIs."""

    if min_messages < 1:
        raise ValueError("min_messages must be at least 1.")
    if max_messages is not None and max_messages < 1:
        raise ValueError("max_messages must be at least 1 when provided.")
    if max_messages is not None and min_messages > max_messages:
        raise ValueError("min_messages cannot exceed max_messages.")
    if max_chars_per_message is not None and max_chars_per_message < 40:
        raise ValueError("max_chars_per_message must be at least 40 when provided.")
    return AgentResponseSpec(
        mode="conversational",
        min_messages=min_messages,
        max_messages=max_messages,
        max_chars_per_message=max_chars_per_message,
        instruction=instruction,
    )


def agent_response_instructions(spec: AgentResponseSpec | None) -> str | None:
    """Return provider instructions for a configured agent response style."""

    if spec is None or spec.mode == "single":
        return None
    if spec.instruction is not None:
        return spec.instruction
    if spec.max_messages is None:
        count = f"at least {spec.min_messages}"
    elif spec.min_messages == spec.max_messages:
        count = str(spec.min_messages)
    else:
        count = f"{spec.min_messages}-{spec.max_messages}"
    length = (
        f" Keep each message under about {spec.max_chars_per_message} characters."
        if spec.max_chars_per_message is not None
        else ""
    )
    return (
        f"Respond as {count} short assistant messages for a chat UI. "
        "Separate messages with a blank line. Do not number the messages and "
        f"do not return JSON.{length}"
    )


def agent_response_messages(
    text: str,
    spec: AgentResponseSpec | None = None,
) -> list[AgentMessage]:
    """Project final agent text into UI-ready assistant messages."""

    response_spec = spec or AgentResponseSpec()
    content = text.strip()
    if not content:
        return []
    if response_spec.mode == "single":
        return [AgentMessage(content=content)]

    segments = _split_paragraphs(content)
    segments = _split_long_segments(segments, response_spec.max_chars_per_message)
    target_min = response_spec.min_messages
    if response_spec.max_messages is not None:
        target_min = min(target_min, response_spec.max_messages)
    segments = _ensure_min_segments(
        segments,
        min_messages=target_min,
        max_chars=response_spec.max_chars_per_message,
    )
    if response_spec.max_messages is not None:
        segments = _merge_to_limit(
            segments,
            max_messages=response_spec.max_messages,
            separator=response_spec.separator,
        )
    return [
        AgentMessage(content=segment, index=index)
        for index, segment in enumerate(segments)
        if segment
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
    messages: list[AgentMessage] = field(default_factory=list)
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
    for key in ("hosted_tools", "mcp", "mcp_trust", "cost", "cache"):
        if key in metadata:
            summary[key] = metadata[key]
    payloads = getattr(result, "payloads", None)
    if payloads:
        summary["payloads"] = [_payload_summary(payload) for payload in payloads]
    messages = getattr(result, "messages", None)
    if messages:
        summary["message_count"] = len(messages)
        summary["messages"] = [_message_summary(message) for message in messages]
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


def _message_summary(message: Any) -> dict[str, Any]:
    content = getattr(message, "content", "")
    return {
        "role": getattr(message, "role", None),
        "index": getattr(message, "index", None),
        "chars": len(content) if isinstance(content, str) else None,
        "metadata": _jsonable(getattr(message, "metadata", {})),
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


def _split_paragraphs(text: str) -> list[str]:
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n+", text) if part.strip()]
    return paragraphs or [text.strip()]


def _split_long_segments(
    segments: list[str],
    max_chars: int | None,
) -> list[str]:
    if max_chars is None:
        return segments
    split: list[str] = []
    for segment in segments:
        if len(segment) <= max_chars:
            split.append(segment)
            continue
        split.extend(_pack_sentences(segment, max_chars=max_chars))
    return split


def _ensure_min_segments(
    segments: list[str],
    *,
    min_messages: int,
    max_chars: int | None,
) -> list[str]:
    result = list(segments)
    while len(result) < min_messages:
        index = max(range(len(result)), key=lambda item: len(result[item]))
        pieces = _split_once(result[index], max_chars=max_chars)
        if len(pieces) < 2:
            break
        result[index : index + 1] = pieces
    return result


def _merge_to_limit(
    segments: list[str],
    *,
    max_messages: int,
    separator: str,
) -> list[str]:
    if len(segments) <= max_messages:
        return segments
    head = segments[: max_messages - 1]
    tail = separator.join(segments[max_messages - 1 :])
    return [*head, tail]


def _split_once(segment: str, *, max_chars: int | None) -> list[str]:
    packed = _pack_sentences(segment, max_chars=max_chars or max(len(segment) // 2, 80))
    if len(packed) > 1:
        return packed[:2]
    midpoint = len(segment) // 2
    split_at = segment.rfind(" ", 0, midpoint)
    if split_at <= 0:
        split_at = segment.find(" ", midpoint)
    if split_at <= 0:
        return [segment]
    return [segment[:split_at].strip(), segment[split_at:].strip()]


def _pack_sentences(segment: str, *, max_chars: int) -> list[str]:
    sentences = [part.strip() for part in re.split(r"(?<=[.!?])\s+", segment) if part.strip()]
    if len(sentences) <= 1:
        return _split_by_words(segment, max_chars=max_chars)
    packed: list[str] = []
    current = ""
    for sentence in sentences:
        if not current:
            current = sentence
        elif len(f"{current} {sentence}") <= max_chars:
            current = f"{current} {sentence}"
        else:
            packed.extend(_split_by_words(current, max_chars=max_chars))
            current = sentence
    if current:
        packed.extend(_split_by_words(current, max_chars=max_chars))
    return packed


def _split_by_words(segment: str, *, max_chars: int) -> list[str]:
    words = segment.split()
    if not words:
        return []
    chunks: list[str] = []
    current = words[0]
    for word in words[1:]:
        if len(f"{current} {word}") <= max_chars:
            current = f"{current} {word}"
            continue
        chunks.append(current)
        current = word
    chunks.append(current)
    return chunks
