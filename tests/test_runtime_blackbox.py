"""High-level AgentRuntime.run / .stream tests.

These tests are the offline analogues of the llm_factory_toolkit v1 tests in
``llm_toolkit/tests/`` (test_llmcall, test_llmcall_pydantic_response,
test_llmcall_tools, test_llmcall_multiple_tools, test_llmcall_tools_with_context,
test_llmcall_deferred_payload, test_mock_tools). They exercise the same
product contract: the runtime owns the tool loop and returns a typed result.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import pytest
from pydantic import BaseModel, Field

from agent_runtime import AgentResult, AgentRuntime, EventTypes, OutputValidationError
from agent_runtime.tools import ToolResult
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


# --- text-only ---------------------------------------------------------------

async def test_run_returns_text_when_no_output_type() -> None:
    runtime, scripted = _runtime()
    scripted.queue(text_only_turn("Paris is the capital of France."))

    result: AgentResult[str] = await runtime.run(
        provider="scripted/test", input="What is the capital of France?",
    )

    assert isinstance(result, AgentResult)
    assert result.output == "Paris is the capital of France."
    assert result.text == result.output
    assert any(e.type == EventTypes.MODEL_TEXT_DELTA for e in result.events)


# --- pydantic structured output ---------------------------------------------

class ExtractedInfo(BaseModel):
    name: str = Field(description="Main person's name")
    age: int
    location: str
    sentiment: str


async def test_run_validates_pydantic_output_type() -> None:
    runtime, scripted = _runtime()
    payload = {
        "name": "Sarah",
        "age": 35,
        "location": "Paris",
        "sentiment": "positive",
    }
    scripted.queue(text_only_turn(json.dumps(payload)))

    result = await runtime.run(
        provider="scripted/test",
        input="Extract info.",
        output_type=ExtractedInfo,
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
            provider="scripted/test",
            input="x",
            output_type=ExtractedInfo,
        )
    assert info.value.raw_text == "not even json"


async def test_run_validates_dataclass_output_type() -> None:
    @dataclass
    class Decision:
        priority: str
        notes: str

    runtime, scripted = _runtime()
    scripted.queue(text_only_turn(json.dumps({"priority": "high", "notes": "x"})))

    result = await runtime.run(
        provider="scripted/test", input="x", output_type=Decision,
    )
    assert isinstance(result.output, Decision)
    assert result.output.priority == "high"


# --- single tool -------------------------------------------------------------

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

    result = await runtime.run(
        provider="scripted/test", input="get me the secret", tools=["get_secret"],
    )

    assert "secret=abc-XYZ" in result.output
    assert any(e.type == EventTypes.TOOL_CALL_COMPLETED for e in result.events)


# --- multiple tools in one turn ---------------------------------------------

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
        from agent_runtime.core.events import AgentEvent as Evt
        from agent_runtime.core.state import ProviderState
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

    result = await runtime.run(
        provider="scripted/test", input="combine all parts",
        tools=["part_1", "part_2", "part_3"],
    )

    completed = [e for e in result.events if e.type == EventTypes.TOOL_CALL_COMPLETED]
    assert len(completed) == 3
    assert "alpha-BRAVO-charlie123" in result.output


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

    result = await runtime.run(
        provider="scripted/test",
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


# --- deferred payload pattern -----------------------------------------------

async def test_run_collects_deferred_payloads_from_each_tool() -> None:
    runtime, scripted = _runtime()
    secrets = {1: "Alpha-", 2: "BRAVO-", 3: "Charlie123"}

    def make(part_no: int):
        def fn(**_kwargs: Any) -> ToolResult:
            return ToolResult(
                content=json.dumps({"status": "retrieved", "part": part_no}),
                payload={"part_number": part_no, "secret_part": secrets[part_no]},
            )
        return fn

    for n in (1, 2, 3):
        runtime.tools.register(
            make(n),
            name=f"part_{n}",
            description=f"part {n}",
            parameters={"type": "object", "properties": {}, "required": []},
        )

    def parallel_turn(request: Any) -> Any:
        from agent_runtime.core.events import AgentEvent as Evt
        from agent_runtime.core.state import ProviderState
        yield Evt(type=EventTypes.MODEL_REQUEST_STARTED, provider="scripted")
        for n in (1, 2, 3):
            yield Evt(type=EventTypes.TOOL_CALL_REQUESTED, provider="scripted",
                      item_id=f"c{n}", data={"call_id": f"c{n}", "name": f"part_{n}",
                                              "arguments": {}})
        yield Evt(type=EventTypes.MODEL_COMPLETED, provider="scripted",
                  data={"provider_state": ProviderState(provider="scripted")})

    scripted.queue(parallel_turn)
    scripted.queue(text_only_turn("All three parts retrieved; assembled externally."))

    result = await runtime.run(
        provider="scripted/test",
        input="get all three parts",
        tools=["part_1", "part_2", "part_3"],
    )

    assert len(result.payloads) == 3
    assembled = "".join(
        p.payload["secret_part"]
        for p in sorted(result.payloads, key=lambda p: p.payload["part_number"])
    )
    assert assembled == "Alpha-BRAVO-Charlie123"
    # Final text deliberately does NOT contain the assembled secret
    assert "Alpha-BRAVO-Charlie123" not in result.output


# --- mock tools --------------------------------------------------------------

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

    result = await runtime.run(
        provider="scripted/test", input="x", tools=["real"], mock_tools=True,
    )

    assert state["called"] is False
    completed = next(e for e in result.events if e.type == EventTypes.TOOL_CALL_COMPLETED)
    assert "Mocked execution" in completed.data["content"]


# --- stream parity -----------------------------------------------------------

async def test_stream_yields_same_events_as_run_collects() -> None:
    runtime, scripted = _runtime()
    runtime.tools.register(
        lambda x: ToolResult(content=f"r{x}"),
        name="t",
        parameters={"type": "object", "properties": {"x": {"type": "integer"}}, "required": ["x"]},
    )
    scripted.queue(tool_call_turn(call_id="c1", name="t", arguments={"x": 1}))
    scripted.queue(text_only_turn("done"))

    streamed = [
        e async for e in runtime.stream(
            provider="scripted/test", input="x", tools=["t"],
        )
    ]
    types = [e.type for e in streamed]
    assert EventTypes.TOOL_CALL_REQUESTED in types
    assert EventTypes.TOOL_CALL_STARTED in types
    assert EventTypes.TOOL_CALL_COMPLETED in types
    assert EventTypes.MODEL_TEXT_DELTA in types
