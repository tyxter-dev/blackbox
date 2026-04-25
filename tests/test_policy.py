from __future__ import annotations

from agent_runtime import AgentRuntime, EventTypes
from agent_runtime.core.policy import Policy, PolicyDecision, PolicyRequest
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
    result = await runtime.run(
        provider="scripted/test", input="x", tools=["t"], policy=policy,
    )

    assert state["called"] is False
    started = [e for e in result.events if e.type == EventTypes.TOOL_CALL_STARTED]
    assert started == []  # tool was never started

    sent_back = scripted.calls[1].input
    assert sent_back[0].data["error"] == "denied_by_policy"
    assert sent_back[0].data["reason"] == "blocked by test policy"
