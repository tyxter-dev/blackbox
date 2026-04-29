"""Claude Agent SDK-backed agent provider.

Targets the public Claude Code / Agent SDK surface (headless mode, file
operations, code execution, web search, MCP extensibility, permissions,
session management). The earlier name ``AnthropicManagedAgentProvider`` was
speculative — there is no public "Anthropic Managed Agents" API matching the
agent/environment/session/events abstraction that name implied. A deprecated
alias is preserved at the bottom of this module for now.
"""
from __future__ import annotations

import asyncio
import importlib
import inspect
import os
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field, is_dataclass, replace
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
class ClaudeCodeClient(Protocol):
    """Minimal client contract used by ``ClaudeCodeAgentProvider``.

    The production SDK wrapper and tests can both satisfy this protocol. Native
    SDK payloads are intentionally kept behind this boundary so the public
    runtime contract stays stable while the provider SDK evolves.
    """

    async def create_agent(self, spec: AgentSpec) -> Any: ...

    async def start_session(self, agent: AgentRef | str, task: TaskSpec) -> Any:
        """Start a Claude Code session and return the native session payload."""
        ...

    def stream_events(
        self,
        provider_session_id: str,
        *,
        after_event_id: str | None = None,
    ) -> AsyncIterator[Any]:
        """Stream native Claude Code events for a provider session."""
        ...

    async def send_message(self, provider_session_id: str, message: str) -> Any:
        """Send a follow-up message to a provider session."""
        ...

    async def approve(self, approval_id: str, decision: ApprovalDecision) -> Any:
        """Resolve a pending Claude Code approval request."""
        ...

    async def cancel(self, provider_session_id: str) -> Any: ...

    async def list_artifacts(
        self,
        provider_session_id: str,
        *,
        type: str | None = None,
        after: str | None = None,
        limit: int = 100,
    ) -> Any:
        """Return native artifact data for a provider session."""
        ...


