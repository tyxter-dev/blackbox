from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import pytest

from blackbox.core.approvals import ApprovalDecision
from blackbox.core.errors import ProviderNotConfiguredError
from blackbox.core.events import EventTypes
from blackbox.providers.agent_adapters import claude_code
from blackbox.providers.agent_adapters.claude_code import (
    ClaudeAgentSDKClient,
    ClaudeCodeAgentProvider,
    _filter_options_kwargs,
)
from blackbox.providers.base import AgentSpec, TaskSpec
from blackbox.workspaces.spec import WorkspaceSpec
from tests.fixtures.fake_claude_code_client import FakeClaudeCodeClient


async def test_claude_code_client_backed_lifecycle_streams_and_resumes() -> None:
    client = FakeClaudeCodeClient(
        events=[
            {"id": "evt_1", "type": "status", "status": "running"},
            {"id": "evt_2", "type": "log", "message": "working"},
            {"id": "evt_3", "type": "file_changed", "path": "app.py"},
            {"id": "evt_4", "type": "approval_required", "approval_id": "approval_1"},
            {"id": "evt_5", "type": "completed"},
        ],
    )
    provider = ClaudeCodeAgentProvider(client=client)

    agent = await provider.create_agent(AgentSpec(name="coder", instructions="Ship code."))
    session = await provider.start_session(agent, TaskSpec(prompt="fix bug", model="claude"))
    events = [event async for event in provider.stream_events(session)]

    assert provider.capabilities().supports_sessions is True
    assert agent.id == "agent_coder"
    assert session.id == "sess_runtime_1"
    assert session.metadata["provider_session_id"] == "provider_sess_1"
    assert [event.type for event in events] == [
        EventTypes.CLOUD_AGENT_STATUS_CHANGED,
        EventTypes.CLOUD_AGENT_LOG,
        EventTypes.WORKSPACE_FILE_CHANGED,
        EventTypes.APPROVAL_REQUESTED,
        EventTypes.SESSION_COMPLETED,
    ]
    assert session.status == "completed"

    resumed = [event async for event in provider.stream_events(session, after_event_id="evt_3")]
    assert [event.id for event in resumed] == ["evt_4", "evt_5"]


async def test_claude_code_client_backed_controls_and_artifacts() -> None:
    client = FakeClaudeCodeClient(
        artifacts=[
            {"id": "art_patch", "type": "patch", "name": "fix.patch", "data": "diff"},
            {"id": "art_log", "type": "log", "name": "pytest.log", "data": "ok"},
        ],
    )
    provider = ClaudeCodeAgentProvider(client=client)
    agent = await provider.create_agent(AgentSpec(name="coder"))
    session = await provider.start_session(agent, TaskSpec(prompt="fix bug"))

    invocation = await provider.send_message(session, "continue")
    await provider.approve("approval_1", ApprovalDecision.approve("ok"))
    await provider.cancel(session)
    artifacts = await provider.list_artifacts(session, type="patch")

    assert invocation.id == "inv_followup"
    assert client.messages == [("provider_sess_1", "continue")]
    assert client.approvals[0][0] == "approval_1"
    assert client.cancelled == ["provider_sess_1"]
    assert session.status == "cancelled"
    assert [artifact.id for artifact in artifacts.items] == ["art_patch"]


