"""High-level AgentRuntime.run() product surface tests.

Offline analogues of the llm_factory_toolkit v1 contract: the runtime owns
the loop and returns a typed AgentResult[T] with text, payloads, events,
items, and provider state.
"""
from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, Field

from blackbox import AgentResult, AgentRuntime, EventTypes, OutputValidationError
from blackbox.core.policy import Policy, PolicyDecision, PolicyRequest
from blackbox.hosted_tools import WebSearch
from blackbox.tools import ToolResult
from blackbox.workspaces import (
    CommandResult,
    CommandSpec,
    WorkspaceRef,
    WorkspaceRuntime,
    WorkspaceSpec,
)
from tests.fixtures.scripted_model import (
    ScriptedModelProvider,
    text_only_turn,
    tool_call_turn,
)


def _runtime() -> tuple[AgentRuntime, ScriptedModelProvider]:
    runtime = AgentRuntime()
    scripted = ScriptedModelProvider()
    runtime.registry.register_model(scripted)
    return runtime, scripted


# --- text-only --------------------------------------------------------------

async def test_run_returns_text_when_no_output_type() -> None:
    runtime, scripted = _runtime()
    scripted.queue(text_only_turn("Paris is the capital of France."))

    result: AgentResult[str] = await runtime.run(
        provider="scripted:test", input="What is the capital of France?",
    )

    assert isinstance(result, AgentResult)
    assert result.output == "Paris is the capital of France."
    assert result.text == result.output
    assert any(e.type == EventTypes.MODEL_TEXT_DELTA for e in result.events)


# --- structured output ------------------------------------------------------

class ExtractedInfo(BaseModel):
    name: str = Field(description="Main person's name")
    age: int
    location: str
    sentiment: str


async def test_run_validates_pydantic_output_type() -> None:
    runtime, scripted = _runtime()
    scripted.queue(text_only_turn(json.dumps({
        "name": "Sarah", "age": 35, "location": "Paris", "sentiment": "positive",
    })))

    result: AgentResult[ExtractedInfo] = await runtime.run(
        provider="scripted:test", input="Extract info.", output_type=ExtractedInfo,
    )

    assert isinstance(result.output, ExtractedInfo)
    assert result.output.name == "Sarah"
    assert result.output.age == 35
    assert result.output.location == "Paris"


async def test_run_raises_output_validation_error_on_pydantic_mismatch() -> None:
    runtime, scripted = _runtime()
    scripted.queue(text_only_turn("not even json"))

    with pytest.raises(OutputValidationError) as info:
        await runtime.run(
            provider="scripted:test", input="x", output_type=ExtractedInfo,
        )
    assert info.value.raw_text == "not even json"


async def test_run_validates_dataclass_output_type() -> None:
    @dataclass
    class Decision:
        priority: str
        notes: str

    runtime, scripted = _runtime()
    scripted.queue(text_only_turn(json.dumps({"priority": "high", "notes": "x"})))

    result: AgentResult[Decision] = await runtime.run(
        provider="scripted:test", input="x", output_type=Decision,
    )
    assert isinstance(result.output, Decision)
    assert result.output.priority == "high"


# --- tool dispatch ----------------------------------------------------------

async def test_run_dispatches_a_single_tool_and_returns_final_text() -> None:
    runtime, scripted = _runtime()
    runtime.tools.register(
        lambda data_id: ToolResult(content=f"secret={data_id}-XYZ"),
        name="get_secret",
        description="Get a secret by data_id.",
        parameters={
            "type": "object",
            "properties": {"data_id": {"type": "string"}},
            "required": ["data_id"],
        },
    )
    scripted.queue(tool_call_turn(call_id="c1", name="get_secret", arguments={"data_id": "abc"}))
    scripted.queue(text_only_turn("Final answer: secret=abc-XYZ"))

    result: AgentResult[str] = await runtime.run(
        provider="scripted:test", input="get me the secret", tools=["get_secret"],
    )

    assert "secret=abc-XYZ" in result.output
    assert any(e.type == EventTypes.TOOL_CALL_COMPLETED for e in result.events)


