from __future__ import annotations

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
