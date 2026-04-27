from __future__ import annotations

import importlib
import inspect
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass, field, fields, is_dataclass
from typing import Any, Protocol, cast, runtime_checkable
from uuid import uuid4

from agent_runtime.core.approvals import ApprovalDecision
from agent_runtime.core.artifacts import Artifact, ArtifactPage
from agent_runtime.core.capabilities import AgentCapabilities
from agent_runtime.core.errors import ProviderNotConfiguredError, UnsupportedFeatureError
from agent_runtime.core.events import AgentEvent, EventTypes
from agent_runtime.core.sessions import AgentRef, AgentSession, InvocationRef, SessionRef
from agent_runtime.providers.base import AgentSpec, TaskSpec


@runtime_checkable
class OpenAIAgentsClient(Protocol):
    """Minimal client contract used by ``OpenAICloudAgentProvider``."""

    async def create_agent(self, spec: AgentSpec) -> Any: ...

    async def start_session(self, agent: AgentRef | str, task: TaskSpec) -> Any: ...

    def stream_events(
        self,
        provider_session_id: str,
        *,
        after_event_id: str | None = None,
    ) -> AsyncIterator[Any]: ...

    async def send_message(self, provider_session_id: str, message: str) -> Any: ...

    async def approve(self, approval_id: str, decision: ApprovalDecision) -> Any: ...

    async def cancel(self, provider_session_id: str) -> Any: ...

    async def list_artifacts(
        self,
        provider_session_id: str,
        *,
        type: str | None = None,
        after: str | None = None,
        limit: int = 100,
    ) -> Any: ...


class OpenAICloudAgentProvider:
    """OpenAI Agents SDK-backed provider.

    The name is kept for compatibility with the original cloud-agent scaffold,
    but the implemented surface targets OpenAI's current code-first Agents SDK:
    ``Agent`` / ``Runner.run_streamed`` / ``RunState`` with streaming events,
    sessions, approvals, MCP-capable agents, cancellation, and continuation
    metadata. Agent Builder remains a hosted workflow product, not a public
    session API this adapter can supervise directly.
    """

    provider_id = "openai-agent"
    provider_aliases = ("openai-agents",)

    def __init__(
        self,
        api_key: str | None = None,
        *,
        client: OpenAIAgentsClient | None = None,
        sdk_module: Any | None = None,
    ) -> None:
        self.api_key = api_key
        self._client = client
        self._sdk_module = sdk_module
        self._agents: dict[str, AgentRef] = {}
        self._sessions: dict[str, AgentSession] = {}

    def capabilities(self) -> AgentCapabilities:
        if self._client is None and not (
            self.api_key or self._sdk_module is not None or os.environ.get("OPENAI_API_KEY")
        ):
            return AgentCapabilities(supports_sessions=False, supports_streaming_events=False)
        advertised = getattr(self._client, "capabilities", None)
        if callable(advertised):
            caps = advertised()
            if isinstance(caps, AgentCapabilities):
                return caps
            if isinstance(caps, dict):
                return AgentCapabilities(**caps)
        return AgentCapabilities(
            supports_sessions=True,
            supports_streaming_events=True,
            supports_artifacts=True,
            supports_workspace=True,
            supports_approvals=True,
            supports_mcp=True,
            supports_cancellation=True,
            supports_resume=True,
        )

    async def create_agent(self, spec: AgentSpec) -> AgentRef:
        client = self._get_client()
        raw = await client.create_agent(spec)
        agent = _coerce_agent_ref(raw, provider=self.provider_id, fallback_name=spec.name)
        self._agents[agent.id] = agent
        return agent

    async def start_session(self, agent: AgentRef | str, task: TaskSpec) -> AgentSession:
        client = self._get_client()
        raw = await client.start_session(agent, task)
        session = _coerce_session(raw, provider=self.provider_id, agent=agent, task=task)
        self._sessions[session.id] = session
        return session

    async def stream_events(
        self,
        session: SessionRef | AgentSession,
        *,
        after_event_id: str | None = None,
    ) -> AsyncIterator[AgentEvent]:
        client = self._get_client()
        runtime_session = self._resolve_session(session)
        async for raw_event in client.stream_events(
            _provider_session_id(runtime_session),
            after_event_id=after_event_id,
        ):
            event = _coerce_event(
                raw_event,
                provider=self.provider_id,
                session_id=runtime_session.id,
            )
            _update_session_from_event(runtime_session, event)
            runtime_session.metadata["last_event_id"] = event.id
            yield event

    async def send_message(
        self, session: SessionRef | AgentSession, message: str
    ) -> InvocationRef:
        client = self._get_client()
        runtime_session = self._resolve_session(session)
        raw = await client.send_message(_provider_session_id(runtime_session), message)
        return _coerce_invocation_ref(
            raw,
            provider=self.provider_id,
            session_id=runtime_session.id,
        )

    async def approve(self, approval_id: str, decision: ApprovalDecision) -> None:
        client = self._get_client()
        await client.approve(approval_id, decision)

    async def cancel(self, session: SessionRef | AgentSession) -> None:
        client = self._get_client()
        runtime_session = self._resolve_session(session)
        await client.cancel(_provider_session_id(runtime_session))
        runtime_session.status = "cancelled"

    async def list_artifacts(
        self,
        session: SessionRef | AgentSession,
        *,
        type: str | None = None,
        after: str | None = None,
        limit: int = 100,
    ) -> ArtifactPage:
        client = self._get_client()
        runtime_session = self._resolve_session(session)
        raw = await client.list_artifacts(
            _provider_session_id(runtime_session),
            type=type,
            after=after,
            limit=limit,
        )
        return _coerce_artifact_page(raw)

    def _get_client(self) -> OpenAIAgentsClient:
        if self._client is not None:
            return self._client
        if not self.api_key and not os.environ.get("OPENAI_API_KEY"):
            raise ProviderNotConfiguredError(
                "OpenAICloudAgentProvider requires an api_key or OPENAI_API_KEY."
            )
        self._client = OpenAIAgentsSDKClient(
            api_key=self.api_key,
            sdk_module=self._sdk_module,
        )
        return self._client

    def _resolve_session(self, session: SessionRef | AgentSession) -> AgentSession:
        if isinstance(session, AgentSession):
            self._sessions.setdefault(session.id, session)
            return session
        existing = self._sessions.get(session.id)
        if existing is not None:
            return existing
        restored = AgentSession(
            provider=session.provider,
            task=str(session.metadata.get("task", "")),
            agent_id=session.agent_id,
            status="running",
            metadata=dict(session.metadata),
            id=session.id,
        )
        self._sessions[restored.id] = restored
        return restored


