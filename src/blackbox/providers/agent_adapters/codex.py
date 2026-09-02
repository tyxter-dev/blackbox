"""Codex app-server-backed :class:`AgentProvider`.

The adapter intentionally uses Codex's app-server session protocol, rather
than ``codex exec``.  Threads, turns, streamed items, native approvals, and
interruption are therefore retained as provider-native state.  Authentication
is deliberately left to the pinned Codex runtime: it reuses and refreshes the
user's existing Codex / ChatGPT subscription credential without Blackbox
reading, copying, or supplying it.
"""
from __future__ import annotations

import asyncio
import importlib
import json
import os
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Protocol, cast, runtime_checkable
from uuid import uuid4

from blackbox.core.approvals import ApprovalDecision
from blackbox.core.artifacts import Artifact, ArtifactPage
from blackbox.core.capabilities import AgentCapabilities
from blackbox.core.errors import (
    ProviderExecutionError,
    SessionBusyError,
    SessionNotFoundError,
    UnsupportedFeatureError,
)
from blackbox.core.events import AgentEvent, EventTypes
from blackbox.core.sessions import AgentRef, AgentSession, InvocationRef, SessionRef
from blackbox.providers.base import AgentSpec, TaskSpec

CODEX_SDK_VERSION = "0.147.0"
"""The ``openai-codex`` SDK version used by this app-server adapter."""

CODEX_CANCEL_GRACE_SECONDS = 2.0
CODEX_PROCESS_KILL_GRACE_SECONDS = 0.5

_APPROVAL_METHODS = frozenset(
    {
        "item/commandExecution/requestApproval",
        "item/fileChange/requestApproval",
    }
)


@runtime_checkable
class CodexAppServerClient(Protocol):
    """Minimal native Codex client boundary used by the provider and tests."""

    async def create_agent(self, spec: AgentSpec) -> Any: ...

    async def start_session(self, agent: AgentRef | str, task: TaskSpec) -> Any:
        """Start a Codex thread and its first turn."""
        ...

    def stream_events(
        self,
        provider_session_id: str,
        *,
        after_event_id: str | None = None,
    ) -> AsyncIterator[Any]:
        """Stream raw app-server notifications for one Codex turn."""
        ...

    async def send_message(self, provider_session_id: str, message: str) -> Any:
        """Start a follow-up turn, or steer the active one."""
        ...

    async def approve(self, approval_id: str, decision: ApprovalDecision) -> Any:
        """Resolve a pending app-server command or file-change approval."""
        ...

    async def cancel(self, provider_session_id: str) -> Any:
        """Interrupt an active turn and shut down its bounded local process."""
        ...

    async def list_artifacts(
        self,
        provider_session_id: str,
        *,
        type: str | None = None,
        after: str | None = None,
        limit: int = 100,
    ) -> Any:
        """Return file-change artifacts observed in the current process."""
        ...


class CodexAgentProvider:
    """Provider-native Codex coding-agent sessions backed by app-server.

    Install ``blackbox[codex]`` and sign in with Codex once.  The optional SDK
    ships a version-pinned CLI; this adapter launches that binary and lets it
    own subscription authentication.  It never reads ``CODEX_HOME`` or parses
    auth files.
    """

    provider_id = "codex"
    provider_aliases = ("codex-app-server", "codex_app_server")

    def __init__(
        self,
        *,
        client: CodexAppServerClient | None = None,
        codex_bin: str | None = None,
    ) -> None:
        self._client = client
        self._codex_bin = codex_bin
        self._agents: dict[str, AgentRef] = {}
        self._sessions: dict[str, AgentSession] = {}

    def capabilities(self) -> AgentCapabilities:
        advertised = getattr(self._client, "capabilities", None)
        if callable(advertised):
            capabilities = advertised()
            if isinstance(capabilities, AgentCapabilities):
                return capabilities
            if isinstance(capabilities, dict):
                return AgentCapabilities(**capabilities)
        # Auth is intentionally not inspected: the Codex runtime owns the
        # subscription credential and can refresh it while starting a thread.
        return AgentCapabilities(
            supports_sessions=True,
            supports_streaming_events=True,
            supports_artifacts=True,
            supports_workspace=True,
            supports_approvals=True,
            supports_cancellation=True,
        )

    async def create_agent(self, spec: AgentSpec) -> AgentRef:
        _validate_agent_spec(spec)
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
        self,
        session: SessionRef | AgentSession,
        message: str,
    ) -> InvocationRef:
        client = self._get_client()
        runtime_session = self._resolve_session(session)
        raw = await client.send_message(_provider_session_id(runtime_session), message)
        runtime_session.status = "running"
        return _coerce_invocation_ref(raw, provider=self.provider_id, session_id=runtime_session.id)

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

    async def close(self) -> None:
        """Close live app-server child processes owned by this provider."""

        close = getattr(self._client, "close", None)
        if callable(close):
            result = close()
            if hasattr(result, "__await__"):
                await result

    def _get_client(self) -> CodexAppServerClient:
        if self._client is None:
            self._client = CodexPythonSDKClient(codex_bin=self._codex_bin)
        return self._client

    def _resolve_session(self, session: SessionRef | AgentSession) -> AgentSession:
        if isinstance(session, AgentSession):
            self._sessions.setdefault(session.id, session)
            return session
        existing = self._sessions.get(session.id)
        if existing is not None:
            return existing
        raise SessionNotFoundError(
            "Codex sessions are process-local and cannot be resumed after the app-server "
            "process is gone.",
            session_id=session.id,
            provider=self.provider_id,
            operation="stream_events",
            safe_to_retry=False,
        )