async def test_run_forwards_hosted_tools_separately_from_local_tools() -> None:
    runtime, scripted = _runtime()
    runtime.tools.register(
        lambda: ToolResult(content="local"),
        name="local_tool",
        description="Local tool.",
        parameters={"type": "object", "properties": {}},
    )
    scripted.queue(text_only_turn("done"))

    result: AgentResult[str] = await runtime.run(
        provider="scripted:test",
        input="use tools",
        tools=["local_tool"],
        hosted_tools=[WebSearch()],
    )

    assert result.output == "done"
    assert scripted.calls[0].tools == [
        {
            "type": "function",
            "name": "local_tool",
            "description": "Local tool.",
            "parameters": {"type": "object", "properties": {}},
            "metadata": {},
        }
    ]
    assert scripted.calls[0].hosted_tools == [WebSearch()]


async def test_run_uses_dynamic_tool_session_without_global_registration() -> None:
    runtime, scripted = _runtime()
    tool_session = runtime.tools.session()
    tool_session.register(
        lambda topic: ToolResult(content=f"dynamic:{topic}"),
        name="dynamic_lookup",
        description="Temporary lookup.",
        parameters={
            "type": "object",
            "properties": {"topic": {"type": "string"}},
            "required": ["topic"],
        },
    )
    scripted.queue(tool_call_turn(call_id="c1", name="dynamic_lookup", arguments={"topic": "x"}))
    scripted.queue(text_only_turn("Final answer: dynamic:x"))

    result: AgentResult[str] = await runtime.run(
        provider="scripted:test",
        input="use temporary tool",
        tools=["dynamic_lookup"],
        tool_session=tool_session,
    )

    assert "dynamic:x" in result.output
    assert "dynamic_lookup" not in [tool.name for tool in runtime.tools.all_tools()]
    assert scripted.calls[0].tools[0]["name"] == "dynamic_lookup"


async def test_run_dispatches_three_tools_in_one_turn() -> None:
    runtime, scripted = _runtime()
    runtime.tools.register(
        lambda retrieval_source: ToolResult(content="alpha-"),
        name="part_1", description="part 1",
        parameters={"type": "object", "properties": {"retrieval_source": {"type": "string"}}, "required": ["retrieval_source"]},
    )
    runtime.tools.register(
        lambda key_identifier: ToolResult(content="BRAVO-"),
        name="part_2", description="part 2",
        parameters={"type": "object", "properties": {"key_identifier": {"type": "string"}}, "required": ["key_identifier"]},
    )
    runtime.tools.register(
        lambda vault_name: ToolResult(content="charlie123"),
        name="part_3", description="part 3",
        parameters={"type": "object", "properties": {"vault_name": {"type": "string"}}, "required": ["vault_name"]},
    )

    def three_tool_turn(request: Any) -> Any:
        from blackbox.core.events import AgentEvent as Evt
        from blackbox.core.state import ProviderState
        yield Evt(type=EventTypes.MODEL_REQUEST_STARTED, provider="scripted")
        yield Evt(type=EventTypes.TOOL_CALL_REQUESTED, provider="scripted",
                  item_id="c1", data={"call_id": "c1", "name": "part_1",
                                       "arguments": {"retrieval_source": "source_A"}})
        yield Evt(type=EventTypes.TOOL_CALL_REQUESTED, provider="scripted",
                  item_id="c2", data={"call_id": "c2", "name": "part_2",
                                       "arguments": {"key_identifier": "key_B"}})
        yield Evt(type=EventTypes.TOOL_CALL_REQUESTED, provider="scripted",
                  item_id="c3", data={"call_id": "c3", "name": "part_3",
                                       "arguments": {"vault_name": "vault_C"}})
        yield Evt(type=EventTypes.MODEL_COMPLETED, provider="scripted",
                  data={"provider_state": ProviderState(provider="scripted")})

    scripted.queue(three_tool_turn)
    scripted.queue(text_only_turn("alpha-BRAVO-charlie123"))

    result: AgentResult[str] = await runtime.run(
        provider="scripted:test", input="combine all parts",
        tools=["part_1", "part_2", "part_3"],
    )

    completed = [e for e in result.events if e.type == EventTypes.TOOL_CALL_COMPLETED]
    assert len(completed) == 3
    assert completed[0].data["arguments"] == {"retrieval_source": "source_A"}
    assert completed[1].data["arguments"] == {"key_identifier": "key_B"}
    assert completed[2].data["arguments"] == {"vault_name": "vault_C"}
    assert "alpha-BRAVO-charlie123" in result.output


