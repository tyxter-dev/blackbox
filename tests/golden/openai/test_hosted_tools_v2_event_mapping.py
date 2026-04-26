from __future__ import annotations

from agent_runtime import AgentRuntime, EventTypes
from agent_runtime.core.items import ItemTypes
from agent_runtime.models.openai_responses import OpenAIResponsesProvider
from tests.fixtures.fake_openai_client import FakeOpenAIClient, evt, final_response, item


def _runtime_with(client: FakeOpenAIClient) -> AgentRuntime:
    runtime = AgentRuntime()
    runtime.registry.register_model(OpenAIResponsesProvider(client=client))
    return runtime


async def test_openai_shell_call_maps_to_hosted_call_requested() -> None:
    client = FakeOpenAIClient()
    shell = item(
        "shell_call",
        id_="sh_1",
        call_id="call_shell",
        status="requires_action",
        arguments={"command": "pwd"},
    )
    client.queue(
        events=[
            evt("response.output_item.added", item=shell),
            evt("response.output_item.done", item=shell),
        ],
        final_response=final_response(id_="resp_shell", output=[shell]),
    )

    runtime = _runtime_with(client)
    result = await runtime.models.run(provider="openai/gpt-5.4", input="shell")

    requested = next(
        event for event in result.events if event.type == EventTypes.HOSTED_TOOL_CALL_REQUESTED
    )
    assert requested.data["hosted_tool_type"] == "shell"
    assert requested.data["provider_item_type"] == "shell_call"
    assert requested.data["requires_continuation"] is True
    assert requested.data["item"].type == ItemTypes.HOSTED_TOOL_CALL
    assert requested.data["item"].status == "requires_action"


async def test_openai_apply_patch_call_output_maps_to_hosted_result() -> None:
    client = FakeOpenAIClient()
    output = item(
        "apply_patch_call_output",
        id_="apo_1",
        call_id="patch_1",
        status="completed",
        output="applied",
    )
    client.queue(
        events=[evt("response.output_item.done", item=output)],
        final_response=final_response(id_="resp_patch", output=[output]),
    )

    runtime = _runtime_with(client)
    result = await runtime.models.run(provider="openai/gpt-5.4", input="patch")

    completed = next(
        event for event in result.events if event.type == EventTypes.HOSTED_TOOL_CALL_COMPLETED
    )
    assert completed.data["hosted_tool_type"] == "apply_patch"
    assert completed.data["item"].type == ItemTypes.HOSTED_TOOL_RESULT
    assert completed.data["item"].data["output"] == "applied"