@dataclass(slots=True)
class _PendingApproval:
    method: str
    params: dict[str, Any]
    decision: asyncio.Future[ApprovalDecision]


@dataclass(slots=True)
class _CodexSession:
    thread_id: str
    agent_id: str
    task: TaskSpec
    spec: AgentSpec
    connection: _CodexAppServerConnection
    current_turn_id: str | None
    events: list[dict[str, Any]] = field(default_factory=list)
    condition: asyncio.Condition = field(default_factory=asyncio.Condition)
    approvals: dict[str, _PendingApproval] = field(default_factory=dict)
    artifacts: list[Artifact] = field(default_factory=list)
    current_turn_start_index: int = 0
    closed: bool = False


class CodexPythonSDKClient:
    """Raw JSON-RPC bridge using the runtime bundled by ``openai-codex``.

    The public SDK guarantees the pinned executable, but its high-level helper
    has auto-approval convenience modes.  This narrow bridge uses the bundled
    runtime directly so Blackbox can expose app-server approval pauses through
    the normal :class:`ApprovalDecision` contract.
    """

    def __init__(self, *, codex_bin: str | None = None) -> None:
        self._codex_bin = codex_bin
        self._bundled_path_dir: str | None = None
        self._agents: dict[str, AgentSpec] = {}
        self._sessions: dict[str, _CodexSession] = {}

    def capabilities(self) -> AgentCapabilities:
        return AgentCapabilities(
            supports_sessions=True,
            supports_streaming_events=True,
            supports_artifacts=True,
            supports_workspace=True,
            supports_approvals=True,
            supports_cancellation=True,
        )

    async def create_agent(self, spec: AgentSpec) -> dict[str, Any]:
        _validate_agent_spec(spec)
        agent_id = str(spec.metadata.get("id") or f"codex_agent_{spec.name}")
        self._agents[agent_id] = spec
        return {
            "id": agent_id,
            "metadata": {
                "instructions": spec.instructions,
                "model": spec.model,
                "raw": spec,
            },
        }

    async def start_session(self, agent: AgentRef | str, task: TaskSpec) -> dict[str, Any]:
        agent_id = agent.id if isinstance(agent, AgentRef) else str(agent)
        try:
            spec = self._agents[agent_id]
        except KeyError as exc:
            raise UnsupportedFeatureError(f"Unknown Codex agent {agent_id!r}.") from exc

        connection = _CodexAppServerConnection(
            codex_bin=self._resolve_codex_bin(),
            environment=_process_environment(spec.environment, self._bundled_path_dir),
        )
        await connection.start()
        try:
            thread_result = await connection.request(
                "thread/start",
                _thread_start_params(spec, task),
            )
            thread_id = _nested_str(thread_result, "thread", "id")
            if thread_id is None:
                raise ProviderExecutionError("Codex app-server returned no thread id.")

            managed = _CodexSession(
                thread_id=thread_id,
                agent_id=agent_id,
                task=task,
                spec=spec,
                connection=connection,
                current_turn_id=None,
            )
            connection.bind(managed)
            await connection.append_event(
                {
                    "id": f"codex_evt_{uuid4().hex}",
                    "method": "blackbox/session/started",
                    "params": {"threadId": thread_id},
                }
            )
            await self._start_turn(managed, task.prompt, task)
            self._sessions[thread_id] = managed
            return {
                "id": thread_id,
                "provider_session_id": thread_id,
                "status": "running",
                "model": _selected_model(spec, task),
                "metadata": {
                    "agent_id": agent_id,
                    "task": task.prompt,
                    "thread_id": thread_id,
                    "ephemeral": _ephemeral(task),
                },
            }
        except BaseException:
            await connection.close()
            raise

    async def stream_events(
        self,
        provider_session_id: str,
        *,
        after_event_id: str | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        session = self._resolve(provider_session_id)
        cursor = _cursor_after(session.events, after_event_id)
        target_turn_id = session.current_turn_id
        if after_event_id is None:
            cursor = session.current_turn_start_index

        while True:
            async with session.condition:
                while (
                    cursor >= len(session.events)
                    and not session.closed
                    and not _turn_is_complete(session.events, target_turn_id)
                ):
                    await session.condition.wait()
                events = list(session.events[cursor:])
                cursor = len(session.events)
                closed = session.closed

            for event in events:
                yield event
                if _is_turn_completed(event, target_turn_id):
                    return
            if closed or _turn_is_complete(session.events, target_turn_id):
                return

    async def send_message(self, provider_session_id: str, message: str) -> dict[str, Any]:
        session = self._resolve(provider_session_id)
        if session.closed:
            raise SessionBusyError(
                "Codex app-server session is closed.",
                session_id=provider_session_id,
                provider="codex",
                operation="send_message",
                safe_to_retry=False,
            )
        turn_id: str | None
        if session.current_turn_id is not None and not _turn_is_complete(
            session.events, session.current_turn_id
        ):
            result = await session.connection.request(
                "turn/steer",
                {
                    "expectedTurnId": session.current_turn_id,
                    "input": [{"type": "text", "text": message}],
                    "threadId": session.thread_id,
                },
            )
            turn_id = _first_str(result, "turnId") or session.current_turn_id
        else:
            await self._start_turn(session, message, session.task)
            turn_id = session.current_turn_id
        if turn_id is None:  # pragma: no cover - defensive app-server boundary
            raise ProviderExecutionError("Codex app-server accepted a turn without a turn id.")
        return {"id": turn_id, "provider_session_id": provider_session_id}

    async def approve(self, approval_id: str, decision: ApprovalDecision) -> None:
        for session in self._sessions.values():
            pending = session.approvals.get(approval_id)
            if pending is None:
                continue
            if not pending.decision.done():
                pending.decision.set_result(decision)
            session.approvals.pop(approval_id, None)
            return
        raise UnsupportedFeatureError(f"Unknown Codex app-server approval request {approval_id!r}.")

    async def cancel(self, provider_session_id: str) -> None:
        session = self._resolve(provider_session_id)
        turn_id = session.current_turn_id
        if turn_id is not None and not _turn_is_complete(session.events, turn_id):
            await session.connection.request(
                "turn/interrupt",
                {"threadId": session.thread_id, "turnId": turn_id},
            )
            try:
                async with session.condition:
                    await asyncio.wait_for(
                        session.condition.wait_for(
                            lambda: _turn_is_complete(session.events, turn_id) or session.closed
                        ),
                        timeout=CODEX_CANCEL_GRACE_SECONDS,
                    )
            except TimeoutError:
                pass
        await session.connection.close()
        session.closed = True
        await _notify_session(session)
        if not _turn_is_complete(session.events, turn_id):
            await session.connection.append_event(
                {
                    "id": f"codex_evt_{uuid4().hex}",
                    "method": "blackbox/session/cancelled",
                    "params": {"threadId": session.thread_id, "turnId": turn_id},
                }
            )

    async def list_artifacts(
        self,
        provider_session_id: str,
        *,
        type: str | None = None,
        after: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        session = self._resolve(provider_session_id)
        start = _artifact_cursor(session.artifacts, after)
        items = [artifact for artifact in session.artifacts[start:] if type is None or artifact.type == type]
        return {
            "items": items[:limit],
            "has_more": len(items) > limit,
            "next_cursor": items[limit - 1].id if len(items) > limit and limit > 0 else None,
        }

    async def close(self) -> None:
        sessions = list({id(session): session for session in self._sessions.values()}.values())
        for session in sessions:
            await session.connection.close()
            session.closed = True
            await _notify_session(session)

    async def _start_turn(self, session: _CodexSession, prompt: str, task: TaskSpec) -> None:
        session.current_turn_start_index = len(session.events)
        result = await session.connection.request(
            "turn/start",
            _turn_start_params(session.thread_id, prompt, session.spec, task),
        )
        turn_id = _nested_str(result, "turn", "id")
        if turn_id is None:
            raise ProviderExecutionError("Codex app-server returned no turn id.")
        session.current_turn_id = turn_id

    def _resolve_codex_bin(self) -> str:
        if self._codex_bin is not None:
            if not Path(self._codex_bin).is_file():
                raise UnsupportedFeatureError(
                    f"Codex binary not found at {self._codex_bin!r}. Install blackbox[codex] "
                    "or provide a valid codex_bin."
                )
            return self._codex_bin
        try:
            sdk = importlib.import_module("openai_codex")
            if getattr(sdk, "__version__", None) != CODEX_SDK_VERSION:
                raise UnsupportedFeatureError(
                    "Codex app-server protocol mismatch: install "
                    f"blackbox[codex] (openai-codex=={CODEX_SDK_VERSION})."
                )
            runtime = importlib.import_module("codex_cli_bin")
            path = runtime.bundled_codex_path()
            path_dir = runtime.bundled_path_dir()
            self._bundled_path_dir = str(path_dir) if path_dir is not None else None
        except ModuleNotFoundError as exc:
            raise UnsupportedFeatureError(
                "CodexAgentProvider requires the optional openai-codex package. "
                "Install blackbox[codex] or pass client=...."
            ) from exc
        return str(path)

    def _resolve(self, provider_session_id: str) -> _CodexSession:
        try:
            return self._sessions[provider_session_id]
        except KeyError as exc:
            raise SessionNotFoundError(
                f"Codex app-server session {provider_session_id!r} is not active in this process.",
                session_id=provider_session_id,
                provider="codex",
                safe_to_retry=False,
            ) from exc


class _CodexAppServerConnection:
    """One pinned Codex app-server JSON-RPC connection over stdio."""

    def __init__(self, *, codex_bin: str, environment: dict[str, str]) -> None:
        self._codex_bin = codex_bin
        self._environment = environment
        self._process: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._write_lock = asyncio.Lock()
        self._session: _CodexSession | None = None
        self._stderr: list[str] = []
        self._closed = False

    async def start(self) -> None:
        if self._process is not None:
            return
        self._process = await asyncio.create_subprocess_exec(
            self._codex_bin,
            "app-server",
            "--listen",
            "stdio://",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=self._environment,
        )
        self._reader_task = asyncio.create_task(self._read_stdout())
        self._stderr_task = asyncio.create_task(self._drain_stderr())
        await self.request(
            "initialize",
            {
                "clientInfo": {
                    "name": "blackbox",
                    "title": "Blackbox Codex AgentProvider",
                    "version": "0.1.1",
                }
            },
        )
        await self.notify("initialized", {})

    def bind(self, session: _CodexSession) -> None:
        self._session = session

    async def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if self._closed:
            raise ProviderExecutionError("Codex app-server connection is closed.")
        request_id = f"blackbox_{uuid4().hex}"
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        try:
            await self._write({"id": request_id, "method": method, "params": params})
            return await future
        finally:
            self._pending.pop(request_id, None)

    async def notify(self, method: str, params: dict[str, Any]) -> None:
        await self._write({"method": method, "params": params})

    async def append_event(self, event: dict[str, Any]) -> None:
        session = self._session
        if session is None:
            return
        event.setdefault("id", f"codex_evt_{uuid4().hex}")
        session.events.append(event)
        _record_artifact(session, event)
        async with session.condition:
            session.condition.notify_all()

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        process = self._process
        self._process = None
        if process is not None:
            if process.stdin is not None:
                process.stdin.close()
            if process.returncode is None:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), timeout=CODEX_PROCESS_KILL_GRACE_SECONDS)
                except TimeoutError:
                    process.kill()
                    await process.wait()
        for task in (self._reader_task, self._stderr_task):
            if task is not None and not task.done():
                task.cancel()
        await asyncio.gather(
            *(task for task in (self._reader_task, self._stderr_task) if task is not None),
            return_exceptions=True,
        )
        self._reader_task = None
        self._stderr_task = None
        error = ProviderExecutionError("Codex app-server connection closed.")
        for future in self._pending.values():
            if not future.done():
                future.set_exception(error)
        self._pending.clear()

    async def _read_stdout(self) -> None:
        try:
            process = self._require_process()
            if process.stdout is None:  # pragma: no cover - subprocess invariant
                raise ProviderExecutionError("Codex app-server has no stdout pipe.")
            while line := await process.stdout.readline():
                raw = _decode_message(line)
                if "method" in raw and "id" in raw:
                    await self._handle_server_request(raw)
                elif "method" in raw:
                    await self._handle_notification(raw)
                else:
                    self._handle_response(raw)
            if not self._closed:
                raise ProviderExecutionError(self._closed_message("Codex app-server closed stdout."))
        except asyncio.CancelledError:
            raise
        except BaseException as exc:
            await self._fail(exc)

    async def _drain_stderr(self) -> None:
        process = self._require_process()
        if process.stderr is None:  # pragma: no cover - subprocess invariant
            return
        while line := await process.stderr.readline():
            self._stderr.append(line.decode("utf-8", errors="replace").rstrip())
            del self._stderr[:-40]

    async def _handle_server_request(self, raw: dict[str, Any]) -> None:
        method = raw.get("method")
        request_id = raw.get("id")
        params = raw.get("params")
        if not isinstance(method, str) or not isinstance(request_id, str):
            return
        if method not in _APPROVAL_METHODS or not isinstance(params, dict):
            await self._write(
                {
                    "error": {
                        "code": -32601,
                        "message": f"Blackbox refuses unsupported Codex server request {method!r}.",
                    },
                    "id": request_id,
                }
            )
            return

        session = self._session
        if session is None:
            await self._write(
                {
                    "error": {"code": -32000, "message": "No Blackbox session is bound."},
                    "id": request_id,
                }
            )
            return
        approval_id = _approval_id(method, params)
        decision = asyncio.get_running_loop().create_future()
        session.approvals[approval_id] = _PendingApproval(method, params, decision)
        await self.append_event(
            {
                "id": f"codex_evt_{uuid4().hex}",
                "method": "blackbox/approval/requested",
                "params": {
                    "action": "command" if "commandExecution" in method else "file_change",
                    "approvalId": approval_id,
                    "request": params,
                    "threadId": session.thread_id,
                },
                "raw_server_request": raw,
            }
        )
        resolved = await decision
        await self._write(
            {
                "id": request_id,
                "result": {"decision": "accept" if resolved.approved else "decline"},
            }
        )

    async def _handle_notification(self, raw: dict[str, Any]) -> None:
        session = self._session
        if session is None:
            return
        params = raw.get("params")
        if not isinstance(params, dict):
            return
        thread_id = params.get("threadId")
        if thread_id != session.thread_id:
            return
        raw.setdefault("id", f"codex_evt_{uuid4().hex}")
        if raw.get("method") == "turn/completed":
            turn = params.get("turn")
            if isinstance(turn, dict) and turn.get("id") == session.current_turn_id:
                session.current_turn_id = None
        await self.append_event(raw)

    def _handle_response(self, raw: dict[str, Any]) -> None:
        request_id = raw.get("id")
        if not isinstance(request_id, str):
            return
        future = self._pending.get(request_id)
        if future is None or future.done():
            return
        error = raw.get("error")
        if isinstance(error, dict):
            future.set_exception(
                ProviderExecutionError(str(error.get("message") or "Codex app-server request failed."))
            )
            return
        result = raw.get("result")
        if not isinstance(result, dict):
            future.set_exception(ProviderExecutionError("Codex app-server returned a non-object result."))
            return
        future.set_result(result)

    async def _fail(self, error: BaseException) -> None:
        failure = error if isinstance(error, ProviderExecutionError) else ProviderExecutionError(str(error))
        for future in self._pending.values():
            if not future.done():
                future.set_exception(failure)
        self._pending.clear()
        session = self._session
        if session is None:
            return
        session.closed = True
        await self.append_event(
            {
                "id": f"codex_evt_{uuid4().hex}",
                "method": "blackbox/session/failed",
                "params": {"error": str(failure), "threadId": session.thread_id},
            }
        )
        await _notify_session(session)

    async def _write(self, payload: dict[str, Any]) -> None:
        process = self._require_process()
        if process.stdin is None:  # pragma: no cover - subprocess invariant
            raise ProviderExecutionError("Codex app-server has no stdin pipe.")
        encoded = (json.dumps(payload, separators=(",", ":")) + "\n").encode()
        async with self._write_lock:
            process.stdin.write(encoded)
            await process.stdin.drain()

    def _require_process(self) -> asyncio.subprocess.Process:
        if self._process is None:
            raise ProviderExecutionError("Codex app-server process is not running.")
        return self._process

    def _closed_message(self, prefix: str) -> str:
        details = "\n".join(self._stderr[-40:])
        return f"{prefix} stderr_tail={details[:2000]}" if details else prefix