async def test_run_executes_parallel_tool_calls_concurrently() -> None:
    runtime, scripted = _runtime()
    active = 0
    max_active = 0

    def make_tool(part: str) -> Callable[[], Awaitable[ToolResult]]:
        async def tool() -> ToolResult:
            nonlocal active, max_active
            active += 1
            max_active = max(max_active, active)
            await asyncio.sleep(0.01)
            active -= 1
            return ToolResult(content=part)

        return tool

    for name, part in {"part_1": "a", "part_2": "b", "part_3": "c"}.items():
        runtime.tools.register(
            make_tool(part),
            name=name,
            description=name,
            parameters={"type": "object", "properties": {}, "required": []},
        )

    def three_tool_turn(request: Any) -> Any:
        from blackbox.core.events import AgentEvent as Evt
        from blackbox.core.state import ProviderState

        yield Evt(type=EventTypes.MODEL_REQUEST_STARTED, provider="scripted")
        for n in (1, 2, 3):
            yield Evt(
                type=EventTypes.TOOL_CALL_REQUESTED,
                provider="scripted",
                item_id=f"c{n}",
                data={"call_id": f"c{n}", "name": f"part_{n}", "arguments": {}},
            )
        yield Evt(
            type=EventTypes.MODEL_COMPLETED,
            provider="scripted",
            data={"provider_state": ProviderState(provider="scripted")},
        )

    scripted.queue(three_tool_turn)
    scripted.queue(text_only_turn("abc"))

    result: AgentResult[str] = await runtime.run(
        provider="scripted:test",
        input="combine all parts",
        tools=["part_1", "part_2", "part_3"],
    )

    assert max_active == 3
    assert result.text == "abc"


async def test_run_can_limit_tool_concurrency() -> None:
    runtime, scripted = _runtime()
    active = 0
    max_active = 0

    def make_tool(part: str) -> Callable[[], Awaitable[ToolResult]]:
        async def tool() -> ToolResult:
            nonlocal active, max_active
            active += 1
            max_active = max(max_active, active)
            await asyncio.sleep(0.01)
            active -= 1
            return ToolResult(content=part)

        return tool

    for index in range(3):
        runtime.tools.register(
            make_tool(str(index)),
            name=f"limited_{index}",
            description="limited",
            parameters={"type": "object", "properties": {}, "required": []},
        )

    def three_tool_turn(request: Any) -> Any:
        from blackbox.core.events import AgentEvent as Evt
        from blackbox.core.state import ProviderState

        yield Evt(type=EventTypes.MODEL_REQUEST_STARTED, provider="scripted")
        for index in range(3):
            yield Evt(
                type=EventTypes.TOOL_CALL_REQUESTED,
                provider="scripted",
                item_id=f"c{index}",
                data={"call_id": f"c{index}", "name": f"limited_{index}", "arguments": {}},
            )
        yield Evt(
            type=EventTypes.MODEL_COMPLETED,
            provider="scripted",
            data={"provider_state": ProviderState(provider="scripted")},
        )

    scripted.queue(three_tool_turn)
    scripted.queue(text_only_turn("012"))

    await runtime.run(
        provider="scripted:test",
        input="limit tools",
        tools=["limited_0", "limited_1", "limited_2"],
        tool_max_concurrent=1,
    )

    assert max_active == 1


