from __future__ import annotations

import asyncio
import dataclasses
import json
import os
import shutil
import tempfile
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent_runtime.core.artifacts import Artifact, ArtifactPage
from agent_runtime.core.capabilities import (
    AgentCapabilities,
    CapabilityDetail,
    HostedToolSupport,
    ModelCapabilities,
    ModelCapabilityProfile,
)
from agent_runtime.core.events import AgentEvent, EventTypes
from agent_runtime.core.items import ItemTypes, RunItem
from agent_runtime.core.results import OutputSpec
from agent_runtime.core.sessions import AgentRef, AgentSession, InvocationRef, SessionRef
from agent_runtime.core.state import ProviderState, RunState
from agent_runtime.core.stores import (
    InMemoryEventStore,
    JSONLEventStore,
    SQLiteRunStore,
)
from agent_runtime.mcp import (
    MCPApprovalMode,
    MCPConnector,
    MCPServerSpec,
    MCPServerTrustPolicy,
    MCPTrustLevel,
)
from agent_runtime.observability.traces import trace_metadata_from_events
from agent_runtime.output.schema import build_output_schema
from agent_runtime.providers.base import (
    AgentSpec,
    TaskSpec,
    TurnRequest,
)
from agent_runtime.runtime.main import AgentRuntime
from agent_runtime.runtime.output import _validate_output
from agent_runtime.tools.catalog import ToolCatalog
from agent_runtime.tools.results import ToolResult
from agent_runtime.tools.toolsets import DynamicToolsetSession, ToolBudget
from agent_runtime.workspaces import CommandResult, CommandSpec, WorkspaceRuntime, WorkspaceSpec
from agent_runtime.workspaces.changes import FileChange, Patch
from benchmarks.harness import BenchmarkRecorder, MetricValue, time_async, time_sync

ScenarioAction = Callable[[BenchmarkRecorder], Awaitable[dict[str, MetricValue] | None]]
ScenarioEntry = tuple[str, ScenarioAction]
TurnScript = Callable[[TurnRequest], Iterable[AgentEvent]]


@dataclass(slots=True)
class Decision:
    action: str
    confidence: float
    tags: list[str]


@dataclass(slots=True)
class BenchmarkModelProvider:
    provider_id: str = "bench-model"
    scripts: list[TurnScript] = field(default_factory=list)
    calls: list[TurnRequest] = field(default_factory=list)

    def capabilities(self, model: str | None = None) -> ModelCapabilities:
        return ModelCapabilities(
            supports_streaming_events=True,
            supports_function_tools=True,
            supports_parallel_tool_calls=True,
            supports_hosted_tools=True,
            supports_tool_search=True,
            supports_client_executed_tool_search=True,
            supports_provider_state=True,
            hosted_tools={
                "web_search": HostedToolSupport("web_search", True, True, True, False),
            },
        )

    def capability_profile(self, model: str | None = None) -> ModelCapabilityProfile:
        summary = self.capabilities(model)
        return ModelCapabilityProfile(
            provider=self.provider_id,
            model=model,
            hosted_tools={
                "web_search": CapabilityDetail(status="supported"),
                "raw": CapabilityDetail(status="passthrough"),
            },
            output_strategies={
                "provider_native": CapabilityDetail(status="unsupported"),
                "finalizer_tool": CapabilityDetail(status="supported"),
                "posthoc_parse": CapabilityDetail(status="supported"),
                "posthoc_parse_with_retry": CapabilityDetail(status="supported"),
            },
            controls={
                "instructions": CapabilityDetail(status="conditional"),
                "temperature": CapabilityDetail(status="conditional"),
                "tool_choice": CapabilityDetail(status="conditional"),
                "parallel_tool_calls": CapabilityDetail(status="supported"),
                "extra": CapabilityDetail(status="passthrough"),
            },
            state_modes={
                "none": CapabilityDetail(status="supported"),
                "provider_stateful": CapabilityDetail(status="supported"),
                "stateless_replay": CapabilityDetail(status="supported"),
                "zdr": CapabilityDetail(status="unsupported"),
                "encrypted_reasoning": CapabilityDetail(status="unsupported"),
            },
            summary=summary,
        )

    def queue(self, script: TurnScript) -> None:
        self.scripts.append(script)

    async def stream_turn(self, request: TurnRequest) -> AsyncIterator[AgentEvent]:
        self.calls.append(request)
        if not self.scripts:
            raise AssertionError("BenchmarkModelProvider has no queued turn script")
        script = self.scripts.pop(0)
        for event in script(request):
            yield event