async def test_claude_code_sdk_backed_provider_runs_without_injected_client() -> None:
    sdk = FakeClaudeAgentSDK()
    provider = ClaudeCodeAgentProvider(api_key="sk-test", sdk_module=sdk)

    agent = await provider.create_agent(
        AgentSpec(
            name="coder",
            instructions="Ship code.",
            model="claude-sonnet",
            tools=["Read", "Write"],
            permissions={"permission_mode": "default"},
        )
    )
    session = await provider.start_session(
        agent,
        TaskSpec(prompt="fix bug", workspace=WorkspaceSpec.local("/tmp/project")),
    )

    events = [event async for event in provider.stream_events(session)]
    artifacts = await provider.list_artifacts(session, type="file_change")

    assert provider.capabilities().supports_sessions is True
    assert provider.capabilities().supports_mcp is True
    assert session.metadata["provider_session_id"] == "sdk_sess_1"
    assert sdk.clients[0].options.kwargs["env"]["ANTHROPIC_API_KEY"] == "sk-test"
    assert sdk.clients[0].options.kwargs["cwd"] == "/tmp/project"
    assert sdk.clients[0].queries == ["fix bug"]
    assert isinstance(provider._client, ClaudeAgentSDKClient)
    assert (
        provider._client._sessions[session.id]
        is provider._client._sessions[session.metadata["provider_session_id"]]
    )
    assert [event.type for event in events] == [
        EventTypes.MODEL_REQUEST_STARTED,
        EventTypes.MODEL_TEXT_DELTA,
        EventTypes.WORKSPACE_FILE_CHANGED,
        EventTypes.SESSION_COMPLETED,
    ]
    assert session.status == "completed"
    assert [artifact.name for artifact in artifacts.items] == ["app.py"]

    replayed = [
        event async for event in provider.stream_events(session, after_event_id=events[0].id)
    ]
    assert [event.id for event in replayed] == [event.id for event in events[1:]]


async def test_claude_code_sdk_permission_callback_emits_approval_and_resumes() -> None:
    sdk = FakeClaudeAgentSDK(permission_request=True)
    provider = ClaudeCodeAgentProvider(api_key="sk-test", sdk_module=sdk)
    agent = await provider.create_agent(AgentSpec(name="coder"))
    session = await provider.start_session(agent, TaskSpec(prompt="run risky command"))

    stream = provider.stream_events(session).__aiter__()
    request_started = await anext(stream)
    approval = await anext(stream)
    assert request_started.type == EventTypes.MODEL_REQUEST_STARTED
    assert approval.type == EventTypes.APPROVAL_REQUESTED

    await provider.approve(approval.item_id or "", ApprovalDecision.approve("allowed"))
    rest = [event async for event in stream]

    assert rest[-1].type == EventTypes.SESSION_COMPLETED
    assert sdk.clients[0].permission_result is not None


async def test_claude_code_auto_uses_subscription_without_api_key(
    monkeypatch: Any,
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "oauth-test-token")
    sdk = FakeClaudeAgentSDK()
    provider = ClaudeCodeAgentProvider(sdk_module=sdk)  # no api_key, auth defaults to "auto"

    assert provider.capabilities().supports_sessions is True

    agent = await provider.create_agent(AgentSpec(name="coder"))
    await provider.start_session(agent, TaskSpec(prompt="fix bug"))

    env = sdk.clients[0].options.kwargs["env"]
    # Subscription auth must not inject a real API key; the inherited key (none
    # here) is neutralized to an empty string so the CLI uses OAuth credentials.
    assert env["ANTHROPIC_API_KEY"] == ""