def _validate_agent_spec(spec: AgentSpec) -> None:
    if spec.tools:
        raise UnsupportedFeatureError(
            "CodexAgentProvider does not map Blackbox local tools into Codex app-server. "
            "Use Codex's native workspace tools instead."
        )
    if spec.hosted_tools:
        raise UnsupportedFeatureError(
            "CodexAgentProvider does not map Blackbox hosted tools into Codex app-server."
        )
    if spec.mcp_servers:
        raise UnsupportedFeatureError(
            "CodexAgentProvider does not map MCPServerSpec values into Codex app-server yet."
        )


def _thread_start_params(spec: AgentSpec, task: TaskSpec) -> dict[str, Any]:
    params: dict[str, Any] = {
        "approvalPolicy": "on-request",
        "approvalsReviewer": "user",
        "ephemeral": _ephemeral(task),
    }
    model = _selected_model(spec, task)
    if model is not None:
        params["model"] = model
    if spec.instructions:
        params["developerInstructions"] = spec.instructions
    workspace_root = _workspace_root(task.workspace)
    if workspace_root is not None:
        params["cwd"] = workspace_root
    sandbox = _sandbox_mode(spec, task)
    if sandbox is not None:
        params["sandbox"] = sandbox
    return params


def _turn_start_params(
    thread_id: str,
    prompt: str,
    spec: AgentSpec,
    task: TaskSpec,
) -> dict[str, Any]:
    params: dict[str, Any] = {
        "input": [{"type": "text", "text": prompt}],
        "threadId": thread_id,
    }
    model = _selected_model(spec, task)
    if model is not None:
        params["model"] = model
    workspace_root = _workspace_root(task.workspace)
    if workspace_root is not None:
        params["cwd"] = workspace_root
    sandbox = _sandbox_policy(spec, task)
    if sandbox is not None:
        params["sandboxPolicy"] = sandbox
    return params


