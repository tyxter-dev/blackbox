from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Any

import pytest

from blackbox import (
    AgentRuntime,
    EventTypes,
    RuntimeConfig,
    ToolPermission,
    WebSearch,
    WorkspaceAgentSpec,
)
from blackbox.core.approvals import ApprovalDecision
from blackbox.core.errors import ConfigurationError, UnsupportedFeatureError
from blackbox.core.policy import AllowAllPolicy, PolicyDecision, PolicyRequest
from blackbox.core.tool_permissions import active_permissions, permission_boundary
from blackbox.providers.agent_adapters.local import LocalAgentProvider
from blackbox.tools import ToolRuntime
from blackbox.tools.hosted.specs import HostedToolRaw
from blackbox.workspace_agents.permissions import (
    ApprovalRequirement,
    ConnectorSpec,
    compile_package_permissions,
)
from blackbox.workspace_agents.runtime import run_workspace_agent
from tests.fixtures.scripted_model import ScriptedModelProvider, text_only_turn, tool_call_turn


def setup() -> tuple[AgentRuntime, ScriptedModelProvider, list[str]]:
    runtime = AgentRuntime()
    scripted = ScriptedModelProvider()
    runtime.registry.register_model(scripted)
    effects: list[str] = []
    runtime.tools.register(
        lambda: effects.append("read") or "allowed", name="read", scopes=["read"]
    )
    runtime.tools.register(lambda: effects.append("write") or "bad", name="write", scopes=["write"])
    return runtime, scripted, effects


def spec(**kwargs: Any) -> WorkspaceAgentSpec:
    return WorkspaceAgentSpec(
        name="guarded",
        model_provider="scripted",
        model="scripted:test",
        tools=["read", "write"],
        permissions=[ToolPermission("read")],
        permission_mode="allowlist_v1",
        **kwargs,
    )


@pytest.mark.parametrize("surface", ["model", "local"])
async def test_package_walkthrough(surface: str) -> None:
    runtime, scripted, effects = setup()
    package = spec()
    if surface == "local":
        runtime.registry.register_agent(
            LocalAgentProvider(runtime.models, tools=ToolRuntime(runtime.tools.registry))
        )
        package = replace(package, agent_provider="local")
    scripted.queue(tool_call_turn(call_id="r", name="read", arguments={}))
    scripted.queue(tool_call_turn(call_id="w", name="write", arguments={}))
    scripted.queue(text_only_turn("finished"))
    result = await run_workspace_agent(runtime, package, input="go")
    assert result.text == "finished"
    assert effects == ["read"]
    assert all({tool["name"] for tool in call.tools} == {"read"} for call in scripted.calls)
    assert scripted.calls[2].input[0].status == "failed"
    assert any(event.type == EventTypes.TOOL_CHOICE_REJECTED for event in result.events)
    assert not active_permissions()


@pytest.mark.parametrize(
    "permissions",
    [[], [ToolPermission("read", scopes=["write"])], [ToolPermission("read", connector="crm")]],
)
async def test_deny_all_wrong_scope_and_binding(permissions: list[ToolPermission]) -> None:
    runtime, scripted, effects = setup()
    package = replace(
        spec(),
        permissions=permissions,
        connectors=[ConnectorSpec("crm", "test", tool_refs=["read"])],
    )
    scripted.queue(tool_call_turn(call_id="r", name="read", arguments={}))
    scripted.queue(text_only_turn("safe"))
    await run_workspace_agent(
        runtime,
        package,
        input="go",
        tools=["read", "write"],
        policy=AllowAllPolicy(),
        config=RuntimeConfig(overrides={"tools": ["write"]}),
    )
    assert effects == []
    assert scripted.calls[0].tools == []
    assert scripted.calls[1].input[0].status == "failed"


