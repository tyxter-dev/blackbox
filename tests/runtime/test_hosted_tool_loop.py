from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from agent_runtime import AgentRuntime, EventTypes
from agent_runtime.core.events import AgentEvent
from agent_runtime.core.items import ItemTypes
from agent_runtime.core.state import ProviderState
from agent_runtime.hosted_tools import ApplyPatch, HostedToolHandlers, Shell
from agent_runtime.tools.hosted import HostedToolCall, HostedToolContext, HostedToolOutput
from tests.fixtures.scripted_model import ScriptedModelProvider, text_only_turn


@dataclass
class RecordingHostedHandler:
    hosted_tool_type: str
    content: str
    calls: list[HostedToolCall]

    async def handle(
        self, call: HostedToolCall, context: HostedToolContext
    ) -> HostedToolOutput:
        self.calls.append(call)
        return HostedToolOutput(
            provider=call.provider,
            hosted_tool_type=call.hosted_tool_type,
            call_id=call.call_id,
            status="completed",
            content=self.content,
            payload={"output": self.content},
        )


def _runtime() -> tuple[AgentRuntime, ScriptedModelProvider]:
    runtime = AgentRuntime()
    scripted = ScriptedModelProvider()
    runtime.registry.register_model(scripted)
    return runtime, scripted


def _hosted_call_turn(
    *,
    hosted_tool_type: str,
    provider_item_type: str,
    call_id: str,
    arguments: dict[str, Any],
) -> Any:
    def script(request: Any) -> Iterable[AgentEvent]:
        yield AgentEvent(type=EventTypes.MODEL_REQUEST_STARTED, provider="scripted")
        yield AgentEvent(
            type=EventTypes.HOSTED_TOOL_CALL_REQUESTED,
            provider="scripted",
            item_id=call_id,
            data={
                "hosted_tool_type": hosted_tool_type,
                "provider_item_type": provider_item_type,
                "call_id": call_id,
                "arguments": arguments,
                "requires_continuation": True,
            },
            raw={"type": provider_item_type, "call_id": call_id},
        )
        yield AgentEvent(
            type=EventTypes.MODEL_COMPLETED,
            provider="scripted",
            data={"provider_state": ProviderState(provider="scripted", previous_response_id="r1")},
        )

    return script


async def test_runtime_executes_hosted_shell_through_handler_and_continues() -> None:
    runtime, scripted = _runtime()
    scripted.queue(
        _hosted_call_turn(
            hosted_tool_type="shell",
            provider_item_type="shell_call",
            call_id="shell_1",
            arguments={"command": "echo hi"},
        )
    )
    scripted.queue(text_only_turn("done"))
    calls: list[HostedToolCall] = []
    handler = RecordingHostedHandler("shell", "hi", calls)

    result = await runtime.run(
        provider="scripted:test",
        input="run shell",
        hosted_tools=[Shell(execution="local")],
        hosted_tool_handlers=HostedToolHandlers(shell=handler),
    )

    assert result.text == "done"
    assert calls[0].arguments == {"command": "echo hi"}
    assert scripted.calls[1].input
    continuation = scripted.calls[1].input[0]
    assert continuation.type == ItemTypes.HOSTED_TOOL_RESULT
    assert continuation.data["provider_input_item"]["type"] == "shell_call_output"
    assert EventTypes.HOSTED_TOOL_CALL_STARTED in [event.type for event in result.events]
    assert EventTypes.HOSTED_TOOL_OUTPUT_PREPARED in [event.type for event in result.events]


async def test_runtime_executes_apply_patch_through_handler_and_continues() -> None:
    runtime, scripted = _runtime()
    scripted.queue(
        _hosted_call_turn(
            hosted_tool_type="apply_patch",
            provider_item_type="apply_patch_call",
            call_id="patch_1",
            arguments={"diff": "*** Begin Patch\n*** End Patch"},
        )
    )
    scripted.queue(text_only_turn("patched"))
    calls: list[HostedToolCall] = []
    handler = RecordingHostedHandler("apply_patch", "applied", calls)

    result = await runtime.run(
        provider="scripted:test",
        input="patch",
        hosted_tools=[ApplyPatch(workspace_id="ws_1")],
        hosted_tool_handlers=HostedToolHandlers(apply_patch=handler),
    )

    assert result.text == "patched"
    continuation = scripted.calls[1].input[0]
    assert continuation.data["provider_input_item"]["type"] == "apply_patch_call_output"
    assert result.items[-1].type == ItemTypes.HOSTED_TOOL_RESULT