async def test_claude_code_subscription_neutralizes_inherited_api_key(
    monkeypatch: Any,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-should-not-be-used")
    sdk = FakeClaudeAgentSDK()
    provider = ClaudeCodeAgentProvider(auth="subscription", sdk_module=sdk)

    agent = await provider.create_agent(AgentSpec(name="coder"))
    await provider.start_session(agent, TaskSpec(prompt="fix bug"))

    env = sdk.clients[0].options.kwargs["env"]
    assert env["ANTHROPIC_API_KEY"] == ""


async def test_claude_code_forwards_setting_sources_for_project_skills() -> None:
    # Skills/agents/commands under the workspace ``.claude/`` only load when the
    # SDK is told to read filesystem settings; the provider must forward
    # ``setting_sources`` so a caller can opt in.
    sdk = FakeClaudeAgentSDK()
    provider = ClaudeCodeAgentProvider(api_key="sk-test", sdk_module=sdk)

    agent = await provider.create_agent(
        AgentSpec(name="coder", permissions={"setting_sources": ["project"]})
    )
    await provider.start_session(
        agent,
        TaskSpec(prompt="use the review-pr skill", workspace=WorkspaceSpec.local("/tmp/project")),
    )

    assert sdk.clients[0].options.kwargs["setting_sources"] == ["project"]


async def test_claude_code_setting_sources_via_task_extra() -> None:
    sdk = FakeClaudeAgentSDK()
    provider = ClaudeCodeAgentProvider(api_key="sk-test", sdk_module=sdk)

    agent = await provider.create_agent(AgentSpec(name="coder"))
    await provider.start_session(
        agent,
        TaskSpec(prompt="fix bug", extra={"setting_sources": ["project", "user"]}),
    )

    assert sdk.clients[0].options.kwargs["setting_sources"] == ["project", "user"]


async def test_claude_code_forwards_task_budget_and_emits_turn_boundaries() -> None:
    sdk = FakeClaudeAgentSDK()
    provider = ClaudeCodeAgentProvider(api_key="sk-test", sdk_module=sdk)

    agent = await provider.create_agent(AgentSpec(name="coder", model="agent-fallback"))
    session = await provider.start_session(
        agent,
        TaskSpec(
            prompt="first request",
            model="claude-sonnet",
            extra={"task_budget": {"total": 1024}},
        ),
    )
    first = [event async for event in provider.stream_events(session)]
    await provider.send_message(session, "follow up")
    second = [event async for event in provider.stream_events(session)]

    assert sdk.clients[0].options.kwargs["task_budget"] == {"total": 1024}
    assert first[0].type == EventTypes.MODEL_REQUEST_STARTED
    assert first[0].data == {"model": "claude-sonnet", "turn": 1}
    assert second[0].type == EventTypes.MODEL_REQUEST_STARTED
    assert second[0].data == {"model": "claude-sonnet", "turn": 2}


async def test_claude_code_turn_boundary_uses_agent_model_when_task_model_is_absent() -> None:
    sdk = FakeClaudeAgentSDK()
    provider = ClaudeCodeAgentProvider(api_key="sk-test", sdk_module=sdk)
    agent = await provider.create_agent(AgentSpec(name="coder", model="agent-model"))
    session = await provider.start_session(agent, TaskSpec(prompt="first request"))
    first = [event async for event in provider.stream_events(session)]
    await provider.send_message(session, "follow up")
    second = [event async for event in provider.stream_events(session)]

    assert first[0].data == {"model": "agent-model", "turn": 1}
    assert second[0].data == {"model": "agent-model", "turn": 2}


async def test_claude_code_turn_boundary_preserves_empty_task_model_override() -> None:
    sdk = FakeClaudeAgentSDK()
    provider = ClaudeCodeAgentProvider(api_key="sk-test", sdk_module=sdk)
    agent = await provider.create_agent(AgentSpec(name="coder", model="agent-model"))
    session = await provider.start_session(agent, TaskSpec(prompt="first request", model=""))
    first = [event async for event in provider.stream_events(session)]
    await provider.send_message(session, "follow up")
    second = [event async for event in provider.stream_events(session)]

    assert sdk.clients[0].options.kwargs["model"] == ""
    assert first[0].data == {"model": "", "turn": 1}
    assert second[0].data == {"model": "", "turn": 2}


@pytest.mark.parametrize("total", [1, claude_code.MAX_CLAUDE_TASK_BUDGET_TOKENS])
async def test_claude_code_accepts_task_budget_boundaries(total: int) -> None:
    sdk = FakeClaudeAgentSDK()
    provider = ClaudeCodeAgentProvider(api_key="sk-test", sdk_module=sdk)
    agent = await provider.create_agent(AgentSpec(name="coder"))
    await provider.start_session(agent, TaskSpec(prompt="budget", extra={"task_budget": {"total": total}}))

    assert sdk.clients[0].options.kwargs["task_budget"] == {"total": total}


@pytest.mark.parametrize(
    ("source", "value"),
    [
        ("task", "1024"),
        ("task", {}),
        ("task", {"total": True}),
        ("task", {"total": "1024"}),
        ("task", {"total": 0}),
        ("task", {"total": -1}),
        ("task", {"total": claude_code.MAX_CLAUDE_TASK_BUDGET_TOKENS + 1}),
        ("task", {"total": 1024, "unreviewed": 1}),
        ("permissions", {"total": "1024"}),
    ],
)
async def test_claude_code_rejects_unbounded_or_malformed_task_budget(
    source: str,
    value: Any,
) -> None:
    sdk = FakeClaudeAgentSDK()
    permissions = {"task_budget": value} if source == "permissions" else {}
    extra = {"task_budget": value} if source == "task" else {}
    provider = ClaudeCodeAgentProvider(api_key="sk-test", sdk_module=sdk)
    agent = await provider.create_agent(AgentSpec(name="coder", permissions=permissions))

    with pytest.raises(ValueError, match="task_budget"):
        await provider.start_session(agent, TaskSpec(prompt="budget", extra=extra))


async def test_claude_code_registers_sdk_owner_before_startup_connect_returns() -> None:
    class BlockingClient(FakeClaudeSDKClient):
        async def connect(self) -> None:
            self.sdk.connect_started.set()
            await self.sdk.release_connect.wait()

    class BlockingSDK(FakeClaudeAgentSDK):
        def __init__(self) -> None:
            super().__init__()
            self.connect_started = asyncio.Event()
            self.release_connect = asyncio.Event()

        def _client_factory(self, *, options: FakeClaudeAgentOptions) -> BlockingClient:
            return BlockingClient(options, self)

    sdk = BlockingSDK()
    client = ClaudeAgentSDKClient(sdk_module=sdk)
    agent = await client.create_agent(AgentSpec(name="coder"))
    startup = asyncio.create_task(client.start_session(agent["id"], TaskSpec(prompt="start")))
    await sdk.connect_started.wait()

    managed = next(iter(client._sessions.values()))
    assert managed.client is sdk.clients[0]
    assert managed.task.prompt == "start"

    startup.cancel()
    with pytest.raises(asyncio.CancelledError):
        await startup


def test_filter_options_kwargs_drops_unknown_keys_with_warning() -> None:
    # An option key the installed SDK doesn't model must be dropped (not raised)
    # so version skew degrades gracefully; the caller is warned it had no effect.
    @dataclass(slots=True)
    class StrictOptions:
        model: str | None = None
        cwd: str | None = None

    with pytest.warns(RuntimeWarning, match="setting_sources"):
        filtered = _filter_options_kwargs(
            StrictOptions, {"model": "claude", "setting_sources": ["project"]}
        )

    assert filtered == {"model": "claude"}
    StrictOptions(**filtered)  # constructs without TypeError


def test_filter_options_kwargs_passes_through_var_keyword_classes() -> None:
    # Classes whose constructor accepts ``**kwargs`` (e.g. test fakes, or a
    # future SDK that does the same) must not be filtered.
    class Loose:
        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs

    payload = {"setting_sources": ["project"], "anything": 1}
    assert _filter_options_kwargs(Loose, payload) == payload


async def test_claude_code_auth_api_key_requires_key(monkeypatch: Any) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    provider = ClaudeCodeAgentProvider(auth="api_key")

    assert provider.capabilities().supports_sessions is False
    try:
        await provider.create_agent(AgentSpec(name="coder"))
    except ProviderNotConfiguredError:
        pass
    else:  # pragma: no cover - guard
        raise AssertionError("expected ProviderNotConfiguredError")


async def test_claude_code_sdk_projects_text_delta_into_event_data() -> None:
    sdk = FakeClaudeAgentSDK()
    provider = ClaudeCodeAgentProvider(api_key="sk-test", sdk_module=sdk)
    agent = await provider.create_agent(AgentSpec(name="coder"))
    session = await provider.start_session(agent, TaskSpec(prompt="hi"))

    events = [event async for event in provider.stream_events(session)]
    delta = next(event for event in events if event.type == EventTypes.MODEL_TEXT_DELTA)

    # The decoded text projection must win over the raw SDK envelope (which also
    # carries a ``message`` key holding the full SDK message dict).
    assert delta.data["message"] == "working"
    # ...while the untouched raw envelope is still preserved verbatim on ``raw``.
    assert isinstance(delta.raw, dict)
    assert isinstance(delta.raw["data"]["message"], dict)


@dataclass(slots=True)
class FakeClaudeAgentOptions:
    kwargs: dict[str, Any]

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs


@dataclass(slots=True)
class FakeStreamEvent:
    uuid: str
    session_id: str
    event: dict[str, Any]


@dataclass(slots=True)
class FakeToolUseBlock:
    id: str
    name: str
    input: dict[str, Any]


@dataclass(slots=True)
class FakeAssistantMessage:
    content: list[Any]
    model: str


@dataclass(slots=True)
class FakeResultMessage:
    subtype: str
    is_error: bool
    result: str
    session_id: str


@dataclass(slots=True)
class FakePermissionContext:
    tool_use_id: str


@dataclass(slots=True)
class FakePermissionResultAllow:
    updated_input: dict[str, Any]


@dataclass(slots=True)
class FakePermissionResultDeny:
    message: str
    interrupt: bool


class FakeChildProcess:
    def __init__(self) -> None:
        self.returncode: int | None = None
        self.waits = 0
        self.terminated = 0
        self.killed = 0

    async def wait(self) -> None:
        self.waits += 1
        if self.returncode is None:
            await asyncio.Event().wait()

    def terminate(self) -> None:
        self.terminated += 1
        self.returncode = -15

    def kill(self) -> None:
        self.killed += 1
        self.returncode = -9


class FakeClaudeSDKClient:
    """Fake Claude Agent SDK client that emits deterministic stream and permission events."""

    def __init__(self, options: FakeClaudeAgentOptions, sdk: FakeClaudeAgentSDK) -> None:
        self.options = options
        self.sdk = sdk
        self.queries: list[str] = []
        self.permission_result: Any | None = None
        self.interrupted = False
        self.disconnected = False
        if sdk.child_process is not None:
            self._transport = type("Transport", (), {"_process": sdk.child_process})()
        sdk.clients.append(self)

    async def connect(self) -> None:
        self.sdk.connect_started.set()
        if self.sdk.connect_error:
            raise RuntimeError("connect failed")
        if self.sdk.block_connect:
            await self.sdk.release_connect.wait()
        return None

    async def query(self, prompt: str) -> None:
        self.queries.append(prompt)
        if self.sdk.query_error:
            raise RuntimeError("query failed")

    async def get_server_info(self) -> dict[str, str]:
        if self.sdk.server_info_error:
            raise RuntimeError("server info failed")
        return {"session_id": "sdk_sess_1"}

    async def receive_response(self) -> Any:
        if self.sdk.permission_request:
            callback = self.options.kwargs["can_use_tool"]
            task = self.sdk.create_permission_task(
                callback("Bash", {"command": "rm -rf build"}, FakePermissionContext("tool_approval"))
            )
            await self.sdk.sleep_once()
            self.permission_result = await task
        else:
            yield FakeStreamEvent(
                uuid="stream_1",
                session_id="sdk_sess_1",
                event={
                    "type": "content_block_delta",
                    "delta": {"type": "text_delta", "text": "working"},
                },
            )
            yield FakeAssistantMessage(
                content=[
                    FakeToolUseBlock(
                        id="tool_write",
                        name="Write",
                        input={"file_path": "app.py"},
                    )
                ],
                model="claude-sonnet",
            )
        yield FakeResultMessage(
            subtype="success",
            is_error=False,
            result="done",
            session_id="sdk_sess_1",
        )

    async def interrupt(self) -> None:
        self.interrupted = True

    async def disconnect(self) -> None:
        self.disconnected = True


class FakeClaudeAgentSDK:
    ClaudeAgentOptions = FakeClaudeAgentOptions
    PermissionResultAllow = FakePermissionResultAllow
    PermissionResultDeny = FakePermissionResultDeny

    def __init__(
        self,
        *,
        permission_request: bool = False,
        connect_error: bool = False,
        query_error: bool = False,
        server_info_error: bool = False,
        block_connect: bool = False,
        child_process: FakeChildProcess | None = None,
    ) -> None:
        self.permission_request = permission_request
        self.connect_error = connect_error
        self.query_error = query_error
        self.server_info_error = server_info_error
        self.block_connect = block_connect
        self.child_process = child_process
        self.connect_started = asyncio.Event()
        self.release_connect = asyncio.Event()
        self.clients: list[FakeClaudeSDKClient] = []
        self.ClaudeSDKClient = self._client_factory

    def _client_factory(self, *, options: FakeClaudeAgentOptions) -> FakeClaudeSDKClient:
        return FakeClaudeSDKClient(options, self)

    def create_permission_task(self, awaitable: Any) -> Any:
        import asyncio

        return asyncio.create_task(awaitable)

    async def sleep_once(self) -> None:
        import asyncio

        await asyncio.sleep(0)


@pytest.mark.parametrize("failure", ["connect", "query", "server-info", "cancel"])
async def test_claude_code_startup_failure_reaps_and_forgets_provisional_owner(
    monkeypatch: Any,
    failure: str,
) -> None:
    monkeypatch.setattr(claude_code, "CLAUDE_STARTUP_CHILD_GRACE_SECONDS", 0.001)
    monkeypatch.setattr(claude_code, "CLAUDE_STARTUP_CHILD_KILL_GRACE_SECONDS", 0.001)
    process = FakeChildProcess()
    sdk = FakeClaudeAgentSDK(
        connect_error=failure == "connect",
        query_error=failure == "query",
        server_info_error=failure == "server-info",
        block_connect=failure == "cancel",
        child_process=process,
    )
    client = ClaudeAgentSDKClient(sdk_module=sdk)
    agent = await client.create_agent(AgentSpec(name="coder"))

    if failure == "cancel":
        startup = asyncio.create_task(client.start_session(agent["id"], TaskSpec(prompt="start")))
        await sdk.connect_started.wait()
        startup.cancel()
        with pytest.raises(asyncio.CancelledError):
            await startup
    else:
        message = "server info failed" if failure == "server-info" else f"{failure} failed"
        with pytest.raises(RuntimeError, match=message):
            await client.start_session(agent["id"], TaskSpec(prompt="start"))

    assert client._sessions == {}
    assert sdk.clients[0].interrupted is True
    assert sdk.clients[0].disconnected is True
    assert (process.terminated, process.killed, process.returncode) == (1, 0, -15)


@pytest.mark.parametrize("failure", ["connect", "cancel"])
async def test_claude_code_retains_uninspectable_failed_start_for_explicit_retry(
    monkeypatch: Any,
    failure: str,
) -> None:
    monkeypatch.setattr(claude_code, "CLAUDE_STARTUP_CHILD_GRACE_SECONDS", 0.001)
    monkeypatch.setattr(claude_code, "CLAUDE_STARTUP_CHILD_KILL_GRACE_SECONDS", 0.001)
    sdk = FakeClaudeAgentSDK(
        connect_error=failure == "connect",
        block_connect=failure == "cancel",
    )
    client = ClaudeAgentSDKClient(sdk_module=sdk)
    agent = await client.create_agent(AgentSpec(name="coder"))

    if failure == "cancel":
        startup = asyncio.create_task(client.start_session(agent["id"], TaskSpec(prompt="start")))
        await sdk.connect_started.wait()
        startup.cancel()
        with pytest.raises(asyncio.CancelledError):
            await startup
    else:
        with pytest.raises(RuntimeError, match="connect failed"):
            await client.start_session(agent["id"], TaskSpec(prompt="start"))

    assert len(client._sessions) == 1
    assert await client.cleanup_failed_starts() is False
    process = FakeChildProcess()
    sdk.clients[0]._transport = type("Transport", (), {"_process": process})()

    assert await client.cleanup_failed_starts() is True
    assert client._sessions == {}
    assert (process.terminated, process.killed, process.returncode) == (1, 0, -15)
