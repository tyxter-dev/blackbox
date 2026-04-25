"""Claude Code SDK-backed agent provider (scaffold).

Targets the public Claude Code / Agent SDK surface (headless mode, file
operations, code execution, web search, MCP extensibility, permissions,
session management). The earlier name ``AnthropicManagedAgentProvider`` was
speculative — there is no public "Anthropic Managed Agents" API matching the
agent/environment/session/events abstraction that name implied. A deprecated
alias is preserved at the bottom of this module for now.
"""
from __future__ import annotations

import inspect
from collections.abc import AsyncIterator
from dataclasses import replace
from typing import Any, Protocol, cast, runtime_checkable

from agent_runtime.core.approvals import ApprovalDecision
from agent_runtime.core.artifacts import Artifact, ArtifactPage
from agent_runtime.core.capabilities import AgentCapabilities
from agent_runtime.core.errors import ProviderNotConfiguredError, UnsupportedFeatureError
from agent_runtime.core.events import AgentEvent, EventTypes
from agent_runtime.core.sessions import AgentRef, AgentSession, InvocationRef, SessionRef
from agent_runtime.providers.base import AgentSpec, TaskSpec


@runtime_checkable
class ClaudeCodeClient(Protocol):
    """Minimal client contract used by ``ClaudeCodeAgentProvider``.

    The production SDK wrapper and tests can both satisfy this protocol. Native
    SDK payloads are intentionally kept behind this boundary so the public
    runtime contract stays stable while the provider SDK evolves.
    """

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


class ClaudeCodeAgentProvider:
    """Agent SDK-backed Claude Code provider.

    The provider is fully operational when constructed with an object matching
    ``ClaudeCodeClient``. Constructing it with credentials only preserves the
    existing honest-scaffold behavior until a stable public SDK wrapper is wired
    into ``_get_client``.
    """

    provider_id = "claude-code"

    def __init__(
        self,
        api_key: str | None = None,
        *,
        client: ClaudeCodeClient | None = None,
    ) -> None:
        self.api_key = api_key
        self._client = client
        self._agents: dict[str, AgentRef] = {}
        self._sessions: dict[str, AgentSession] = {}

    def capabilities(self) -> AgentCapabilities:
        if self._client is None:
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
            supports_mcp=False,
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
        session = _coerce_session(
            raw,
            provider=self.provider_id,
            agent=agent,
            task=task,
        )
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
        provider_session_id = _provider_session_id(runtime_session)
        async for raw_event in client.stream_events(
            provider_session_id,
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

    def _get_client(self) -> ClaudeCodeClient:
        if self._client is not None:
            return self._client
        if not self.api_key:
            raise ProviderNotConfiguredError("ClaudeCodeAgentProvider requires an api_key.")
        raise UnsupportedFeatureError(
            "Claude Code SDK client is not configured. Pass client=... implementing "
            "ClaudeCodeClient to enable the provider."
        )

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
    if runtime_session_id is None:
        runtime_session_id = provider_session_id or ""
    metadata = dict(data.get("metadata") or {})
    if provider_session_id is not None:
        metadata.setdefault("provider_session_id", provider_session_id)
    provider_invocation_id = _first_str(data, "invocation_id", "provider_invocation_id")
    if provider_invocation_id is not None:
        metadata.setdefault("provider_invocation_id", provider_invocation_id)
    resume_token = _first_str(data, "resume_token")
    if resume_token is not None:
        metadata.setdefault("resume_token", resume_token)
    workspace = data.get("workspace")
    if isinstance(workspace, dict):
        metadata.setdefault("workspace", dict(workspace))
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
    return InvocationRef(
        provider=provider,
        session_id=session_id,
        id=invocation_id,
        metadata=metadata,
    )


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
        return replace(
            raw,
            provider=raw.provider or provider,
            session_id=raw.session_id or session_id,
        )
    data = _as_dict(raw)
    event_type = _canonical_event_type(str(data.get("type", data.get("event", ""))))
    event_data = dict(data.get("data") or {})
    for key in (
        "status",
        "message",
        "level",
        "path",
        "diff",
        "approval_id",
        "action",
        "arguments",
        "artifact",
        "tool",
        "name",
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
        "status": EventTypes.CLOUD_AGENT_STATUS_CHANGED,
        "log": EventTypes.CLOUD_AGENT_LOG,
        "checkpoint": EventTypes.CLOUD_AGENT_CHECKPOINT_CREATED,
        "file_changed": EventTypes.WORKSPACE_FILE_CHANGED,
        "patch": EventTypes.WORKSPACE_PATCH_CREATED,
        "artifact": EventTypes.ARTIFACT_CREATED,
        "approval_required": EventTypes.APPROVAL_REQUESTED,
        "completed": EventTypes.SESSION_COMPLETED,
        "failed": EventTypes.SESSION_FAILED,
        "cancelled": EventTypes.SESSION_CANCELLED,
    }
    return aliases.get(event_type, event_type or EventTypes.CLOUD_AGENT_LOG)


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
    }:
        if session.status != "waiting":
            session.status = "running"


def _provider_session_id(session: AgentSession) -> str:
    value = session.metadata.get("provider_session_id")
    return str(value) if value is not None else session.id


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        dumped = dump()
        if isinstance(dumped, dict):
            return dumped
    if hasattr(value, "__dict__"):
        return dict(vars(value))
    fields = {
        name: getattr(value, name)
        for name in dir(value)
        if not name.startswith("_")
        and not inspect.ismethod(getattr(value, name))
        and not inspect.isfunction(getattr(value, name))
    }
    return fields


def _first_str(data: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = data.get(key)
        if isinstance(value, str):
            return value
    return None


# Deprecated alias preserved temporarily so existing imports keep working.
AnthropicManagedAgentProvider = ClaudeCodeAgentProvider