@dataclass(slots=True)
class BenchmarkAgentProvider:
    provider_id: str = "bench-cloud-agent"
    sessions: dict[str, AgentSession] = field(default_factory=dict)
    artifacts: list[Artifact] = field(default_factory=list)

    def capabilities(self) -> AgentCapabilities:
        return AgentCapabilities(
            supports_sessions=True,
            supports_streaming_events=True,
            supports_artifacts=True,
            supports_workspace=True,
            supports_resume=True,
        )

    async def create_agent(self, spec: AgentSpec) -> AgentRef:
        return AgentRef(provider=self.provider_id, id=spec.name or "bench-agent")

    async def start_session(self, agent: AgentRef | str, task: TaskSpec) -> AgentSession:
        agent_id = agent.id if isinstance(agent, AgentRef) else agent
        session = AgentSession(
            provider=self.provider_id,
            agent_id=agent_id,
            task=task.prompt,
            model=task.model,
            status="running",
        )
        self.sessions[session.id] = session
        return session

    async def stream_events(
        self,
        session: SessionRef | AgentSession,
        *,
        after_event_id: str | None = None,
    ) -> AsyncIterator[AgentEvent]:
        session_obj = self.sessions[session.id]
        artifact = Artifact(
            type="session_log",
            name="bench-cloud-session.json",
            data={"session_id": session.id, "status": "completed"},
            metadata={"session_id": session.id},
        )
        self.artifacts.append(artifact)
        events = [
            AgentEvent(
                type=EventTypes.SESSION_STARTED,
                provider=self.provider_id,
                session_id=session.id,
                data={"status": "running"},
            ),
            AgentEvent(
                type=EventTypes.CLOUD_AGENT_STATUS_CHANGED,
                provider=self.provider_id,
                session_id=session.id,
                data={"status": "collecting"},
            ),
            AgentEvent(
                type=EventTypes.CLOUD_AGENT_LOG,
                provider=self.provider_id,
                session_id=session.id,
                data={"message": "offline cloud-agent stream benchmark"},
            ),
            AgentEvent(
                type=EventTypes.MODEL_TEXT_DELTA,
                provider=self.provider_id,
                session_id=session.id,
                data={"delta": "Collected cloud-agent session output."},
            ),
            AgentEvent(
                type=EventTypes.ARTIFACT_CREATED,
                provider=self.provider_id,
                session_id=session.id,
                data={"artifact": artifact},
            ),
            AgentEvent(
                type=EventTypes.SESSION_COMPLETED,
                provider=self.provider_id,
                session_id=session.id,
                data={
                    "output": "Collected cloud-agent session output.",
                    "provider_state": ProviderState(
                        provider=self.provider_id,
                        conversation_id=session.id,
                        continuation={"last_event_id": "session.completed"},
                    ),
                },
            ),
        ]
        if after_event_id is not None:
            try:
                start = next(index for index, event in enumerate(events) if event.id == after_event_id) + 1
            except StopIteration:
                start = 0
        else:
            start = 0
        for event in events[start:]:
            yield event
        session_obj.status = "completed"

    async def send_message(
        self,
        session: SessionRef | AgentSession,
        message: str,
    ) -> InvocationRef:
        return InvocationRef(
            provider=self.provider_id,
            session_id=session.id,
            id=f"inv_{session.id}",
            metadata={"message": message},
        )

    async def approve(self, approval_id: str, decision: Any) -> None:
        return None

    async def cancel(self, session: SessionRef | AgentSession) -> None:
        self.sessions[session.id].status = "cancelled"

    async def list_artifacts(
        self,
        session: SessionRef | AgentSession,
        *,
        type: str | None = None,
        after: str | None = None,
        limit: int = 100,
    ) -> ArtifactPage:
        items = [
            artifact
            for artifact in self.artifacts
            if artifact.metadata.get("session_id") == session.id
            and (type is None or artifact.type == type)
        ]
        return ArtifactPage(items=items[:limit], has_more=len(items) > limit)