def _selected_model(spec: AgentSpec, task: TaskSpec) -> str | None:
    return task.model if task.model is not None else spec.model


def _ephemeral(task: TaskSpec) -> bool:
    value = task.extra.get("ephemeral", True)
    if not isinstance(value, bool):
        raise ValueError("TaskSpec.extra['ephemeral'] must be a bool for CodexAgentProvider.")
    return value


def _sandbox_mode(spec: AgentSpec, task: TaskSpec) -> str | None:
    value = _codex_option("sandbox", spec, task)
    if value is None:
        return None
    modes = {
        "read-only": "read-only",
        "workspace-write": "workspace-write",
        "full-access": "danger-full-access",
    }
    try:
        return modes[value]
    except KeyError as exc:
        raise ValueError(
            "Codex sandbox must be 'read-only', 'workspace-write', or 'full-access'."
        ) from exc


def _sandbox_policy(spec: AgentSpec, task: TaskSpec) -> dict[str, str] | None:
    value = _codex_option("sandbox", spec, task)
    if value is None:
        return None
    policies = {
        "read-only": {"type": "readOnly"},
        "workspace-write": {"type": "workspaceWrite"},
        "full-access": {"type": "dangerFullAccess"},
    }
    try:
        return policies[value]
    except KeyError as exc:
        raise ValueError(
            "Codex sandbox must be 'read-only', 'workspace-write', or 'full-access'."
        ) from exc