class ClaudeCodeAgentProvider:
    """Agent SDK-backed Claude Code provider.

    The provider is operational when either:

    - constructed with an object matching ``ClaudeCodeClient``; or
    - constructed with credentials and the optional ``claude-agent-sdk`` package
      installed.
    """

    provider_id = "claude-code"

    def __init__(
        self,
        api_key: str | None = None,
        *,
        client: ClaudeCodeClient | None = None,
        sdk_module: Any | None = None,
    ) -> None:
        self.api_key = api_key
        self._client = client
        self._sdk_module = sdk_module
        self._agents: dict[str, AgentRef] = {}
        self._sessions: dict[str, AgentSession] = {}

    def capabilities(self) -> AgentCapabilities:
        if self._client is None and not (
            self.api_key or self._sdk_module is not None or os.environ.get("ANTHROPIC_API_KEY")
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
        if not self.api_key and not os.environ.get("ANTHROPIC_API_KEY"):
            raise ProviderNotConfiguredError(
                "ClaudeCodeAgentProvider requires an api_key or ANTHROPIC_API_KEY."
            )
        self._client = ClaudeAgentSDKClient(
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
            status=_session_status(session.metadata.get("status", "running")),
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
    if runtime_session_id is not None:
        metadata.setdefault("runtime_session_id", runtime_session_id)
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
        "file_read": EventTypes.WORKSPACE_FILE_READ,
        "file_changed": EventTypes.WORKSPACE_FILE_CHANGED,
        "command_started": EventTypes.WORKSPACE_COMMAND_STARTED,
        "command_output": EventTypes.WORKSPACE_COMMAND_OUTPUT,
        "command_completed": EventTypes.WORKSPACE_COMMAND_COMPLETED,
        "test_started": EventTypes.WORKSPACE_TEST_STARTED,
        "test_completed": EventTypes.WORKSPACE_TEST_COMPLETED,
        "snapshot_created": EventTypes.WORKSPACE_SNAPSHOT_CREATED,
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
    if value is not None:
        return str(value)
    value = session.metadata.get("runtime_session_id")
    return str(value) if value is not None else session.id


def _session_status(value: Any) -> Any:
    if value in {"created", "running", "waiting", "completed", "failed", "cancelled"}:
        return value
    return "running"


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: getattr(value, field.name) for field in value.__dataclass_fields__.values()}
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


@dataclass(slots=True)
class _SDKSession:
    id: str
    client: Any
    task: TaskSpec
    queue: asyncio.Queue[dict[str, Any] | None] = field(default_factory=asyncio.Queue)
    events: list[dict[str, Any]] = field(default_factory=list)
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    approvals: dict[str, asyncio.Future[ApprovalDecision]] = field(default_factory=dict)
    pump_task: asyncio.Task[None] | None = None
    terminal: bool = False


class ClaudeAgentSDKClient:
    """Adapter from ``claude-agent-sdk`` to ``ClaudeCodeClient``.

    The official SDK exposes a continuous ``ClaudeSDKClient`` with query,
    streaming, interrupts, sessions, built-in coding tools, MCP configuration,
    and permission callbacks. This class maps that surface onto the runtime's
    provider/session contract while keeping the SDK import optional.
    """

    def __init__(self, *, api_key: str | None = None, sdk_module: Any | None = None) -> None:
        self.api_key = api_key
        self._sdk = sdk_module
        self._agents: dict[str, AgentSpec] = {}
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
        agent_id = spec.metadata.get("id") or f"agent_{spec.name}"
        self._agents[str(agent_id)] = spec
        return {
            "id": str(agent_id),
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
        session_id = f"sess_{uuid4().hex}"
        spec = self._agent_spec(agent)
        managed = _SDKSession(id=session_id, client=None, task=task)
        options = self._build_options(sdk, spec, task, managed)
        sdk_client = sdk.ClaudeSDKClient(options=options)
        managed.client = sdk_client

        connect = getattr(sdk_client, "connect", None)
        if callable(connect):
            await connect()
        await sdk_client.query(task.prompt)

        server_info = await _maybe_await(getattr(sdk_client, "get_server_info", lambda: None)())
        provider_session_id = _session_id_from_server_info(server_info) or session_id
        if provider_session_id != session_id:
            managed.id = provider_session_id
        self._sessions[managed.id] = managed
        self._sessions[session_id] = managed

        return {
            "id": managed.id,
            "runtime_session_id": session_id,
            "provider_session_id": managed.id,
            "status": "running",
            "metadata": {
                "agent_id": spec.name if spec is not None else str(getattr(agent, "id", agent)),
                "task": task.prompt,
                "server_info": server_info,
            },
        }

    async def stream_events(
        self,
        provider_session_id: str,
        *,
        after_event_id: str | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        session = self._resolve(provider_session_id)
        if after_event_id is not None:
            for event in _events_after(session.events, after_event_id):
                yield event
            if session.terminal:
                return

        self._ensure_pump(session)
        while True:
            queued = await session.queue.get()
            if queued is None:
                return
            if after_event_id is None or _event_is_after(session.events, queued, after_event_id):
                yield queued

    async def send_message(self, provider_session_id: str, message: str) -> dict[str, Any]:
        session = self._resolve(provider_session_id)
        if session.pump_task is not None and not session.pump_task.done():
            raise UnsupportedFeatureError(
                "Cannot send a Claude Agent SDK follow-up while the current response is streaming."
            )
        session.terminal = False
        await session.client.query(message)
        return {"id": f"inv_{uuid4().hex}", "provider_session_id": provider_session_id}

    async def approve(self, approval_id: str, decision: ApprovalDecision) -> None:
        seen: set[int] = set()
        for session in self._sessions.values():
            identity = id(session)
            if identity in seen:
                continue
            seen.add(identity)
            future = session.approvals.get(approval_id)
            if future is not None:
                if not future.done():
                    future.set_result(decision)
                return
        raise UnsupportedFeatureError(f"Unknown Claude Agent SDK approval request {approval_id!r}.")

    async def cancel(self, provider_session_id: str) -> None:
        session = self._resolve(provider_session_id)
        interrupt = getattr(session.client, "interrupt", None)
        if not callable(interrupt):
            raise UnsupportedFeatureError("Claude Agent SDK client does not expose interrupt().")
        await interrupt()
        await self._put_event(session, {"type": "cancelled"})

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
            return self._sdk
        try:
            self._sdk = importlib.import_module("claude_agent_sdk")
        except ModuleNotFoundError as exc:
            raise UnsupportedFeatureError(
                "ClaudeCodeAgentProvider requires the optional claude-agent-sdk package. "
                "Install agent-runtime-core[claude-agent] or pass client=...."
            ) from exc
        return self._sdk

    def _agent_spec(self, agent: AgentRef | str) -> AgentSpec | None:
        agent_id = agent.id if isinstance(agent, AgentRef) else str(agent)
        return self._agents.get(agent_id)

    def _build_options(
        self,
        sdk: Any,
        spec: AgentSpec | None,
        task: TaskSpec,
        session: _SDKSession,
    ) -> Any:
        """Build Claude Agent SDK options from runtime agent, task, and permission state."""
        options_cls = getattr(sdk, "ClaudeAgentOptions", None)
        if options_cls is None:
            raise UnsupportedFeatureError("claude-agent-sdk does not expose ClaudeAgentOptions.")

        permissions = dict(spec.permissions if spec is not None else {})
        env = dict(spec.environment if spec is not None else {})
        if self.api_key is not None:
            env.setdefault("ANTHROPIC_API_KEY", self.api_key)

        kwargs: dict[str, Any] = {
            "include_partial_messages": True,
            "env": env,
            "can_use_tool": self._permission_callback(session, sdk),
        }
        if spec is not None:
            if spec.instructions:
                kwargs["system_prompt"] = spec.instructions
            if spec.tools:
                kwargs["allowed_tools"] = list(spec.tools)
            if spec.model is not None:
                kwargs["model"] = spec.model
            if spec.mcp_servers:
                kwargs["mcp_servers"] = _coerce_mcp_servers(spec.mcp_servers)
        if task.model is not None:
            kwargs["model"] = task.model

        workspace_root = _workspace_root(task.workspace)
        if workspace_root is not None:
            kwargs["cwd"] = workspace_root

        for key in (
            "tools",
            "allowed_tools",
            "disallowed_tools",
            "permission_mode",
            "permission_prompt_tool_name",
            "max_turns",
            "max_budget_usd",
            "fallback_model",
            "output_format",
            "cli_path",
            "settings",
            "add_dirs",
            "extra_args",
            "sandbox",
            "enable_file_checkpointing",
        ):
            if key in permissions:
                kwargs[key] = permissions[key]
            elif key in task.extra:
                kwargs[key] = task.extra[key]

        resume = task.extra.get("resume") or task.metadata.get("resume")
        if isinstance(resume, str):
            kwargs["resume"] = resume

        return options_cls(**kwargs)

    def _permission_callback(
        self,
        session: _SDKSession,
        sdk: Any,
    ) -> Callable[[str, dict[str, Any], Any], Any]:
        async def can_use_tool(
            tool_name: str,
            input_data: dict[str, Any],
            context: Any,
        ) -> Any:
            approval_id = _approval_id(tool_name, context)
            loop = asyncio.get_running_loop()
            future: asyncio.Future[ApprovalDecision] = loop.create_future()
            session.approvals[approval_id] = future
            await self._put_event(
                session,
                {
                    "id": f"evt_{uuid4().hex}",
                    "type": "approval_required",
                    "approval_id": approval_id,
                    "action": tool_name,
                    "arguments": input_data,
                },
            )
            decision = await future
            if decision.approved:
                return _permission_allow(sdk, input_data)
            return _permission_deny(sdk, decision.reason or "Denied by runtime approval.")

        return can_use_tool

    def _ensure_pump(self, session: _SDKSession) -> None:
        if session.pump_task is None or session.pump_task.done():
            session.pump_task = asyncio.create_task(self._pump(session))

    async def _pump(self, session: _SDKSession) -> None:
        try:
            async for message in session.client.receive_response():
                for event in self._events_from_sdk_message(session, message):
                    await self._put_event(session, event)
        except Exception as exc:  # pragma: no cover - defensive boundary
            await self._put_event(
                session,
                {
                    "type": "failed",
                    "error": str(exc),
                    "name": type(exc).__name__,
                },
            )
        finally:
            session.terminal = True
            await session.queue.put(None)

    async def _put_event(self, session: _SDKSession, event: dict[str, Any]) -> None:
        event.setdefault("id", f"evt_{uuid4().hex}")
        event.setdefault("provider_session_id", session.id)
        session.events.append(event)
        await session.queue.put(event)

    def _events_from_sdk_message(
        self,
        session: _SDKSession,
        message: Any,
    ) -> list[dict[str, Any]]:
        data = _as_dict(message)
        class_name = type(message).__name__
        base = {"message_type": class_name, "message": data}

        if class_name == "StreamEvent" or "event" in data:
            return _events_from_stream_event(data, base)
        if class_name == "AssistantMessage" or "content" in data:
            return _events_from_assistant_message(session, data, base)
        if class_name == "ResultMessage" or "is_error" in data:
            return _events_from_result_message(session, data, base)
        if class_name in {"TaskStartedMessage", "TaskProgressMessage", "TaskNotificationMessage"}:
            return _events_from_task_message(session, data, base)
        if class_name == "RateLimitEvent" or "rate_limit_info" in data:
            return [{"type": EventTypes.CLOUD_AGENT_STATUS_CHANGED, "data": base}]
        if class_name == "SystemMessage" or "subtype" in data:
            return [{"type": EventTypes.CLOUD_AGENT_STATUS_CHANGED, "status": data.get("subtype"), "data": base}]
        return [{"type": EventTypes.CLOUD_AGENT_LOG, "data": base}]

    def _resolve(self, provider_session_id: str) -> _SDKSession:
        session = self._sessions.get(provider_session_id)
        if session is None:
            raise UnsupportedFeatureError(
                f"Claude Agent SDK session {provider_session_id!r} is not active in this process."
            )
        return session


def _events_from_stream_event(data: dict[str, Any], base: dict[str, Any]) -> list[dict[str, Any]]:
    event = data.get("event")
    if not isinstance(event, dict):
        return [{"type": EventTypes.CLOUD_AGENT_LOG, "data": base}]
    event_type = event.get("type")
    if event_type == "content_block_delta":
        delta = event.get("delta") or {}
        if isinstance(delta, dict) and delta.get("type") == "text_delta":
            return [{"type": EventTypes.MODEL_TEXT_DELTA, "message": delta.get("text", ""), "data": base}]
    if event_type == "content_block_start":
        block = event.get("content_block") or {}
        if isinstance(block, dict) and block.get("type") == "tool_use":
            return [_tool_event(block, started=False, base=base)]
    return [{"type": EventTypes.CLOUD_AGENT_LOG, "data": base | {"stream_event": event}}]


def _events_from_assistant_message(
    session: _SDKSession,
    data: dict[str, Any],
    base: dict[str, Any],
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for block in data.get("content") or []:
        block_data = _as_dict(block)
        if "text" in block_data:
            events.append({"type": EventTypes.CLOUD_AGENT_LOG, "message": block_data["text"], "data": base})
        elif {"id", "name", "input"}.issubset(block_data):
            events.append(_tool_event(block_data, started=True, base=base))
            _record_artifact_for_tool_use(session, block_data)
        elif "tool_use_id" in block_data:
            events.append(
                {
                    "type": EventTypes.HOSTED_TOOL_CALL_COMPLETED,
                    "item_id": block_data.get("tool_use_id"),
                    "result": block_data.get("content"),
                    "data": base,
                }
            )
    if not events:
        events.append({"type": EventTypes.CLOUD_AGENT_LOG, "data": base})
    return events


def _events_from_result_message(
    session: _SDKSession,
    data: dict[str, Any],
    base: dict[str, Any],
) -> list[dict[str, Any]]:
    result = data.get("result")
    if result is not None:
        session.artifacts.append(
            {
                "id": f"art_{uuid4().hex}",
                "type": "log",
                "name": "result.txt",
                "data": result,
                "metadata": base,
            }
        )
    return [
        {
            "type": "failed" if data.get("is_error") else "completed",
            "status": data.get("subtype"),
            "message": result,
            "data": base,
        }
    ]


def _events_from_task_message(
    session: _SDKSession,
    data: dict[str, Any],
    base: dict[str, Any],
) -> list[dict[str, Any]]:
    output_file = data.get("output_file")
    if isinstance(output_file, str) and output_file:
        session.artifacts.append(
            {
                "id": f"art_{uuid4().hex}",
                "type": "file",
                "name": output_file,
                "uri": output_file,
                "metadata": base,
            }
        )
    return [{"type": EventTypes.CLOUD_AGENT_STATUS_CHANGED, "status": data.get("status"), "data": base}]


def _tool_event(block: dict[str, Any], *, started: bool, base: dict[str, Any]) -> dict[str, Any]:
    name = str(block.get("name", "tool"))
    raw_arguments = block.get("input")
    arguments: dict[str, Any] = raw_arguments if isinstance(raw_arguments, dict) else {}
    if name == "Bash":
        return {
            "type": EventTypes.WORKSPACE_COMMAND_STARTED,
            "item_id": block.get("id"),
            "tool": name,
            "arguments": arguments,
            "data": base,
        }
    if name in {"Read", "Glob", "Grep"}:
        return {
            "type": EventTypes.WORKSPACE_FILE_READ,
            "item_id": block.get("id"),
            "tool": name,
            "arguments": arguments,
            "data": base,
        }
    if name in {"Write", "Edit", "MultiEdit", "NotebookEdit"}:
        return {
            "type": EventTypes.WORKSPACE_FILE_CHANGED,
            "item_id": block.get("id"),
            "tool": name,
            "path": arguments.get("file_path"),
            "arguments": arguments,
            "data": base,
        }
    return {
        "type": EventTypes.HOSTED_TOOL_CALL_STARTED if started else EventTypes.HOSTED_TOOL_CALL_REQUESTED,
        "item_id": block.get("id"),
        "tool": name,
        "arguments": arguments,
        "data": base,
    }


def _record_artifact_for_tool_use(session: _SDKSession, block: dict[str, Any]) -> None:
    name = block.get("name")
    input_data = block.get("input")
    if not isinstance(input_data, dict):
        return
    path = input_data.get("file_path")
    if isinstance(path, str) and name in {"Write", "Edit", "MultiEdit", "NotebookEdit"}:
        session.artifacts.append(
            {
                "id": f"art_{uuid4().hex}",
                "type": "file_change",
                "name": path,
                "uri": path,
                "metadata": {"tool": name, "input": input_data},
            }
        )


def _events_after(events: list[dict[str, Any]], after_event_id: str) -> list[dict[str, Any]]:
    for index, event in enumerate(events):
        if event.get("id") == after_event_id:
            return events[index + 1 :]
    return []


def _event_is_after(events: list[dict[str, Any]], event: dict[str, Any], after_event_id: str) -> bool:
    if not after_event_id:
        return True
    after_events = _events_after(events, after_event_id)
    return event in after_events


def _coerce_mcp_servers(value: list[Any]) -> Any:
    if len(value) == 1 and isinstance(value[0], (str, dict)):
        return value[0]
    return {
        str(index): server
        for index, server in enumerate(value)
    }


def _workspace_root(workspace: Any) -> str | None:
    if workspace is None:
        return None
    root = getattr(workspace, "root", None)
    if isinstance(root, str):
        return root
    if isinstance(workspace, dict):
        value = workspace.get("root")
        return value if isinstance(value, str) else None
    return None


def _session_id_from_server_info(server_info: Any) -> str | None:
    if server_info is None:
        return None
    data = _as_dict(server_info)
    return _first_str(data, "session_id", "sessionId", "id")


def _approval_id(tool_name: str, context: Any) -> str:
    data = _as_dict(context)
    value = _first_str(data, "tool_use_id", "toolUseId", "id")
    if value is not None:
        return f"approval_{value}"
    return f"approval_{tool_name}_{uuid4().hex}"


def _permission_allow(sdk: Any, input_data: dict[str, Any]) -> Any:
    cls = _sdk_type(sdk, "PermissionResultAllow")
    if cls is not None:
        return cls(updated_input=input_data)
    return {"behavior": "allow", "updatedInput": input_data}


def _permission_deny(sdk: Any, message: str) -> Any:
    cls = _sdk_type(sdk, "PermissionResultDeny")
    if cls is not None:
        return cls(message=message, interrupt=False)
    return {"behavior": "deny", "message": message, "interrupt": False}


def _sdk_type(sdk: Any, name: str) -> Any | None:
    value = getattr(sdk, name, None)
    if value is not None:
        return value
    try:
        types_module = importlib.import_module("claude_agent_sdk.types")
    except ModuleNotFoundError:
        return None
    return getattr(types_module, name, None)


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


# Deprecated alias preserved temporarily so existing imports keep working.
AnthropicManagedAgentProvider = ClaudeCodeAgentProvider