class FakeMCPTransport:
    def __init__(self) -> None:
        self.started = False
        self.stopped = False
        self.requests: list[tuple[str, dict[str, Any] | None]] = []

    @property
    def session_id(self) -> str:
        return "bench-session"

    @property
    def connected(self) -> bool:
        return self.started and not self.stopped

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True

    async def notify(
        self,
        method: str,
        params: dict[str, Any] | None = None,
    ) -> None:
        self.requests.append((method, params))

    async def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout_seconds: float | None = None,
    ) -> Any:
        self.requests.append((method, params))
        if method == "initialize":
            return {
                "protocolVersion": "2025-06-18",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "bench-mcp"},
            }
        if method == "tools/list":
            return {
                "tools": [
                    {
                        "name": "lookup",
                        "description": "Lookup a benchmark ticket.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {"ticket_id": {"type": "string"}},
                            "required": ["ticket_id"],
                        },
                    }
                ]
            }
        if method == "tools/call":
            arguments = dict(params.get("arguments", {})) if params is not None else {}
            return {
                "content": [{"type": "text", "text": json.dumps(arguments)}],
                "structuredContent": {"ok": True, "arguments": arguments},
            }
        raise AssertionError(method)


def offline_scenarios() -> list[ScenarioEntry]:
    return [
        ("text_only_run", text_only_run),
        ("structured_output_run", structured_output_run),
        ("posthoc_parse_with_retry_run", posthoc_parse_with_retry_run),
        ("one_local_tool_call", one_local_tool_call),
        ("five_parallel_local_tool_calls", five_parallel_local_tool_calls),
        ("dynamic_toolset_search_load", dynamic_toolset_search_load),
        ("mcp_tool_discovery_call", mcp_tool_discovery_call),
        ("workspace_read_write_patch_snapshot", workspace_read_write_patch_snapshot),
        ("cloud_agent_session_stream_collection", cloud_agent_session_stream_collection),
        ("persisted_resume_jsonl_sqlite", persisted_resume_jsonl_sqlite),
    ]


def credentialed_network_scenarios() -> tuple[list[ScenarioEntry], list[str]]:
    """Return opt-in network scenarios that have credentials available."""

    skipped: list[str] = []
    scenarios: list[ScenarioEntry] = []
    if not os.environ.get("OPENAI_API_KEY"):
        skipped.append("openai_text_only_network: missing OPENAI_API_KEY")
    else:
        scenarios.append(("openai_text_only_network", openai_text_only_network))
    return scenarios, skipped


async def openai_text_only_network(recorder: BenchmarkRecorder) -> dict[str, MetricValue]:
    """Minimal credential-gated provider run for manual network baselines."""

    try:
        from agent_runtime.providers.model_adapters.openai_responses import (
            OpenAIResponsesProvider,
        )
    except ImportError as exc:
        raise RuntimeError("openai extra is not installed") from exc

    api_key = os.environ["OPENAI_API_KEY"]
    model = os.environ.get("AGENT_RUNTIME_PERF_OPENAI_MODEL", "gpt-4o-mini")
    runtime = AgentRuntime()
    runtime.registry.register_model(OpenAIResponsesProvider(api_key=api_key))

    events: list[AgentEvent] = []
    text_parts: list[str] = []
    async for event in runtime.models.stream(
        provider="openai",
        model=model,
        input="Reply with exactly: pong",
    ):
        recorder.observe_event()
        events.append(event)
        delta = event.data.get("delta")
        if isinstance(delta, str):
            text_parts.append(delta)
    recorder.mark_final_output()
    metadata = {"provider": "openai", "model": model, "text": "".join(text_parts)}
    await _record_runtime_result_metrics(recorder, events, metadata)
    return {"network_event_count": len(events)}


async def text_only_run(recorder: BenchmarkRecorder) -> dict[str, MetricValue]:
    runtime, provider = _runtime_with_model()
    provider.queue(_text_turn("Offline benchmark text.", recorder=recorder))

    result: Any = await runtime.run(provider="bench-model:test", input="Say hello.")
    recorder.mark_final_output()

    await _record_runtime_result_metrics(recorder, result.events, result.metadata)
    return {}