def _codex_option(name: str, spec: AgentSpec, task: TaskSpec) -> str | None:
    value = task.extra.get(name, spec.permissions.get(name))
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"Codex option {name!r} must be a string.")
    return value


def _process_environment(
    environment: Mapping[str, Any],
    bundled_path_dir: str | None,
) -> dict[str, str]:
    result = dict(os.environ)
    # This provider is deliberately subscription-only.  Let the native Codex
    # login own its persisted, refreshable ChatGPT credentials rather than
    # silently switching the turn to API-key billing because a parent process
    # happens to export an OpenAI key.
    result.pop("OPENAI_API_KEY", None)
    for key, value in environment.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise ValueError("Codex AgentSpec.environment keys and values must be strings.")
        if key == "OPENAI_API_KEY":
            raise UnsupportedFeatureError(
                "CodexAgentProvider is subscription-only and does not accept OPENAI_API_KEY."
            )
        result[key] = value
    if bundled_path_dir is not None:
        path = result.get("PATH", "")
        entries = [entry for entry in path.split(os.pathsep) if entry != bundled_path_dir]
        result["PATH"] = os.pathsep.join([bundled_path_dir, *entries])
    return result


def _workspace_root(workspace: Any) -> str | None:
    if workspace is None:
        return None
    root = getattr(workspace, "root", None)
    if isinstance(root, str):
        return root
    if isinstance(workspace, Mapping):
        value = workspace.get("root")
        return value if isinstance(value, str) else None
    return None


