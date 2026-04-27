from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

from agent_runtime import AgentRuntime
from agent_runtime.agents.openai_cloud import OpenAICloudAgentProvider
from agent_runtime.core.approvals import ApprovalDecision
from agent_runtime.core.events import EventTypes
from agent_runtime.providers.base import AgentSpec, TaskSpec


def test_openai_agents_provider_registers_plural_alias() -> None:
    runtime = AgentRuntime()
    provider = OpenAICloudAgentProvider(client=cast(Any, object()))

    runtime.registry.register_agent(provider)

    assert runtime.registry.get_agent("openai-agents") is provider


async def test_openai_agents_sdk_lifecycle_streams_and_resumes() -> None:
    sdk = FakeOpenAIAgentsSDK()
    provider = OpenAICloudAgentProvider(api_key="sk-test", sdk_module=sdk)

    agent = await provider.create_agent(
        AgentSpec(name="coder", instructions="Ship code.", model="gpt-5.5")
    )
    session = await provider.start_session(agent, TaskSpec(prompt="fix bug"))
    events = [event async for event in provider.stream_events(session)]
    artifacts = await provider.list_artifacts(session, type="log")

    assert provider.capabilities().supports_sessions is True
    assert provider.capabilities().supports_approvals is True
    assert sdk.api_key == ("sk-test", False)
    assert sdk.agents[0].kwargs["model"] == "gpt-5.5"
    assert sdk.runs[0].input == "fix bug"
    assert [event.type for event in events] == [
        EventTypes.SESSION_STARTED,
        EventTypes.MODEL_TEXT_DELTA,
        EventTypes.TOOL_CALL_STARTED,
        EventTypes.TOOL_CALL_COMPLETED,
        EventTypes.SESSION_COMPLETED,
    ]
    assert str(session.status) == "completed"
    assert [artifact.name for artifact in artifacts.items] == ["final_output.txt"]

    resumed = [event async for event in provider.stream_events(session, after_event_id=events[1].id)]
    assert [event.type for event in resumed] == [
        EventTypes.TOOL_CALL_STARTED,
        EventTypes.TOOL_CALL_COMPLETED,
        EventTypes.SESSION_COMPLETED,
    ]


async def test_openai_agents_sdk_followup_and_cancel() -> None:
    sdk = FakeOpenAIAgentsSDK()
    provider = OpenAICloudAgentProvider(api_key="sk-test", sdk_module=sdk)
    agent = await provider.create_agent(AgentSpec(name="assistant"))
    session = await provider.start_session(agent, TaskSpec(prompt="first"))
    _ = [event async for event in provider.stream_events(session)]

    invocation = await provider.send_message(session, "second")
    await provider.cancel(session)

    assert invocation.id.startswith("inv_")
    assert sdk.runs[1].input[-1] == {"role": "user", "content": "second"}
    assert sdk.runs[1].cancelled is True
    assert session.status == "cancelled"


async def test_openai_agents_sdk_approval_resume() -> None:
    sdk = FakeOpenAIAgentsSDK(interrupt=True)
    provider = OpenAICloudAgentProvider(api_key="sk-test", sdk_module=sdk)
    agent = await provider.create_agent(AgentSpec(name="assistant"))
    session = await provider.start_session(agent, TaskSpec(prompt="delete files"))

    first_pass = [event async for event in provider.stream_events(session)]
    approval = first_pass[-1]
    assert approval.type == EventTypes.APPROVAL_REQUESTED
    assert session.status == "waiting"

    await provider.approve(approval.item_id or "", ApprovalDecision.approve("ok"))
    resumed = [event async for event in provider.stream_events(session, after_event_id=approval.id)]

    assert resumed[0].type == EventTypes.APPROVAL_APPROVED
    assert resumed[-1].type == EventTypes.SESSION_COMPLETED
    assert sdk.state.approved is True
    assert cast(str, session.status) == "completed"


@dataclass(slots=True)
class FakeResponseEvent:
    type: str
    delta: str | None = None


@dataclass(slots=True)
class FakeStreamEvent:
    type: str
    data: Any | None = None
    name: str | None = None
    item: Any | None = None


@dataclass(slots=True)
class FakeRunItem:
    id: str
    tool_name: str | None = None
    output: str | None = None


@dataclass(slots=True)
class FakeInterruption:
    id: str
    name: str
    arguments: str


class FakeRunState:
    def __init__(self) -> None:
        self.approved = False
        self.rejected = False

    def approve(self, interruption: FakeInterruption) -> None:
        self.approved = interruption.id == "tool_delete"

    def reject(self, interruption: FakeInterruption, *, rejection_message: str | None = None) -> None:
        self.rejected = interruption.id == "tool_delete" and rejection_message is not None


class FakeRunResult:
    def __init__(self, input: Any, *, interrupt: bool = False, state: FakeRunState | None = None) -> None:
        self.input = input
        self.interrupt = interrupt
        self.state = state or FakeRunState()
        self.final_output = "done"
        self.last_response_id = "resp_1"
        self.interruptions = [
            FakeInterruption("tool_delete", "delete_file", '{"path":"tmp.txt"}')
        ] if interrupt else []
        self.cancelled = False

    async def stream_events(self) -> Any:
        if self.interrupt:
            return
            yield
        yield FakeStreamEvent(
            type="raw_response_event",
            data=FakeResponseEvent("response.output_text.delta", "working"),
        )
        yield FakeStreamEvent(
            type="run_item_stream_event",
            name="tool_called",
            item=FakeRunItem("tool_1", tool_name="lookup"),
        )
        yield FakeStreamEvent(
            type="run_item_stream_event",
            name="tool_output",
            item=FakeRunItem("tool_1", tool_name="lookup", output="ok"),
        )

    def to_state(self) -> FakeRunState:
        return self.state

    def to_input_list(self) -> list[Any]:
        return [{"role": "user", "content": str(self.input)}]

    def cancel(self) -> None:
        self.cancelled = True


class FakeAgent:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs


class FakeRunner:
    def __init__(self, sdk: FakeOpenAIAgentsSDK) -> None:
        self.sdk = sdk

    def run_streamed(self, agent: FakeAgent, input: Any, **kwargs: Any) -> FakeRunResult:
        del agent, kwargs
        is_resume = isinstance(input, FakeRunState)
        result = FakeRunResult(
            input,
            interrupt=self.sdk.interrupt and not is_resume,
            state=input if is_resume else self.sdk.state,
        )
        self.sdk.runs.append(result)
        return result


class FakeOpenAIAgentsSDK:
    def __init__(self, *, interrupt: bool = False) -> None:
        self.interrupt = interrupt
        self.state = FakeRunState()
        self.agents: list[FakeAgent] = []
        self.runs: list[FakeRunResult] = []
        self.Runner = FakeRunner(self)
        self.Agent = self._agent_factory
        self.api_key: tuple[str, bool] | None = None

    def set_default_openai_key(self, key: str, *, use_for_tracing: bool = True) -> None:
        self.api_key = (key, use_for_tracing)

    def _agent_factory(self, **kwargs: Any) -> FakeAgent:
        agent = FakeAgent(**kwargs)
        self.agents.append(agent)
        return agent