async def structured_output_run(recorder: BenchmarkRecorder) -> dict[str, MetricValue]:
    runtime, provider = _runtime_with_model()
    text = json.dumps({"action": "ship", "confidence": 0.98, "tags": ["perf", "offline"]})
    provider.queue(_text_turn(text, recorder=recorder))

    result: Any = await runtime.run(
        provider="bench-model:test",
        input="Return a decision.",
        output_type=Decision,
    )
    recorder.mark_final_output()
    _, validation_ms = time_sync(lambda: _validate_output(result.text, Decision))

    await _record_runtime_result_metrics(recorder, result.events, result.metadata)
    return {
        "output_validation_ms": validation_ms,
        "validated_tags": len(result.output.tags),
    }


async def posthoc_parse_with_retry_run(recorder: BenchmarkRecorder) -> dict[str, MetricValue]:
    runtime, provider = _runtime_with_model()
    provider.queue(_text_turn("not-json", recorder=recorder))
    valid_text = json.dumps({"action": "retry", "confidence": 0.88, "tags": ["repair"]})
    provider.queue(_text_turn(valid_text))

    result: Any = await runtime.run(
        provider="bench-model:test",
        input="Return a decision with retry.",
        output_spec=OutputSpec(
            schema=Decision,
            strategy="posthoc_parse_with_retry",
            max_validation_retries=1,
        ),
    )
    recorder.mark_final_output()
    _, validation_ms = time_sync(lambda: _validate_output(result.text, Decision))

    await _record_runtime_result_metrics(recorder, result.events, result.metadata)
    return {
        "output_validation_ms": validation_ms,
        "validation_attempts": result.metadata.get("validation_attempts"),
    }


async def one_local_tool_call(recorder: BenchmarkRecorder) -> dict[str, MetricValue]:
    runtime, provider = _runtime_with_model()
    active = 0
    max_active = 0

    async def lookup(ticket_id: str) -> ToolResult:
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0)
        active -= 1
        return ToolResult(content=f"ticket:{ticket_id}", payload={"ticket_id": ticket_id})

    runtime.tools.register(
        lookup,
        name="lookup_ticket",
        description="Lookup a benchmark ticket.",
        parameters={
            "type": "object",
            "properties": {"ticket_id": {"type": "string"}},
            "required": ["ticket_id"],
        },
    )
    provider.queue(
        _tool_turn(
            [("call_1", "lookup_ticket", {"ticket_id": "T-1"})],
            recorder=recorder,
        )
    )
    provider.queue(_text_turn("ticket:T-1"))

    result: Any = await runtime.run(
        provider="bench-model:test",
        input="Lookup ticket T-1.",
        tools=["lookup_ticket"],
    )
    recorder.mark_final_output()

    await _record_runtime_result_metrics(recorder, result.events, result.metadata)
    return {"tool_dispatch_max_concurrency": max_active}


async def five_parallel_local_tool_calls(recorder: BenchmarkRecorder) -> dict[str, MetricValue]:
    runtime, provider = _runtime_with_model()
    active = 0
    max_active = 0

    def make_tool(index: int) -> Callable[[], Awaitable[ToolResult]]:
        async def tool() -> ToolResult:
            nonlocal active, max_active
            active += 1
            max_active = max(max_active, active)
            await asyncio.sleep(0.01)
            active -= 1
            return ToolResult(content=f"part-{index}", payload={"index": index})

        return tool

    calls: list[tuple[str, str, dict[str, Any]]] = []
    tool_names: list[str] = []
    for index in range(5):
        name = f"parallel_{index}"
        tool_names.append(name)
        calls.append((f"call_{index}", name, {}))
        runtime.tools.register(
            make_tool(index),
            name=name,
            description=f"Parallel benchmark tool {index}.",
            parameters={"type": "object", "properties": {}, "required": []},
        )
    provider.queue(_tool_turn(calls, recorder=recorder))
    provider.queue(_text_turn("parallel tools complete"))

    result: Any = await runtime.run(
        provider="bench-model:test",
        input="Call all parallel tools.",
        tools=tool_names,
    )
    recorder.mark_final_output()

    await _record_runtime_result_metrics(recorder, result.events, result.metadata)
    return {"tool_dispatch_max_concurrency": max_active}