def _decode_message(line: bytes) -> dict[str, Any]:
    try:
        payload = json.loads(line)
    except json.JSONDecodeError as exc:
        raise ProviderExecutionError(f"Codex app-server emitted invalid JSON-RPC: {line!r}") from exc
    if not isinstance(payload, dict):
        raise ProviderExecutionError(f"Codex app-server emitted a non-object message: {payload!r}")
    return cast(dict[str, Any], payload)


def _coerce_agent_ref(raw: Any, *, provider: str, fallback_name: str) -> AgentRef:
    if isinstance(raw, AgentRef):
        return raw
    data = _as_dict(raw)
    agent_id = _first_str(data, "id", "agent_id") or fallback_name
    metadata = dict(data.get("metadata") or {})
    metadata.setdefault("raw", data)
    return AgentRef(provider=provider, id=agent_id, metadata=metadata)


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
    provider_session_id = _first_str(data, "provider_session_id", "id", "thread_id")
    if provider_session_id is None:
        raise ProviderExecutionError("Codex app-server returned a session without a thread id.")
    metadata = dict(data.get("metadata") or {})
    metadata.setdefault("provider_session_id", provider_session_id)
    metadata.setdefault("raw", data)
    agent_id = agent.id if isinstance(agent, AgentRef) else str(agent)
    return AgentSession(
        provider=provider,
        task=task.prompt,
        agent_id=agent_id,
        model=data.get("model") if isinstance(data.get("model"), str) else task.model,
        status="running",
        metadata=metadata,
        id=provider_session_id,
    )