async def test_run_can_timeout_tool_execution() -> None:
    runtime, scripted = _runtime()

    async def slow() -> ToolResult:
        await asyncio.sleep(0.05)
        return ToolResult(content="late")

    runtime.tools.register(slow, name="slow_tool", description="slow")
    scripted.queue(tool_call_turn(call_id="c1", name="slow_tool", arguments={}))
    scripted.queue(text_only_turn("handled timeout"))

    result: AgentResult[str] = await runtime.run(
        provider="scripted:test",
        input="timeout tool",
        tools=["slow_tool"],
        tool_timeout=0.001,
    )

    failed = [event for event in result.events if event.type == EventTypes.TOOL_CALL_FAILED]
    assert failed
    assert "handled timeout" in result.output


# --- context injection ------------------------------------------------------

async def test_run_injects_tool_execution_context_without_schema_visibility() -> None:
    runtime, scripted = _runtime()

    def get_user_password(user_id: str) -> ToolResult:
        passwords = {"user_gamma": "gamma_grape_soda"}
        secret = passwords[user_id]
        return ToolResult(
            content=json.dumps({"status": "retrieved", "snippet": secret[:3] + "***"}),
            payload={"user_id": user_id, "password": secret},
        )

    runtime.tools.register(
        get_user_password,
        name="get_user_password",
        description="Retrieve a user's password.",
        parameters={"type": "object", "properties": {}, "required": []},
    )
    scripted.queue(tool_call_turn(call_id="c1", name="get_user_password", arguments={}))
    scripted.queue(text_only_turn("Password retrieved (snippet): gam***"))

    result: AgentResult[str] = await runtime.run(
        provider="scripted:test",
        input="I need the password.",
        tools=["get_user_password"],
        tool_execution_context={"user_id": "user_gamma"},
    )

    assert "gamma_grape_soda" not in result.text
    assert "gamma_grape_soda" not in result.output
    assert len(result.payloads) == 1
    assert result.payloads[0].tool_name == "get_user_password"
    assert result.payloads[0].payload["password"] == "gamma_grape_soda"
    assert result.payloads[0].payload["user_id"] == "user_gamma"

    schema = next(t for t in runtime.tools.to_provider_tools() if t["name"] == "get_user_password")
    assert "user_id" not in schema["parameters"]["properties"]


# --- deferred payload -------------------------------------------------------

async def test_run_collects_deferred_payloads_from_each_tool() -> None:
    runtime, scripted = _runtime()
    secrets = {1: "Alpha-", 2: "BRAVO-", 3: "Charlie123"}

    def make(part_no: int) -> Any:
        def fn(**_kwargs: Any) -> ToolResult:
            return ToolResult(
                content=json.dumps({"status": "retrieved", "part": part_no}),
                payload={"part_number": part_no, "secret_part": secrets[part_no]},
            )
        return fn

    for n in (1, 2, 3):
        runtime.tools.register(
            make(n),
            name=f"part_{n}", description=f"part {n}",
            parameters={"type": "object", "properties": {}, "required": []},
        )

    def parallel_turn(request: Any) -> Any:
        from blackbox.core.events import AgentEvent as Evt
        from blackbox.core.state import ProviderState
        yield Evt(type=EventTypes.MODEL_REQUEST_STARTED, provider="scripted")
        for n in (1, 2, 3):
            yield Evt(type=EventTypes.TOOL_CALL_REQUESTED, provider="scripted",
                      item_id=f"c{n}", data={"call_id": f"c{n}", "name": f"part_{n}", "arguments": {}})
        yield Evt(type=EventTypes.MODEL_COMPLETED, provider="scripted",
                  data={"provider_state": ProviderState(provider="scripted")})

    scripted.queue(parallel_turn)
    scripted.queue(text_only_turn("All three parts retrieved; assembled externally."))

    result: AgentResult[str] = await runtime.run(
        provider="scripted:test", input="get all three parts",
        tools=["part_1", "part_2", "part_3"],
    )

    assert len(result.payloads) == 3
    assembled = "".join(
        p.payload["secret_part"]
        for p in sorted(result.payloads, key=lambda p: p.payload["part_number"])
    )
    assert assembled == "Alpha-BRAVO-Charlie123"
    assert "Alpha-BRAVO-Charlie123" not in result.output