async def dynamic_toolset_search_load(recorder: BenchmarkRecorder) -> dict[str, MetricValue]:
    runtime = AgentRuntime()
    for index in range(64):
        runtime.tools.register(
            lambda index=index: ToolResult(content=f"tool:{index}"),
            name=f"lookup_{index}",
            description=f"Lookup benchmark record {index}.",
            parameters={"type": "object", "properties": {}, "required": []},
            category="lookup" if index % 2 == 0 else "utility",
            tags=["benchmark", f"group-{index % 4}"],
            scopes=["records:read"],
            risk="low",
        )
    session = DynamicToolsetSession(
        registry=runtime.tools.registry,
        catalog=ToolCatalog(runtime.tools.all_tools()),
        budget=ToolBudget(max_tools_visible=12, search_result_limit=8),
    )
    session.install_meta_tools()
    recorder.observe_event()

    search_result, search_ms = time_sync(lambda: session.search_tools(query="lookup 2"))
    load_result, load_ms = time_sync(
        lambda: session.load_tools(["lookup_2", "lookup_20", "lookup_22"])
    )
    recorder.mark_final_output()

    events = [
        *session.initial_events(),
        *search_result.events,
        *load_result.events,
    ]
    recorder.record("event_count", len(events))
    recorder.record("tool_search_result_count", len(search_result.payload["results"]))
    recorder.record("tool_load_count", len(load_result.payload["loaded"]))
    return {
        "tool_search_ms": search_ms,
        "tool_load_ms": load_ms,
    }


async def mcp_tool_discovery_call(recorder: BenchmarkRecorder) -> dict[str, MetricValue]:
    shared_cache: dict[str, Any] = {}
    transport = FakeMCPTransport()
    connector = MCPConnector(
        [_trusted_mcp_spec()],
        transports={"tickets": transport},
        _tool_cache=shared_cache,
    )
    recorder.observe_event()
    tools, discovery_ms = await time_async(lambda: connector.list_tools("tickets"))
    _, cache_hit_ms = await time_async(lambda: connector.list_tools("tickets"))
    call_result, call_ms = await time_async(
        lambda: connector.call_tool_result("tickets", "lookup", {"ticket_id": "T-1"})
    )
    second_transport = FakeMCPTransport()
    second_connector = MCPConnector(
        [_trusted_mcp_spec()],
        transports={"tickets": second_transport},
        _tool_cache=shared_cache,
    )
    _, shared_cache_hit_ms = await time_async(
        lambda: second_connector.list_tools("tickets")
    )
    recorder.mark_final_output()
    events = [*connector.drain_events(), *second_connector.drain_events()]
    await connector.stop()
    await second_connector.stop()

    recorder.record("event_count", len(events))
    return {
        "mcp_discovery_ms": discovery_ms,
        "mcp_cache_hit_ms": cache_hit_ms,
        "mcp_shared_cache_hit_ms": shared_cache_hit_ms,
        "mcp_call_ms": call_ms,
        "mcp_tool_count": len(tools),
        "mcp_request_count": len(transport.requests),
        "mcp_shared_cache_request_count": len(second_transport.requests),
        "mcp_output_bytes": len(call_result.rendered_content().encode("utf-8")),
    }