@dataclass(slots=True)
class _SDKAgentRecord:
    spec: AgentSpec
    sdk_agent: Any


@dataclass(slots=True)
class _SDKSession:
    id: str
    agent_id: str
    agent: Any
    task: TaskSpec
    result: Any
    events: list[dict[str, Any]] = field(default_factory=list)
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    approvals: dict[str, Any] = field(default_factory=dict)
    state: Any | None = None
    stream_consumed: bool = False
    terminal: bool = False


class OpenAIAgentsSDKClient:
    """Adapter from ``openai-agents`` to ``OpenAIAgentsClient``."""

    def __init__(self, *, api_key: str | None = None, sdk_module: Any | None = None) -> None:
        self.api_key = api_key
        self._sdk = sdk_module
        self._agents: dict[str, _SDKAgentRecord] = {}
        self._sessions: dict[str, _SDKSession] = {}

    def capabilities(self) -> AgentCapabilities:
        return AgentCapabilities(
            supports_sessions=True,
            supports_streaming_events=True,
            supports_artifacts=True,
            supports_workspace=True,
            supports_approvals=True,
            supports_mcp=True,
            supports_cancellation=True,
            supports_resume=True,
        )

    async def create_agent(self, spec: AgentSpec) -> dict[str, Any]:
        sdk = self._load_sdk()
        sdk_agent = _sdk_agent_from_spec(sdk, spec)
        agent_id = str(spec.metadata.get("id") or f"agent_{spec.name}")
        self._agents[agent_id] = _SDKAgentRecord(spec=spec, sdk_agent=sdk_agent)
        return {
            "id": agent_id,
            "metadata": {
                "instructions": spec.instructions,
                "model": spec.model,
                "tools": list(spec.tools),
                "mcp_servers": list(spec.mcp_servers),
                "environment": dict(spec.environment),
                "permissions": dict(spec.permissions),
                "raw": spec,
            },
        }

    async def start_session(self, agent: AgentRef | str, task: TaskSpec) -> dict[str, Any]:
        sdk = self._load_sdk()
        agent_id = agent.id if isinstance(agent, AgentRef) else str(agent)
        record = self._agents.get(agent_id)
        if record is None:
            raise UnsupportedFeatureError(f"Unknown OpenAI Agents SDK agent {agent_id!r}.")

        result = _run_streamed(sdk, record.sdk_agent, task.prompt, task)
        provider_session_id = f"oasess_{uuid4().hex}"
        session = _SDKSession(
            id=provider_session_id,
            agent_id=agent_id,
            agent=record.sdk_agent,
            task=task,
            result=result,
        )
        self._sessions[provider_session_id] = session
        _append_event(session, {"type": EventTypes.SESSION_STARTED})
        return {
            "id": provider_session_id,
            "provider_session_id": provider_session_id,
            "status": "running",
            "metadata": {
                "agent_id": agent_id,
                "task": task.prompt,
                "sdk": "openai-agents",
            },
        }

    async def stream_events(
        self,
        provider_session_id: str,
        *,
        after_event_id: str | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        session = self._resolve(provider_session_id)

        for event in _events_after(session.events, after_event_id):
            yield event

        if session.stream_consumed or session.terminal:
            return

        session.stream_consumed = True
        try:
            async for raw_event in session.result.stream_events():
                for event in _events_from_sdk_stream_event(raw_event):
                    _append_event(session, event)
                    yield event
        except Exception as exc:  # pragma: no cover - defensive SDK boundary
            event = {
                "type": EventTypes.SESSION_FAILED,
                "error": str(exc),
                "name": type(exc).__name__,
            }
            _append_event(session, event)
            session.terminal = True
            yield event
            return

        interruptions = list(getattr(session.result, "interruptions", []) or [])
        if interruptions:
            session.state = session.result.to_state()
            for interruption in interruptions:
                approval_id = _approval_id(interruption)
                session.approvals[approval_id] = interruption
                event = {
                    "type": EventTypes.APPROVAL_REQUESTED,
                    "approval_id": approval_id,
                    "action": getattr(interruption, "name", None)
                    or getattr(interruption, "tool_name", None)
                    or "tool",
                    "arguments": getattr(interruption, "arguments", None),
                    "data": {"interruption": _as_dict(interruption)},
                }
                _append_event(session, event)
                yield event
            return

        _collect_result_artifacts(session)
        completed = {
            "type": EventTypes.SESSION_COMPLETED,
            "message": str(getattr(session.result, "final_output", "") or ""),
        }
        _append_event(session, completed)
        session.terminal = True
        yield completed

    async def send_message(self, provider_session_id: str, message: str) -> dict[str, Any]:
        sdk = self._load_sdk()
        session = self._resolve(provider_session_id)
        if not session.stream_consumed:
            raise UnsupportedFeatureError(
                "Cannot send an OpenAI Agents SDK follow-up before the current stream finishes."
            )
        if session.state is not None:
            raise UnsupportedFeatureError(
                "OpenAI Agents SDK session is waiting on approvals; call approve() first."
            )
        previous_input = _result_to_input_list(session.result)
        run_input: Any = (
            [*previous_input, {"role": "user", "content": message}]
            if previous_input is not None
            else message
        )
        session.result = _run_streamed(sdk, session.agent, run_input, session.task)
        session.stream_consumed = False
        session.terminal = False
        return {"id": f"inv_{uuid4().hex}", "provider_session_id": provider_session_id}

    async def approve(self, approval_id: str, decision: ApprovalDecision) -> None:
        sdk = self._load_sdk()
        for session in self._sessions.values():
            interruption = session.approvals.get(approval_id)
            if interruption is None:
                continue
            if session.state is None:
                raise UnsupportedFeatureError("OpenAI Agents SDK approval state is unavailable.")
            if decision.approved:
                session.state.approve(interruption)
                event_type = EventTypes.APPROVAL_APPROVED
            else:
                session.state.reject(interruption, rejection_message=decision.reason)
                event_type = EventTypes.APPROVAL_DENIED
            _append_event(
                session,
                {
                    "type": event_type,
                    "approval_id": approval_id,
                    "data": {"decision": decision},
                },
            )
            session.result = _run_streamed(sdk, session.agent, session.state, session.task)
            session.state = None
            session.approvals.pop(approval_id, None)
            session.stream_consumed = False
            session.terminal = False
            return
        raise UnsupportedFeatureError(f"Unknown OpenAI Agents SDK approval request {approval_id!r}.")

    async def cancel(self, provider_session_id: str) -> None:
        session = self._resolve(provider_session_id)
        cancel = getattr(session.result, "cancel", None)
        if not callable(cancel):
            raise UnsupportedFeatureError("OpenAI Agents SDK result does not expose cancel().")
        cancel()
        _append_event(session, {"type": EventTypes.SESSION_CANCELLED})
        session.terminal = True

    async def list_artifacts(
        self,
        provider_session_id: str,
        *,
        type: str | None = None,
        after: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        session = self._resolve(provider_session_id)
        start = 0
        if after is not None:
            for index, artifact in enumerate(session.artifacts):
                if artifact.get("id") == after:
                    start = index + 1
                    break
        items = [
            artifact
            for artifact in session.artifacts[start:]
            if type is None or artifact.get("type") == type
        ]
        return {"items": items[:limit], "has_more": len(items) > limit}

    def _load_sdk(self) -> Any:
        if self._sdk is not None:
            self._set_default_key(self._sdk)
            return self._sdk
        try:
            self._sdk = importlib.import_module("agents")
        except ModuleNotFoundError as exc:
            raise UnsupportedFeatureError(
                "OpenAICloudAgentProvider requires the optional openai-agents package. "
                "Install agent-runtime-core[openai-agents] or pass client=...."
            ) from exc
        self._set_default_key(self._sdk)
        return self._sdk

    def _set_default_key(self, sdk: Any) -> None:
        if self.api_key is not None:
            setter = getattr(sdk, "set_default_openai_key", None)
            if callable(setter):
                setter(self.api_key, use_for_tracing=False)

    def _resolve(self, provider_session_id: str) -> _SDKSession:
        session = self._sessions.get(provider_session_id)
        if session is None:
            raise UnsupportedFeatureError(
                f"OpenAI Agents SDK session {provider_session_id!r} is not active in this process."
            )
        return session


def _sdk_agent_from_spec(sdk: Any, spec: AgentSpec) -> Any:
    existing = spec.metadata.get("sdk_agent")
    if existing is not None:
        return existing

    agent_cls = _sdk_agent_class(sdk, spec)
    kwargs: dict[str, Any] = {
        "name": spec.name,
        "instructions": spec.instructions,
    }
    if spec.model is not None:
        kwargs["model"] = spec.model
    sdk_tools = spec.metadata.get("sdk_tools")
    if sdk_tools is not None:
        kwargs["tools"] = sdk_tools
    sdk_mcp_servers = spec.metadata.get("sdk_mcp_servers")
    if sdk_mcp_servers is not None:
        kwargs["mcp_servers"] = sdk_mcp_servers
    elif spec.mcp_servers:
        kwargs["mcp_servers"] = list(spec.mcp_servers)
    kwargs.update(dict(spec.metadata.get("agent_kwargs") or {}))
    return agent_cls(**kwargs)


def _sdk_agent_class(sdk: Any, spec: AgentSpec) -> Any:
    agent_class = spec.metadata.get("sdk_agent_class")
    if agent_class is not None and not isinstance(agent_class, str):
        return agent_class
    if agent_class == "sandbox" or spec.metadata.get("sandbox") is True:
        try:
            sandbox_module = importlib.import_module("agents.sandbox")
        except ModuleNotFoundError as exc:
            raise UnsupportedFeatureError(
                "OpenAI Agents SDK sandbox support is unavailable in the installed package."
            ) from exc
        return sandbox_module.SandboxAgent
    return sdk.Agent


def _run_streamed(sdk: Any, agent: Any, run_input: Any, task: TaskSpec) -> Any:
    kwargs: dict[str, Any] = {}
    for key in (
        "context",
        "max_turns",
        "hooks",
        "run_config",
        "error_handlers",
        "previous_response_id",
        "auto_previous_response_id",
        "conversation_id",
        "session",
    ):
        if key in task.extra:
            kwargs[key] = task.extra[key]
        elif key in task.metadata:
            kwargs[key] = task.metadata[key]
    return sdk.Runner.run_streamed(agent, run_input, **kwargs)


def _events_from_sdk_stream_event(raw_event: Any) -> list[dict[str, Any]]:
    data = _as_dict(raw_event)
    event_type = str(data.get("type", ""))
    payload = data.get("data")
    payload_data = _as_dict(payload) if payload is not None else {}

    if event_type == "raw_response_event":
        response_type = str(payload_data.get("type", ""))
        if response_type.endswith(".output_text.delta") or response_type == "response.output_text.delta":
            return [
                {
                    "type": EventTypes.MODEL_TEXT_DELTA,
                    "message": payload_data.get("delta", ""),
                    "data": {"sdk_event": data},
                }
            ]
        if response_type.endswith(".created"):
            return [
                {
                    "type": EventTypes.MODEL_REQUEST_STARTED,
                    "data": {"sdk_event": data},
                }
            ]
        if response_type.endswith(".completed"):
            return [
                {
                    "type": EventTypes.MODEL_COMPLETED,
                    "data": {"sdk_event": data},
                }
            ]
    if event_type == "run_item_stream_event":
        return [_event_from_run_item(data)]
    if event_type == "agent_updated_stream_event":
        return [
            {
                "type": EventTypes.CLOUD_AGENT_STATUS_CHANGED,
                "status": "agent_updated",
                "data": {"sdk_event": data},
            }
        ]
    return [{"type": EventTypes.CLOUD_AGENT_LOG, "data": {"sdk_event": data}}]


def _event_from_run_item(data: dict[str, Any]) -> dict[str, Any]:
    name = str(data.get("name", ""))
    item = data.get("item")
    item_data = _as_dict(item) if item is not None else {}
    raw_item = _as_dict(item_data.get("raw_item")) if item_data.get("raw_item") is not None else {}
    tool_name = (
        item_data.get("tool_name")
        or item_data.get("name")
        or raw_item.get("name")
        or raw_item.get("tool_name")
        or "tool"
    )

    if name == "tool_called":
        if tool_name in {"ShellTool", "shell", "bash", "Shell"}:
            event_type = EventTypes.WORKSPACE_COMMAND_STARTED
        elif tool_name in {"ApplyPatchTool", "apply_patch"}:
            event_type = EventTypes.WORKSPACE_PATCH_CREATED
        else:
            event_type = EventTypes.TOOL_CALL_STARTED
        return {
            "type": event_type,
            "item_id": str(item_data.get("id") or raw_item.get("id") or ""),
            "tool": tool_name,
            "arguments": raw_item.get("arguments") or item_data.get("arguments"),
            "data": {"sdk_event": data},
        }
    if name == "tool_output":
        return {
            "type": EventTypes.TOOL_CALL_COMPLETED,
            "item_id": str(item_data.get("id") or raw_item.get("id") or ""),
            "tool": tool_name,
            "result": item_data.get("output") or raw_item.get("output"),
            "data": {"sdk_event": data},
        }
    if name == "message_output_created":
        return {
            "type": EventTypes.CLOUD_AGENT_LOG,
            "message": str(item_data.get("text") or item_data.get("content") or ""),
            "data": {"sdk_event": data},
        }
    return {"type": EventTypes.CLOUD_AGENT_LOG, "data": {"sdk_event": data}}


def _coerce_agent_ref(raw: Any, *, provider: str, fallback_name: str) -> AgentRef:
    if isinstance(raw, AgentRef):
        return raw
    data = _as_dict(raw)
    provider_agent_id = _first_str(data, "id", "agent_id", "provider_agent_id") or fallback_name
    metadata = dict(data.get("metadata") or {})
    metadata.setdefault("raw", data)
    metadata.setdefault("provider_agent_id", provider_agent_id)
    return AgentRef(provider=provider, id=provider_agent_id, metadata=metadata)


def _coerce_session(
    raw: Any,
    *,
    provider: str,
    agent: AgentRef | str,
    task: TaskSpec,
) -> AgentSession:
    if isinstance(raw, AgentSession):
        return raw
    data = _as_dict(raw)
    provider_session_id = _first_str(data, "id", "session_id", "provider_session_id")
    runtime_session_id = _first_str(data, "runtime_session_id") or provider_session_id
    metadata = dict(data.get("metadata") or {})
    if provider_session_id is not None:
        metadata.setdefault("provider_session_id", provider_session_id)
    resume_token = _first_str(data, "resume_token")
    if resume_token is not None:
        metadata.setdefault("resume_token", resume_token)
    metadata.setdefault("raw", data)
    agent_id = agent.id if isinstance(agent, AgentRef) else str(agent)
    status = data.get("status", "running")
    if status not in {"created", "running", "waiting", "completed", "failed", "cancelled"}:
        status = "running"
    return AgentSession(
        provider=provider,
        task=task.prompt,
        agent_id=agent_id,
        model=task.model,
        status=cast(Any, status),
        metadata=metadata,
        id=runtime_session_id or f"{provider_session_id}",
    )


def _coerce_invocation_ref(raw: Any, *, provider: str, session_id: str) -> InvocationRef:
    if isinstance(raw, InvocationRef):
        return raw
    data = _as_dict(raw)
    invocation_id = _first_str(data, "id", "invocation_id", "provider_invocation_id")
    if invocation_id is None:
        invocation_id = f"inv_{session_id}"
    metadata = dict(data.get("metadata") or {})
    metadata.setdefault("raw", data)
    return InvocationRef(provider=provider, session_id=session_id, id=invocation_id, metadata=metadata)


def _coerce_artifact_page(raw: Any) -> ArtifactPage:
    if isinstance(raw, ArtifactPage):
        return raw
    data = _as_dict(raw)
    items_value = data.get("items", raw if isinstance(raw, list) else [])
    items = [_coerce_artifact(item) for item in items_value if item is not None]
    return ArtifactPage(
        items=items,
        next_cursor=data.get("next_cursor"),
        has_more=bool(data.get("has_more", False)),
    )


def _coerce_artifact(raw: Any) -> Artifact:
    if isinstance(raw, Artifact):
        return raw
    data = _as_dict(raw)
    metadata = dict(data.get("metadata") or {})
    metadata.setdefault("raw", data)
    artifact_kwargs: dict[str, Any] = {
        "type": str(data.get("type", "provider_ref")),
        "name": str(data.get("name") or data.get("path") or data.get("id") or "artifact"),
        "data": data.get("data"),
        "uri": data.get("uri"),
        "metadata": metadata,
    }
    if data.get("id") is not None:
        artifact_kwargs["id"] = str(data["id"])
    return Artifact(**artifact_kwargs)


def _coerce_event(raw: Any, *, provider: str, session_id: str) -> AgentEvent:
    if isinstance(raw, AgentEvent):
        return raw
    data = _as_dict(raw)
    event_type = _canonical_event_type(str(data.get("type", EventTypes.CLOUD_AGENT_LOG)))
    event_data = dict(data.get("data") or {})
    for key in (
        "status",
        "message",
        "path",
        "approval_id",
        "action",
        "arguments",
        "artifact",
        "tool",
        "result",
        "error",
    ):
        if key in data and key not in event_data:
            event_data[key] = data[key]
    event_kwargs: dict[str, Any] = {
        "type": event_type,
        "session_id": session_id,
        "provider": provider,
        "item_id": _first_str(data, "item_id", "artifact_id", "approval_id"),
        "data": event_data,
        "raw": raw,
    }
    if data.get("id") is not None:
        event_kwargs["id"] = str(data["id"])
    return AgentEvent(**event_kwargs)


def _canonical_event_type(event_type: str) -> str:
    aliases = {
        "file_read": EventTypes.WORKSPACE_FILE_READ,
        "file_changed": EventTypes.WORKSPACE_FILE_CHANGED,
        "patch": EventTypes.WORKSPACE_PATCH_CREATED,
        "patch_created": EventTypes.WORKSPACE_PATCH_CREATED,
        "command_started": EventTypes.WORKSPACE_COMMAND_STARTED,
        "command_output": EventTypes.WORKSPACE_COMMAND_OUTPUT,
        "command_completed": EventTypes.WORKSPACE_COMMAND_COMPLETED,
        "test_started": EventTypes.WORKSPACE_TEST_STARTED,
        "test_completed": EventTypes.WORKSPACE_TEST_COMPLETED,
        "snapshot_created": EventTypes.WORKSPACE_SNAPSHOT_CREATED,
        "artifact": EventTypes.ARTIFACT_CREATED,
        "approval_required": EventTypes.APPROVAL_REQUESTED,
    }
    return aliases.get(event_type, event_type)


def _update_session_from_event(session: AgentSession, event: AgentEvent) -> None:
    if event.type == EventTypes.SESSION_COMPLETED:
        session.status = "completed"
    elif event.type == EventTypes.SESSION_FAILED:
        session.status = "failed"
    elif event.type == EventTypes.SESSION_CANCELLED:
        session.status = "cancelled"
    elif event.type == EventTypes.APPROVAL_REQUESTED:
        session.status = "waiting"
    elif event.type in {
        EventTypes.SESSION_STARTED,
        EventTypes.CLOUD_AGENT_STATUS_CHANGED,
        EventTypes.CLOUD_AGENT_LOG,
        EventTypes.MODEL_TEXT_DELTA,
    }:
        if session.status != "waiting":
            session.status = "running"


def _collect_result_artifacts(session: _SDKSession) -> None:
    final_output = getattr(session.result, "final_output", None)
    if final_output is not None:
        session.artifacts.append(
            {
                "id": f"art_{uuid4().hex}",
                "type": "log",
                "name": "final_output.txt",
                "data": str(final_output),
            }
        )
    last_response_id = getattr(session.result, "last_response_id", None)
    if last_response_id is not None:
        session.artifacts.append(
            {
                "id": f"art_{uuid4().hex}",
                "type": "provider_ref",
                "name": "last_response_id",
                "data": last_response_id,
            }
        )


def _append_event(session: _SDKSession, event: dict[str, Any]) -> None:
    event.setdefault("id", f"evt_{uuid4().hex}")
    event.setdefault("provider_session_id", session.id)
    session.events.append(event)


def _events_after(
    events: list[dict[str, Any]],
    after_event_id: str | None,
) -> list[dict[str, Any]]:
    if after_event_id is None:
        return list(events)
    for index, event in enumerate(events):
        if event.get("id") == after_event_id:
            return events[index + 1 :]
    return []


def _result_to_input_list(result: Any) -> list[Any] | None:
    to_input_list = getattr(result, "to_input_list", None)
    if not callable(to_input_list):
        return None
    value = to_input_list()
    return list(value) if isinstance(value, list) else None


def _approval_id(interruption: Any) -> str:
    data = _as_dict(interruption)
    value = _first_str(data, "id", "tool_call_id", "call_id")
    if value is not None:
        return f"approval_{value}"
    name = data.get("name") or data.get("tool_name") or "tool"
    return f"approval_{name}_{uuid4().hex}"


def _provider_session_id(session: AgentSession) -> str:
    value = session.metadata.get("provider_session_id")
    return str(value) if value is not None else session.id


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: getattr(value, field.name) for field in fields(value)}
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        dumped = dump()
        if isinstance(dumped, dict):
            return dumped
    if hasattr(value, "__dict__"):
        return dict(vars(value))
    try:
        return {
            name: getattr(value, name)
            for name in dir(value)
            if not name.startswith("_")
            and not inspect.ismethod(getattr(value, name))
            and not inspect.isfunction(getattr(value, name))
        }
    except Exception:
        return {}


def _first_str(data: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = data.get(key)
        if isinstance(value, str):
            return value
    return None