async def test_inherit_and_context_isolation() -> None:
    runtime, _, effects = setup()
    constraints = compile_package_permissions([], [])
    barrier = asyncio.Event()

    async def denied() -> None:
        with permission_boundary((constraints,)):
            barrier.set()
            await asyncio.sleep(0)
            result = await ToolRuntime(runtime.tools.registry).call("read")
            assert result.error == "denied_by_policy"

    async def allowed() -> None:
        await barrier.wait()
        result = await ToolRuntime(runtime.tools.registry).call("write")
        assert result.ok

    await asyncio.gather(denied(), allowed())
    assert effects == ["write"]
    runtime, scripted, effects = setup()
    scripted.queue(tool_call_turn(call_id="w", name="write", arguments={}))
    scripted.queue(text_only_turn("done"))
    await run_workspace_agent(runtime, replace(spec(), permission_mode="inherit"), input="go")
    assert effects == ["write"]


async def test_dynamic_catalog_and_load_exclude_denied_tools() -> None:
    runtime, scripted, effects = setup()
    scripted.queue(tool_call_turn(call_id="s", name="search_tools", arguments={}))
    scripted.queue(
        tool_call_turn(call_id="l", name="load_tools", arguments={"tool_names": ["write"]})
    )
    scripted.queue(tool_call_turn(call_id="w", name="write", arguments={}))
    scripted.queue(text_only_turn("done"))
    await run_workspace_agent(runtime, spec(), input="go", tool_selection="dynamic")
    assert effects == []
    assert '"name": "write"' not in scripted.calls[1].input[0].data["content"]
    assert '"write"' in scripted.calls[2].input[0].data["content"]
    assert all("write" not in {tool["name"] for tool in call.tools} for call in scripted.calls)


async def test_fresh_dispatch_rejects_registry_replacement() -> None:
    runtime, scripted, effects = setup()

    class ReplaceDuringPolicy:
        async def check(self, request: PolicyRequest) -> PolicyDecision:
            if request.checkpoint == "before_tool_call":
                runtime.tools.register(
                    lambda: effects.append("replaced"), name="read", scopes=["write"]
                )
            return PolicyDecision.allow()

    scripted.queue(tool_call_turn(call_id="r", name="read", arguments={}))
    scripted.queue(text_only_turn("done"))
    await run_workspace_agent(runtime, spec(), input="go", policy=ReplaceDuringPolicy())
    assert effects == []
    assert scripted.calls[1].input[0].status == "failed"


async def test_fresh_dispatch_after_approval() -> None:
    runtime, scripted, effects = setup()
    local = LocalAgentProvider(runtime.models, tools=ToolRuntime(runtime.tools.registry))
    runtime.registry.register_agent(local)
    package = replace(
        spec(),
        agent_provider="local",
        permissions=[ToolPermission("read", approval=ApprovalRequirement("always"))],
    )
    scripted.queue(tool_call_turn(call_id="r", name="read", arguments={}))
    scripted.queue(text_only_turn("done"))
    task = asyncio.create_task(run_workspace_agent(runtime, package, input="go"))
    for _ in range(100):
        if local._approvals:
            break
        await asyncio.sleep(0.001)
    assert local._approvals
    runtime.tools.register(lambda: effects.append("replaced"), name="read", scopes=["read"])
    await local.approve(next(iter(local._approvals)), ApprovalDecision(approved=True))
    await task
    assert effects == []
    assert scripted.calls[1].input[0].status == "failed"


async def test_local_followup_keeps_boundary() -> None:
    runtime, scripted, effects = setup()
    local = LocalAgentProvider(runtime.models, tools=ToolRuntime(runtime.tools.registry))
    runtime.registry.register_agent(local)
    scripted.queue(text_only_turn("ready"))
    await run_workspace_agent(runtime, replace(spec(), agent_provider="local"), input="go")
    session = next(iter(local._sessions.values()))
    scripted.queue(tool_call_turn(call_id="w", name="write", arguments={}))
    scripted.queue(text_only_turn("safe"))
    await local.send_message(session, "write now")
    events = []
    async for event in local.stream_events(session):
        assert active_permissions() == ()
        events.append(event)
    assert effects == []
    assert any(event.type == EventTypes.TOOL_CHOICE_REJECTED for event in events)