async def workspace_read_write_patch_snapshot(
    recorder: BenchmarkRecorder,
) -> dict[str, MetricValue]:
    with tempfile.TemporaryDirectory(prefix="agent-runtime-bench-ws-") as root:
        root_path = Path(root)
        workspace_runtime = WorkspaceRuntime(executor=_fake_command_executor)
        workspace = await workspace_runtime.open(WorkspaceSpec.local(root_path))
        recorder.observe_event()

        await workspace_runtime.write_file(workspace, "notes.txt", "ship it")
        read_content = await workspace_runtime.read_file(workspace, "notes.txt")
        patch = Patch(
            summary="add benchmark file",
            diff="--- /dev/null\n+++ b/bench.txt\n@@\n+bench\n",
            changes=[FileChange(path="bench.txt", type="create", content="bench")],
        )
        patch_artifact = await workspace_runtime.apply_patch(workspace, patch)
        command_result = await workspace_runtime.run_command(
            workspace,
            CommandSpec(command="echo benchmark", metadata={"artifact": True}),
        )
        snapshot = await workspace_runtime.snapshot(workspace, name="bench-snapshot")
        recorder.mark_final_output()

        _, artifact_serialization_ms = time_sync(
            lambda: json.dumps(
                {
                    "patch": dataclasses.asdict(patch_artifact.to_artifact()),
                    "command": dataclasses.asdict(command_result),
                    "snapshot": dataclasses.asdict(snapshot),
                },
                default=str,
                sort_keys=True,
            )
        )
        events = workspace_runtime.drain_events()
        await workspace_runtime.close(workspace)
        _cleanup_file_uri(snapshot.uri)

    recorder.record("event_count", len(events))
    return {
        "artifact_serialization_ms": artifact_serialization_ms,
        "workspace_event_count": len(events),
        "workspace_read_bytes": len(read_content.encode("utf-8")),
    }


async def cloud_agent_session_stream_collection(
    recorder: BenchmarkRecorder,
) -> dict[str, MetricValue]:
    runtime = AgentRuntime()
    provider = BenchmarkAgentProvider()
    runtime.registry.register_agent(provider)
    recorder.observe_event()
    result: Any = await runtime.agents.run(
        provider="bench-cloud-agent",
        agent="default",
        task="Collect session output.",
        model="bench-agent",
    )
    recorder.mark_final_output()

    _, trace_export_ms = time_sync(
        lambda: trace_metadata_from_events(result.events, metadata=result.metadata)
    )
    recorder.record("event_count", len(result.events))
    return {
        "artifact_serialization_ms": time_sync(
            lambda: json.dumps(result.summary(), default=str, sort_keys=True)
        )[1],
        "trace_export_ms": trace_export_ms,
        "cloud_agent_artifact_count": len(result.artifacts),
    }


async def persisted_resume_jsonl_sqlite(recorder: BenchmarkRecorder) -> dict[str, MetricValue]:
    with tempfile.TemporaryDirectory(prefix="agent-runtime-bench-persist-") as root:
        root_path = Path(root)
        event_store = JSONLEventStore(root_path / "events.jsonl")
        run_store = SQLiteRunStore(root_path / "runs.sqlite")
        runtime, provider = _runtime_with_model(event_store=event_store, run_store=run_store)
        provider.queue(_text_turn("first persisted turn", recorder=recorder))

        result: Any = await runtime.run(provider="bench-model:test", input="First turn.")
        recorder.mark_final_output()

        _, event_store_write_ms = await time_async(
            lambda: _append_events(event_store, result.events)
        )
        state = RunState(
            session_id="bench-resume",
            provider="bench-model",
            model="test",
            provider_state=result.provider_state,
            metadata={"source_run_id": result.events[0].run_id if result.events else None},
        )
        await run_store.save(state)

        _, jsonl_resume_ms = await time_async(
            lambda: event_store.list_events(str(result.events[0].run_id))
        )
        loaded_state, sqlite_resume_ms = await time_async(
            lambda: run_store.load("bench-resume")
        )
        provider.queue(_text_turn("resumed persisted turn"))
        resumed: Any = await runtime.run(
            provider="bench-model:test",
            input="Resume turn.",
            provider_state=loaded_state.provider_state if loaded_state is not None else None,
        )
        await run_store.close()

    await _record_runtime_result_metrics(recorder, result.events, result.metadata)
    return {
        "event_store_write_ms": event_store_write_ms,
        "jsonl_resume_ms": jsonl_resume_ms,
        "sqlite_resume_ms": sqlite_resume_ms,
        "resumed_event_count": len(resumed.events),
    }


def _runtime_with_model(
    *,
    event_store: Any | None = None,
    run_store: Any | None = None,
) -> tuple[AgentRuntime, BenchmarkModelProvider]:
    runtime = AgentRuntime(event_store=event_store, run_store=run_store)
    provider = BenchmarkModelProvider()
    runtime.registry.register_model(provider)
    return runtime, provider