# --- mock tools -------------------------------------------------------------

async def test_run_with_mock_tools_does_not_invoke_real_function() -> None:
    runtime, scripted = _runtime()
    state = {"called": False}

    def real(value: int) -> ToolResult:
        state["called"] = True
        return ToolResult(content=f"real:{value}")

    runtime.tools.register(
        real, name="real",
        parameters={"type": "object", "properties": {"value": {"type": "integer"}}, "required": ["value"]},
    )
    scripted.queue(tool_call_turn(call_id="c1", name="real", arguments={"value": 1}))
    scripted.queue(text_only_turn("done"))

    result: AgentResult[str] = await runtime.run(
        provider="scripted:test", input="x", tools=["real"], mock_tools=True,
    )

    assert state["called"] is False
    completed = next(e for e in result.events if e.type == EventTypes.TOOL_CALL_COMPLETED)
    assert "Mocked execution" in completed.data["content"]


# --- policy gate ------------------------------------------------------------

class _DenyAll:
    async def check(self, request: PolicyRequest) -> PolicyDecision:
        if request.checkpoint == "before_tool_call":
            return PolicyDecision.deny("blocked by test policy")
        return PolicyDecision.allow()


async def test_policy_deny_short_circuits_tool_dispatch() -> None:
    runtime, scripted = _runtime()
    state = {"called": False}

    def tool() -> ToolResult:
        state["called"] = True
        return ToolResult(content="ok")

    runtime.tools.register(
        tool, name="t",
        parameters={"type": "object", "properties": {}, "required": []},
    )
    scripted.queue(tool_call_turn(call_id="c1", name="t", arguments={}))
    scripted.queue(text_only_turn("done"))

    policy: Policy = _DenyAll()
    result: AgentResult[str] = await runtime.run(
        provider="scripted:test", input="x", tools=["t"], policy=policy,
    )

    assert state["called"] is False
    started = [e for e in result.events if e.type == EventTypes.TOOL_CALL_STARTED]
    assert started == []

    sent_back = scripted.calls[1].input
    assert isinstance(sent_back, list)
    assert sent_back[0].data["error"] == "denied_by_policy"
    assert sent_back[0].data["reason"] == "blocked by test policy"


class _AllowAll:
    def __init__(self) -> None:
        self.seen: list[PolicyRequest] = []

    async def check(self, request: PolicyRequest) -> PolicyDecision:
        self.seen.append(request)
        return PolicyDecision.allow("allowed by test policy")


async def test_policy_allow_dispatches_tool() -> None:
    runtime, scripted = _runtime()
    state = {"called": False}

    def tool() -> ToolResult:
        state["called"] = True
        return ToolResult(content="ok")

    runtime.tools.register(
        tool, name="t",
        parameters={"type": "object", "properties": {}, "required": []},
    )
    scripted.queue(tool_call_turn(call_id="c1", name="t", arguments={}))
    scripted.queue(text_only_turn("done"))

    policy = _AllowAll()
    result: AgentResult[str] = await runtime.run(
        provider="scripted:test", input="x", tools=["t"], policy=policy,
    )

    assert state["called"] is True
    assert [request.checkpoint for request in policy.seen] == [
        "before_tool_exposure",
        "before_tool_call",
    ]
    assert result.text == "done"