def _coerce_invocation_ref(raw: Any, *, provider: str, session_id: str) -> InvocationRef:
    if isinstance(raw, InvocationRef):
        return raw
    data = _as_dict(raw)
    invocation_id = _first_str(data, "id", "turn_id") or f"inv_{uuid4().hex}"
    metadata = dict(data.get("metadata") or {})
    metadata.setdefault("raw", data)
    return InvocationRef(provider=provider, session_id=session_id, id=invocation_id, metadata=metadata)


def _coerce_artifact_page(raw: Any) -> ArtifactPage:
    if isinstance(raw, ArtifactPage):
        return raw
    data = _as_dict(raw)
    items = [_coerce_artifact(item) for item in data.get("items", []) if item is not None]
    return ArtifactPage(
        items=items,
        next_cursor=_first_str(data, "next_cursor", "nextCursor"),
        has_more=bool(data.get("has_more", data.get("hasMore", False))),
    )


def _coerce_artifact(raw: Any) -> Artifact:
    if isinstance(raw, Artifact):
        return raw
    data = _as_dict(raw)
    metadata = dict(data.get("metadata") or {})
    metadata.setdefault("raw", data)
    return Artifact(
        id=_first_str(data, "id") or f"art_{uuid4().hex}",
        type=str(data.get("type") or "file_change"),
        name=str(data.get("name") or data.get("path") or data.get("id") or "Codex file change"),
        data=data.get("data"),
        uri=_first_str(data, "uri"),
        metadata=metadata,
    )


def _coerce_event(raw: Any, *, provider: str, session_id: str) -> AgentEvent:
    if isinstance(raw, AgentEvent):
        return replace(raw, provider=raw.provider or provider, session_id=raw.session_id or session_id)
    data = _as_dict(raw)
    method = str(data.get("method") or data.get("type") or "")
    params = data.get("params")
    payload = dict(params) if isinstance(params, dict) else {}
    event_type = _event_type(method, payload)
    event_data = {"method": method, **payload}
    item_id = _first_str(payload, "itemId", "approvalId")
    item = payload.get("item")
    if item_id is None and isinstance(item, dict):
        item_id = _first_str(item, "id")
    return AgentEvent(
        id=_first_str(data, "id") or f"codex_evt_{uuid4().hex}",
        type=event_type,
        provider=provider,
        session_id=session_id,
        item_id=item_id,
        data=event_data,
        raw=raw,
    )


def _event_type(method: str, params: dict[str, Any]) -> str:
    if method == "blackbox/session/started":
        return EventTypes.SESSION_STARTED
    if method == "blackbox/session/cancelled":
        return EventTypes.SESSION_CANCELLED
    if method == "blackbox/session/failed":
        return EventTypes.SESSION_FAILED
    if method == "blackbox/approval/requested":
        return EventTypes.APPROVAL_REQUESTED
    if method == "turn/started":
        return EventTypes.MODEL_REQUEST_STARTED
    if method == "turn/completed":
        turn = params.get("turn")
        status = turn.get("status") if isinstance(turn, dict) else None
        if status == "interrupted":
            return EventTypes.SESSION_CANCELLED
        if status == "failed":
            return EventTypes.SESSION_FAILED
        return EventTypes.SESSION_COMPLETED
    if method == "item/agentMessage/delta":
        return EventTypes.MODEL_TEXT_DELTA
    if method in {"item/reasoning/textDelta", "item/reasoning/summaryTextDelta"}:
        return EventTypes.MODEL_REASONING_DELTA
    if method == "item/commandExecution/outputDelta":
        return EventTypes.WORKSPACE_COMMAND_OUTPUT
    if method in {"item/fileChange/outputDelta", "item/fileChange/patchUpdated", "turn/diff/updated"}:
        return EventTypes.WORKSPACE_FILE_CHANGED
    if method == "item/mcpToolCall/progress":
        return EventTypes.MCP_CALL_STARTED
    if method == "item/started":
        item_type = _item_type(params)
        if item_type == "commandExecution":
            return EventTypes.WORKSPACE_COMMAND_STARTED
        if item_type == "mcpToolCall":
            return EventTypes.MCP_CALL_STARTED
        if item_type in {"webSearch", "dynamicToolCall"}:
            return EventTypes.HOSTED_TOOL_CALL_STARTED
        if item_type == "agentMessage":
            return EventTypes.AGENT_RESPONSE_MESSAGE_CREATED
        return EventTypes.MODEL_ITEM_CREATED
    if method == "item/completed":
        item_type = _item_type(params)
        if item_type == "commandExecution":
            return EventTypes.WORKSPACE_COMMAND_COMPLETED
        if item_type == "fileChange":
            return EventTypes.WORKSPACE_FILE_CHANGED
        if item_type == "mcpToolCall":
            return EventTypes.MCP_CALL_COMPLETED
        if item_type in {"webSearch", "dynamicToolCall"}:
            return EventTypes.HOSTED_TOOL_CALL_COMPLETED
        if item_type == "agentMessage":
            return EventTypes.AGENT_RESPONSE_MESSAGE_CREATED
        return EventTypes.MODEL_ITEM_COMPLETED
    return EventTypes.CLOUD_AGENT_LOG