def _text_turn(
    text: str,
    *,
    recorder: BenchmarkRecorder | None = None,
) -> TurnScript:
    def script(request: TurnRequest) -> Iterable[AgentEvent]:
        if recorder is not None:
            recorder.observe_event()
        yield AgentEvent(
            type=EventTypes.MODEL_REQUEST_STARTED,
            provider="bench-model",
            data={"model": request.model},
        )
        item = RunItem(
            type=ItemTypes.MESSAGE,
            provider="bench-model",
            status="completed",
            data={"role": "assistant", "text": text},
        )
        yield AgentEvent(
            type=EventTypes.MODEL_ITEM_CREATED,
            provider="bench-model",
            item_id=item.id,
            data={"item": item},
        )
        yield AgentEvent(
            type=EventTypes.MODEL_TEXT_DELTA,
            provider="bench-model",
            item_id=item.id,
            data={"delta": text},
        )
        if recorder is not None:
            recorder.mark_final_output()
        yield AgentEvent(
            type=EventTypes.MODEL_COMPLETED,
            provider="bench-model",
            item_id=item.id,
            data={
                "model": request.model,
                "provider_state": ProviderState(
                    provider="bench-model",
                    previous_response_id=item.id,
                    continuation={"turn": len(text)},
                ),
            },
        )

    return script


def _tool_turn(
    calls: list[tuple[str, str, dict[str, Any]]],
    *,
    recorder: BenchmarkRecorder | None = None,
) -> TurnScript:
    def script(request: TurnRequest) -> Iterable[AgentEvent]:
        if recorder is not None:
            recorder.observe_event()
        yield AgentEvent(
            type=EventTypes.MODEL_REQUEST_STARTED,
            provider="bench-model",
            data={"model": request.model},
        )
        for call_id, name, arguments in calls:
            yield AgentEvent(
                type=EventTypes.TOOL_CALL_REQUESTED,
                provider="bench-model",
                item_id=call_id,
                data={"call_id": call_id, "name": name, "arguments": arguments},
            )
        yield AgentEvent(
            type=EventTypes.MODEL_COMPLETED,
            provider="bench-model",
            data={
                "provider_state": ProviderState(
                    provider="bench-model",
                    previous_response_id=calls[-1][0] if calls else None,
                )
            },
        )

    return script


async def _record_runtime_result_metrics(
    recorder: BenchmarkRecorder,
    events: list[AgentEvent],
    metadata: dict[str, Any],
) -> None:
    recorder.record("event_count", len(events))
    _, event_store_write_ms = await time_async(
        lambda: _append_events(InMemoryEventStore(), events)
    )
    _, trace_export_ms = time_sync(
        lambda: trace_metadata_from_events(events, metadata=metadata)
    )
    recorder.record("event_store_write_ms", event_store_write_ms)
    recorder.record("trace_export_ms", trace_export_ms)


async def _append_events(store: InMemoryEventStore | JSONLEventStore, events: list[AgentEvent]) -> None:
    await store.append_many(events)


def _trusted_mcp_spec() -> MCPServerSpec:
    return MCPServerSpec(
        name="tickets",
        transport="stdio",
        command="bench-mcp",
        tool_cache_ttl_seconds=60.0,
        trust_policy=MCPServerTrustPolicy(
            server="tickets",
            trust_level=MCPTrustLevel.TRUSTED,
            allowed_tools=frozenset({"lookup"}),
            approval_mode=MCPApprovalMode.NEVER,
        ),
    )


async def _fake_command_executor(spec: CommandSpec, ws: Any) -> CommandResult:
    return CommandResult(
        exit_code=0,
        stdout=f"ran:{spec.display}",
        duration_seconds=0.001,
    )


def _cleanup_file_uri(uri: str | None) -> None:
    if uri is None or not uri.startswith("file://"):
        return
    path = Path(uri.removeprefix("file://"))
    try:
        if path.exists():
            path.unlink()
        parent = path.parent
        if parent.exists():
            shutil.rmtree(parent, ignore_errors=True)
    except OSError:
        return


def output_schema_generation() -> dict[str, Any]:
    """Expose schema generation work as a small reusable probe for tests/docs."""

    schema, elapsed_ms = time_sync(lambda: build_output_schema(OutputSpec(schema=Decision)))
    return {"schema": schema.to_dict(), "elapsed_ms": elapsed_ms}