async def test_hosted_websearch_and_opaque_config() -> None:
    runtime, scripted, _ = setup()
    scripted.queue(text_only_turn("search"))
    package = replace(
        spec(), hosted_tools=[WebSearch()], permissions=[ToolPermission("hosted:web_search")]
    )
    await run_workspace_agent(runtime, package, input="go")
    assert scripted.calls[0].hosted_tools == [WebSearch()]
    scripted.queue(text_only_turn("none"))
    await run_workspace_agent(runtime, replace(package, permissions=[]), input="go")
    assert scripted.calls[1].hosted_tools == []
    with pytest.raises(UnsupportedFeatureError):
        await run_workspace_agent(
            runtime,
            replace(package, hosted_tools=[HostedToolRaw("scripted", {"type": "evil"})]),
            input="go",
        )
    with pytest.raises(ConfigurationError):
        await run_workspace_agent(runtime, package, input="go", extra={"tools": [{"name": "evil"}]})


@pytest.mark.parametrize("allowed", [False, True])
async def test_workspace_permissions(tmp_path: Any, allowed: bool) -> None:
    from blackbox.workspaces import WorkspaceSpec

    runtime, scripted, _ = setup()
    (tmp_path / "note.txt").write_text("original")
    package = replace(
        spec(), tools=[], permissions=[ToolPermission("workspace:read_file")] if allowed else []
    )
    scripted.queue(
        tool_call_turn(call_id="r", name="workspace_read_file", arguments={"path": "note.txt"})
    )
    scripted.queue(
        tool_call_turn(
            call_id="w",
            name="workspace_write_file",
            arguments={"path": "note.txt", "content": "bad"},
        )
    )
    scripted.queue(text_only_turn("done"))
    await run_workspace_agent(runtime, package, input="go", workspace=WorkspaceSpec.local(tmp_path))
    assert (tmp_path / "note.txt").read_text() == "original"
    assert scripted.calls[1].input[0].status == ("completed" if allowed else "failed")
    assert scripted.calls[2].input[0].status == "failed"


@pytest.mark.parametrize("allowed", [False, True])
async def test_mcp_permissions(monkeypatch: Any, allowed: bool) -> None:
    import blackbox.mcp.connector as module
    from blackbox.mcp import MCPToolset
    from tests.runtime.test_mcp_toolset_runtime import _FakeTransport, _trusted_ticket_spec

    transport = _FakeTransport()
    monkeypatch.setattr(module, "transport_for_spec", lambda spec, auth_provider=None: transport)
    runtime, scripted, _ = setup()
    package = replace(
        spec(),
        tools=[],
        mcp_toolsets=[MCPToolset(server=_trusted_ticket_spec(), mode="local")],
        permissions=[ToolPermission("mcp:tickets.lookup", scopes=["execute"])] if allowed else [],
    )
    scripted.queue(
        tool_call_turn(call_id="m", name="mcp:tickets.lookup", arguments={"ticket_id": "1"})
    )
    scripted.queue(text_only_turn("done"))
    await run_workspace_agent(runtime, package, input="go")
    assert any(method == "tools/call" for method, _ in transport.requests) == allowed
    assert scripted.calls[1].input[0].status == ("completed" if allowed else "failed")
    assert transport.stopped


@pytest.mark.parametrize("allowed", [False, True])
async def test_client_hosted_permissions(allowed: bool) -> None:
    from blackbox.tools.hosted.specs import HostedToolHandlers, Shell
    from tests.runtime.test_hosted_tool_loop import RecordingHostedHandler, _hosted_call_turn

    runtime, scripted, _ = setup()
    package = replace(
        spec(),
        hosted_tools=[Shell(execution="local", require_approval=False)],
        permissions=[ToolPermission("hosted:shell", scopes=["execute"])] if allowed else [],
    )
    calls: list[Any] = []
    scripted.queue(
        _hosted_call_turn(
            hosted_tool_type="shell",
            provider_item_type="shell_call",
            call_id="s",
            arguments={"command": "echo yes"},
        )
    )
    scripted.queue(text_only_turn("done"))
    await run_workspace_agent(
        runtime,
        package,
        input="go",
        hosted_tool_handlers=HostedToolHandlers(
            shell=RecordingHostedHandler("shell", "yes", calls)
        ),
    )
    assert bool(calls) == allowed
    assert scripted.calls[1].input[0].status == ("completed" if allowed else "failed")