def _item_type(params: dict[str, Any]) -> str | None:
    item = params.get("item")
    if not isinstance(item, dict):
        return None
    value = item.get("type")
    return value if isinstance(value, str) else None


def _update_session_from_event(session: AgentSession, event: AgentEvent) -> None:
    if event.type == EventTypes.SESSION_COMPLETED:
        session.status = "completed"
    elif event.type == EventTypes.SESSION_FAILED:
        session.status = "failed"
    elif event.type == EventTypes.SESSION_CANCELLED:
        session.status = "cancelled"
    elif event.type == EventTypes.APPROVAL_REQUESTED:
        session.status = "waiting"
    elif event.type in {EventTypes.SESSION_STARTED, EventTypes.MODEL_REQUEST_STARTED}:
        if session.status != "waiting":
            session.status = "running"


def _provider_session_id(session: AgentSession) -> str:
    value = session.metadata.get("provider_session_id")
    return str(value) if value is not None else session.id


def _approval_id(method: str, params: dict[str, Any]) -> str:
    value = _first_str(params, "approvalId", "itemId", "callId")
    return f"codex_approval_{value}" if value is not None else f"codex_approval_{uuid4().hex}"


async def _notify_session(session: _CodexSession) -> None:
    async with session.condition:
        session.condition.notify_all()


def _record_artifact(session: _CodexSession, event: dict[str, Any]) -> None:
    if event.get("method") != "item/completed":
        return
    params = event.get("params")
    if not isinstance(params, dict) or _item_type(params) != "fileChange":
        return
    item = params.get("item")
    if not isinstance(item, dict):
        return
    item_id = _first_str(item, "id") or _first_str(params, "itemId") or f"art_{uuid4().hex}"
    if any(artifact.id == item_id for artifact in session.artifacts):
        return
    session.artifacts.append(
        Artifact(
            id=item_id,
            type="file_change",
            name=str(item.get("path") or item_id),
            data=item,
            metadata={"raw": event, "thread_id": session.thread_id},
        )
    )


def _cursor_after(events: list[dict[str, Any]], after_event_id: str | None) -> int:
    if after_event_id is None:
        return 0
    for index, event in enumerate(events):
        if event.get("id") == after_event_id:
            return index + 1
    return len(events)


def _artifact_cursor(artifacts: list[Artifact], after: str | None) -> int:
    if after is None:
        return 0
    for index, artifact in enumerate(artifacts):
        if artifact.id == after:
            return index + 1
    return len(artifacts)


def _turn_is_complete(events: list[dict[str, Any]], turn_id: str | None) -> bool:
    return any(_is_turn_completed(event, turn_id) for event in events)


def _is_turn_completed(event: dict[str, Any], turn_id: str | None) -> bool:
    if event.get("method") != "turn/completed":
        return False
    params = event.get("params")
    if not isinstance(params, dict):
        return False
    turn = params.get("turn")
    if not isinstance(turn, dict):
        return False
    return turn_id is None or turn.get("id") == turn_id


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        dumped = model_dump()
        if isinstance(dumped, dict):
            return dumped
    if hasattr(value, "__dict__"):
        return dict(vars(value))
    return {}


def _first_str(data: Mapping[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = data.get(key)
        if isinstance(value, str):
            return value
    return None


def _nested_str(data: Mapping[str, Any], *keys: str) -> str | None:
    value: Any = data
    for key in keys:
        if not isinstance(value, Mapping):
            return None
        value = value.get(key)
    return value if isinstance(value, str) else None
