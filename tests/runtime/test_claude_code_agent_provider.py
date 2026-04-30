from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from blackbox.core.approvals import ApprovalDecision
from blackbox.core.events import EventTypes
from blackbox.providers.agent_adapters.claude_code import ClaudeCodeAgentProvider
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
    assert [event.type for event in events] == [
        EventTypes.MODEL_TEXT_DELTA,
        EventTypes.WORKSPACE_FILE_CHANGED,
        EventTypes.SESSION_COMPLETED,
    ]
    assert session.status == "completed"
    assert [artifact.name for artifact in artifacts.items] == ["app.py"]


async def test_claude_code_sdk_permission_callback_emits_approval_and_resumes() -> None:
    sdk = FakeClaudeAgentSDK(permission_request=True)
    provider = ClaudeCodeAgentProvider(api_key="sk-test", sdk_module=sdk)
    agent = await provider.create_agent(AgentSpec(name="coder"))
    session = await provider.start_session(agent, TaskSpec(prompt="run risky command"))

    stream = provider.stream_events(session).__aiter__()
    approval = await anext(stream)
    assert approval.type == EventTypes.APPROVAL_REQUESTED

    await provider.approve(approval.item_id or "", ApprovalDecision.approve("allowed"))
    rest = [event async for event in stream]

    assert rest[-1].type == EventTypes.SESSION_COMPLETED
    assert sdk.clients[0].permission_result is not None


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


class FakeClaudeSDKClient:
    """Fake Claude Agent SDK client that emits deterministic stream and permission events."""

    def __init__(self, options: FakeClaudeAgentOptions, sdk: FakeClaudeAgentSDK) -> None:
        self.options = options
        self.sdk = sdk
        self.queries: list[str] = []
        self.permission_result: Any | None = None
        self.interrupted = False
        sdk.clients.append(self)

    async def connect(self) -> None:
        return None

    async def query(self, prompt: str) -> None:
        self.queries.append(prompt)

    async def get_server_info(self) -> dict[str, str]:
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


class FakeClaudeAgentSDK:
    ClaudeAgentOptions = FakeClaudeAgentOptions
    PermissionResultAllow = FakePermissionResultAllow
    PermissionResultDeny = FakePermissionResultDeny

    def __init__(self, *, permission_request: bool = False) -> None:
        self.permission_request = permission_request
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