async def test_mcp_fresh_descriptor_rejects_changed_scopes() -> None:
    from blackbox.core.errors import MCPError
    from blackbox.mcp import MCPConnector, MCPServerSpec

    effects: list[str] = []
    connector = MCPConnector([MCPServerSpec(name="tickets", transport="stdio")])
    definition = connector.register_tool(
        "tickets", "lookup", lambda: effects.append("bad"), metadata={"read_only": True}
    )

    class ChangeDescriptor:
        async def check(self, request: PolicyRequest) -> PolicyDecision:
            definition.metadata["read_only"] = False
            return PolicyDecision.allow()

    connector.policy = ChangeDescriptor()
    with permission_boundary(
        (compile_package_permissions([ToolPermission("mcp:tickets.lookup")], []),)
    ):
        with pytest.raises(MCPError):
            await connector.call_tool("tickets", "lookup")
    assert effects == []


async def test_timeout_pins_checked_callable(monkeypatch: Any) -> None:
    import blackbox.tools.runtime as tool_runtime_module

    original_wait_for = asyncio.wait_for

    async def delayed_wait_for(coro: Any, **options: Any) -> Any:
        await asyncio.sleep(0)
        return await original_wait_for(coro, **options)

    monkeypatch.setattr(tool_runtime_module.asyncio, "wait_for", delayed_wait_for)
    runtime, _, effects = setup()
    definition = runtime.tools.registry.get("read")
    boundary = compile_package_permissions([ToolPermission("read")], [])
    with permission_boundary((boundary,)):
        task = asyncio.create_task(ToolRuntime(runtime.tools.registry, timeout=1).call("read"))
        await asyncio.sleep(0)
        definition.function = lambda: effects.append("unchecked")
        await task
    assert effects == ["read"]


async def test_routing_alias_cannot_expose_unauthorized_schema() -> None:
    from blackbox.tools.routing import ToolRoutingSpec

    runtime, scripted, effects = setup()
    runtime.tools.registry.get("write").metadata["route_ref"] = "local:alias"
    scripted.queue(tool_call_turn(call_id="w", name="write", arguments={}))
    scripted.queue(text_only_turn("done"))
    await run_workspace_agent(
        runtime,
        replace(spec(), permissions=[ToolPermission("local:alias", scopes=["write"])]),
        input="write",
        tools="dynamic",
        tool_routing=ToolRoutingSpec(mode="auto"),
    )
    assert effects == []
    assert all("write" not in {tool["name"] for tool in call.tools} for call in scripted.calls)


async def test_local_stream_close_and_positive_approval() -> None:
    runtime, scripted, effects = setup()
    local = LocalAgentProvider(runtime.models, tools=ToolRuntime(runtime.tools.registry))
    runtime.registry.register_agent(local)
    package = replace(
        spec(),
        agent_provider="local",
        permissions=[ToolPermission("read", approval=ApprovalRequirement("always"))],
    )
    scripted.queue(tool_call_turn(call_id="r", name="read", arguments={}))
    scripted.queue(text_only_turn("done"))
    task = asyncio.create_task(run_workspace_agent(runtime, package, input="go"))
    for _ in range(100):
        if local._approvals:
            break
        await asyncio.sleep(0.001)
    await local.approve(next(iter(local._approvals)), ApprovalDecision(approved=True))
    await task
    assert effects == ["read"]
    session = next(iter(local._sessions.values()))
    scripted.queue(text_only_turn("next"))
    await local.send_message(session, "next")
    stream = local.stream_events(session)
    await anext(stream)
    assert active_permissions() == ()
    await stream.aclose()
    assert active_permissions() == ()


async def test_finalizer_survives_deny_all() -> None:
    from blackbox import OutputSpec

    runtime, scripted, effects = setup()
    package = replace(spec(), permissions=[])
    scripted.queue(tool_call_turn(call_id="f", name="submit_final_output", arguments={"ok": True}))
    result = await run_workspace_agent(
        runtime,
        package,
        input="go",
        output_spec=OutputSpec(
            schema={
                "type": "object",
                "properties": {"ok": {"type": "boolean"}},
                "required": ["ok"],
            },
            strategy="finalizer_tool",
        ),
    )
    assert result.output == {"ok": True}
    assert effects == []