async def test_workspace_tools_run_through_agent_loop(tmp_path: Path) -> None:
    (tmp_path / "notes.txt").write_text("ship it")
    runtime, scripted = _runtime()
    workspace_runtime = WorkspaceRuntime()
    workspace = await workspace_runtime.open(WorkspaceSpec.local(tmp_path))
    registered = runtime.tools.register_workspace(workspace_runtime, workspace)

    scripted.queue(
        tool_call_turn(
            call_id="ws_1",
            name="workspace_read_file",
            arguments={"path": "notes.txt"},
        )
    )
    scripted.queue(text_only_turn("done"))

    result: AgentResult[str] = await runtime.run(
        provider="scripted:test",
        input="read notes",
        tools=[tool.name for tool in registered],
    )

    assert result.text == "done"
    assert len(result.payloads) == 1
    payload = result.payloads[0]
    assert payload.tool_name == "workspace_read_file"
    assert payload.call_id == "ws_1"
    assert payload.payload == {"path": "notes.txt", "content": "ship it"}
    assert any(event.type == EventTypes.WORKSPACE_FILE_READ for event in result.events)
    assert workspace_runtime.drain_events() == []


async def test_workspace_write_command_and_snapshot_tools_run_through_agent_loop(
    tmp_path: Path,
) -> None:
    async def executor(spec: CommandSpec, ws: WorkspaceRef) -> CommandResult:
        return CommandResult(exit_code=0, stdout=f"ran:{spec.display}")

    runtime, scripted = _runtime()
    workspace_runtime = WorkspaceRuntime(executor=executor)
    workspace = await workspace_runtime.open(WorkspaceSpec.local(tmp_path))
    registered = runtime.tools.register_workspace(workspace_runtime, workspace)

    def workspace_turn(request: Any) -> Any:
        from blackbox.core.events import AgentEvent as Evt
        from blackbox.core.state import ProviderState

        yield Evt(type=EventTypes.MODEL_REQUEST_STARTED, provider="scripted")
        yield Evt(
            type=EventTypes.TOOL_CALL_REQUESTED,
            provider="scripted",
            item_id="ws_write",
            data={
                "call_id": "ws_write",
                "name": "workspace_write_file",
                "arguments": {"path": "notes.txt", "content": "ship it"},
            },
        )
        yield Evt(
            type=EventTypes.TOOL_CALL_REQUESTED,
            provider="scripted",
            item_id="ws_cmd",
            data={
                "call_id": "ws_cmd",
                "name": "workspace_run_command",
                "arguments": {"command": "echo ok"},
            },
        )
        yield Evt(
            type=EventTypes.TOOL_CALL_REQUESTED,
            provider="scripted",
            item_id="ws_snapshot",
            data={
                "call_id": "ws_snapshot",
                "name": "workspace_snapshot",
                "arguments": {"name": "after-write"},
            },
        )
        yield Evt(
            type=EventTypes.MODEL_COMPLETED,
            provider="scripted",
            data={"provider_state": ProviderState(provider="scripted")},
        )

    scripted.queue(workspace_turn)
    scripted.queue(text_only_turn("done"))

    result: AgentResult[str] = await runtime.run(
        provider="scripted:test",
        input="update workspace",
        tools=[tool.name for tool in registered],
    )

    assert result.text == "done"
    assert (tmp_path / "notes.txt").read_text() == "ship it"
    assert [payload.tool_name for payload in result.payloads] == [
        "workspace_write_file",
        "workspace_run_command",
        "workspace_snapshot",
    ]
    event_types = [event.type for event in result.events]
    assert EventTypes.WORKSPACE_FILE_CHANGED in event_types
    assert EventTypes.WORKSPACE_COMMAND_STARTED in event_types
    assert EventTypes.WORKSPACE_COMMAND_COMPLETED in event_types
    assert EventTypes.ARTIFACT_CREATED in event_types
    assert workspace_runtime.drain_events() == []