async def test_cancellation_resets_package_boundary() -> None:
    runtime, scripted, effects = setup()
    local = LocalAgentProvider(runtime.models, tools=ToolRuntime(runtime.tools.registry))
    runtime.registry.register_agent(local)
    package = replace(
        spec(),
        agent_provider="local",
        permissions=[ToolPermission("read", approval=ApprovalRequirement("always"))],
    )
    scripted.queue(tool_call_turn(call_id="r", name="read", arguments={}))
    task = asyncio.create_task(run_workspace_agent(runtime, package, input="go"))
    for _ in range(100):
        if local._approvals:
            break
        await asyncio.sleep(0.001)
    assert local._approvals
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert active_permissions() == ()
    assert effects == []


async def test_hosted_configuration_respects_user_policy() -> None:
    runtime, scripted, _ = setup()

    class DenyHosted:
        async def check(self, request: PolicyRequest) -> PolicyDecision:
            if request.checkpoint == "before_hosted_tool_config":
                assert request.metadata["scopes"] == ["read"]
                assert request.metadata["tool_ref"] == "hosted:web_search"
                return PolicyDecision.deny("user veto")
            return PolicyDecision.allow()

    scripted.queue(text_only_turn("done"))
    package = replace(
        spec(), hosted_tools=[WebSearch()], permissions=[ToolPermission("hosted:web_search")]
    )
    result = await run_workspace_agent(runtime, package, input="go", policy=DenyHosted())
    assert scripted.calls[0].hosted_tools == []
    assert any(event.data.get("reason") == "user veto" for event in result.events)


async def test_mcp_fresh_scope_cannot_skip_new_approval() -> None:
    from blackbox.core.errors import MCPError
    from blackbox.mcp import MCPConnector, MCPServerSpec

    effects: list[str] = []
    connector = MCPConnector([MCPServerSpec(name="tickets", transport="stdio")])
    definition = connector.register_tool(
        "tickets", "lookup", lambda: effects.append("bad"), metadata={"read_only": True}
    )

    class ChangeDescriptor:
        async def check(self, request: PolicyRequest) -> PolicyDecision:
            definition.metadata["read_only"] = False
            return PolicyDecision.allow()

    connector.policy = ChangeDescriptor()
    permission = ToolPermission(
        "mcp:tickets.lookup", scopes=["read", "execute"], approval=ApprovalRequirement("on_execute")
    )
    with permission_boundary((compile_package_permissions([permission], []),)):
        with pytest.raises(MCPError):
            await connector.call_tool("tickets", "lookup")
    assert effects == []


async def test_mcp_approved_wrapper_keeps_dispatch_grant() -> None:
    from blackbox.mcp import MCPConnector, MCPServerSpec

    runtime, scripted, effects = setup()
    connector = MCPConnector([MCPServerSpec(name="tickets", transport="stdio")])
    connector.register_tool(
        "tickets", "lookup", lambda: effects.append("mcp") or "ok", metadata={"read_only": True}
    )
    await connector.register_runtime_tools(runtime.tools.registry)
    local = LocalAgentProvider(runtime.models, tools=ToolRuntime(runtime.tools.registry))
    runtime.registry.register_agent(local)
    package = replace(
        spec(),
        agent_provider="local",
        tools=["mcp:tickets.lookup"],
        permissions=[ToolPermission("mcp:tickets.lookup", approval=ApprovalRequirement("always"))],
    )
    scripted.queue(tool_call_turn(call_id="m", name="mcp:tickets.lookup", arguments={}))
    scripted.queue(text_only_turn("done"))
    task = asyncio.create_task(run_workspace_agent(runtime, package, input="go"))
    for _ in range(100):
        if local._approvals:
            break
        await asyncio.sleep(0.001)
    await local.approve(next(iter(local._approvals)), ApprovalDecision(approved=True))
    await task
    assert effects == ["mcp"]
    assert scripted.calls[1].input[0].status == "completed"
