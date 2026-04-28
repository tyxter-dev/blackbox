"""Live, report-oriented journeys for the WorkspaceProvider framework.

These tests are intentionally not assertion-driven. Model tool choices,
provider events, and generated text are nondeterministic, so each journey
prints a compact JSON report that can be evaluated by an LLM or a human
reviewer.
"""
from __future__ import annotations

import asyncio
import json
import os
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from agent_runtime import (
    AgentRuntime,
    ApprovalDecision,
    EventTypes,
    ModelPricing,
)
from agent_runtime.core.artifacts import Artifact, ArtifactPage
from agent_runtime.core.errors import WorkspaceError
from agent_runtime.core.events import AgentEvent
from agent_runtime.core.policy import PolicyDecision, PolicyRequest
from agent_runtime.models.anthropic_messages import AnthropicMessagesProvider
from agent_runtime.models.gemini_generate_content import GeminiGenerateContentProvider
from agent_runtime.models.openai_responses import OpenAIResponsesProvider
from agent_runtime.models.xai_responses import XAIResponsesProvider
from agent_runtime.workspaces import (
    CommandResult,
    CommandSpec,
    FakeSandboxClient,
    FileChange,
    LocalWorkspaceProvider,
    Patch,
    SandboxCommandEvent,
    SandboxSession,
    SandboxWorkspaceProvider,
    WorkspaceApprovalRequired,
    WorkspaceProviderCapabilities,
    WorkspaceRef,
    WorkspaceSessionState,
    WorkspaceSpec,
)

pytestmark = pytest.mark.journey_workspace_provider


@dataclass(frozen=True, slots=True)
class LiveModelProvider:
    key: str
    model: str
    register: Callable[[AgentRuntime], None]
    supports_function_tools: bool = True

    @property
    def ref(self) -> str:
        return f"{self.key}:{self.model}"


class WorkspaceWriteApprovalPolicy:
    async def check(self, request: PolicyRequest) -> PolicyDecision:
        if request.checkpoint == "before_workspace_write":
            return PolicyDecision.require_approval("WorkspaceProvider journey write review.")
        return PolicyDecision.allow()


class JourneySandboxClient(FakeSandboxClient):
    async def run_command(self, session: SandboxSession, spec: CommandSpec) -> CommandResult:
        command_id = f"journey_{len(self._sessions)}_{abs(hash(spec.display)) % 100000}"
        return CommandResult(
            exit_code=0,
            stdout=(
                "sandbox command completed\n"
                f"command={spec.display}\n"
                f"cwd={spec.cwd or '.'}\n"
            ),
            duration_seconds=0.01,
            command_id=command_id,
            metadata={"journey_safe_command": True},
        )

    async def stream_command(
        self,
        session: SandboxSession,
        spec: CommandSpec,
    ) -> Any:
        result = await self.run_command(session, spec)
        yield SandboxCommandEvent("stdout", result.stdout, command_id=result.command_id)


async def test_journey_direct_local_workspace_operations(tmp_path: Path) -> None:
    _seed_demo_workspace(tmp_path)
    provider = LocalWorkspaceProvider()
    events: list[AgentEvent] = []
    changes: list[Any] = []

    ws = await provider.open(WorkspaceSpec.local(tmp_path))
    _collect_provider_events(provider, events)
    files_before = await provider.list_files(ws, recursive=True)
    _collect_provider_events(provider, events)
    readme = await provider.read_file(ws, "README.md")
    _collect_provider_events(provider, events)
    changes.append(
        await provider.write_file(
            ws,
            "notes/agent_summary.md",
            "# Agent Summary\n\nWorkspaceProvider can write files.\n",
        )
    )
    _collect_provider_events(provider, events)
    changes.append(
        await provider.apply_patch(
            ws,
            Patch(
                summary="Refresh app status",
                diff="*** Begin Patch\n*** Update File: src/app.py\n@@\n-status = 'todo'\n+status = 'ready'\n*** End Patch",
                changes=[FileChange(path="src/app.py", type="update", content="status = 'ready'\n")],
            ),
        )
    )
    _collect_provider_events(provider, events)
    changes.append(await provider.delete_file(ws, "notes/obsolete.txt"))
    _collect_provider_events(provider, events)
    command = await provider.run_command(
        ws,
        CommandSpec(
            command=(
                "python -c \"from pathlib import Path; "
                "print(Path('notes/agent_summary.md').read_text().splitlines()[0])\""
            )
        ),
    )
    _collect_provider_events(provider, events)
    snapshot = await provider.snapshot(ws, name="direct-local-after")
    _collect_provider_events(provider, events)
    artifacts = await provider.list_artifacts(ws)
    exported = await provider.export_artifact(ws, snapshot.ref)
    _collect_provider_events(provider, events)
    files_after = await provider.list_files(ws, recursive=True)
    _collect_provider_events(provider, events)
    await provider.close(ws)
    _collect_provider_events(provider, events)

    _print_report(
        journey="direct_local_workspace_operations",
        goal=(
            "A user opens a local workspace directly, lists and reads files, writes and "
            "deletes files, applies a patch, runs a command, snapshots the result, "
            "exports artifacts, and closes the workspace."
        ),
        workspace_provider=provider.provider_id,
        prompt="Direct WorkspaceProvider API calls, no model prompt.",
        events=events,
        workspace=ws,
        extra={
            "files_before": files_before,
            "files_after": files_after,
            "readme_excerpt": _clip(readme, limit=600),
            "changes": [_change_summary(change) for change in changes],
            "command": _command_summary(command),
            "snapshot": _artifact_summary(snapshot),
            "exported": _artifact_summary(exported),
            "artifacts": _artifact_page_summary(artifacts),
        },
    )


async def test_journey_local_workspace_state_attach_and_restore(tmp_path: Path) -> None:
    _seed_demo_workspace(tmp_path)
    provider = LocalWorkspaceProvider()
    events: list[AgentEvent] = []

    ws = await provider.open(WorkspaceSpec.local(tmp_path))
    _collect_provider_events(provider, events)
    await provider.write_file(ws, "state/persisted.txt", "state survives attach and restore\n")
    _collect_provider_events(provider, events)
    state = provider.session_state(ws)
    attached_provider = LocalWorkspaceProvider()
    attached = await attached_provider.attach(state)
    _collect_provider_events(attached_provider, events)
    attached_content = await attached_provider.read_file(attached, "state/persisted.txt")
    _collect_provider_events(attached_provider, events)
    snapshot = await attached_provider.snapshot(attached, name="state-attach-restore")
    _collect_provider_events(attached_provider, events)
    restore_root = tmp_path.parent / f"{tmp_path.name}-restored"
    restored = await attached_provider.restore(
        snapshot.ref,
        spec=WorkspaceSpec.local(restore_root),
    )
    _collect_provider_events(attached_provider, events)
    restored_files = await attached_provider.list_files(restored, recursive=True)
    _collect_provider_events(attached_provider, events)
    await provider.close(ws)
    await attached_provider.close(attached)
    await attached_provider.close(restored, delete=True)
    _collect_provider_events(provider, events)
    _collect_provider_events(attached_provider, events)

    _print_report(
        journey="local_workspace_state_attach_and_restore",
        goal=(
            "A user serializes local workspace state, attaches from it in a later process, "
            "snapshots the workspace, and restores into a new local root."
        ),
        workspace_provider=provider.provider_id,
        prompt="Direct WorkspaceProvider state and restore flow, no model prompt.",
        events=events,
        workspace=ws,
        session_state=state,
        extra={
            "attached_workspace": _workspace_summary(attached),
            "attached_content": _clip(attached_content, limit=600),
            "snapshot": _artifact_summary(snapshot),
            "restored_workspace": _workspace_summary(restored),
            "restored_files": restored_files,
        },
    )


async def test_journey_direct_policy_gated_workspace_write(tmp_path: Path) -> None:
    _seed_demo_workspace(tmp_path)
    provider = LocalWorkspaceProvider(policy=WorkspaceWriteApprovalPolicy())
    events: list[AgentEvent] = []
    approval: dict[str, Any] = {"observed": False}

    ws = await provider.open(WorkspaceSpec.local(tmp_path))
    _collect_provider_events(provider, events)
    try:
        change = await provider.write_file(ws, "approvals/reviewed.md", "pending review\n")
    except WorkspaceApprovalRequired as required:
        approval = {
            "observed": True,
            "approval_id": required.pending.id,
            "operation": required.pending.operation,
            "arguments": required.pending.arguments,
            "reason": required.pending.reason,
        }
        await required.approve(ApprovalDecision.approve("Approved by journey reviewer."))
        change = await provider.write_file(ws, "approvals/reviewed.md", "approved write\n")
    _collect_provider_events(provider, events)
    final_content = await provider.read_file(ws, "approvals/reviewed.md")
    _collect_provider_events(provider, events)
    await provider.close(ws)
    _collect_provider_events(provider, events)

    _print_report(
        journey="direct_policy_gated_workspace_write",
        goal=(
            "A user protects workspace writes with policy, observes an approval-required "
            "operation, approves it, and retries the write successfully."
        ),
        workspace_provider=provider.provider_id,
        prompt="Direct WorkspaceProvider approval flow, no model prompt.",
        events=events,
        workspace=ws,
        extra={
            "approval": approval,
            "change": _change_summary(change),
            "final_content": _clip(final_content, limit=600),
        },
    )


async def test_journey_runtime_local_workspace_tool_loop(tmp_path: Path) -> None:
    _seed_demo_workspace(tmp_path)
    for model_provider in _available_model_providers(function_tools=True):
        runtime = _runtime_for(model_provider)
        prompt = (
            "You are helping build a coding-agent app. Use workspace tools before your "
            "final answer. List files recursively, read README.md and src/app.py, write "
            "notes/runtime_summary.md with your findings, run a Python command that checks "
            "that file exists, create a workspace snapshot named runtime-local-after, then "
            "summarize what changed."
        )
        try:
            result = await runtime.run(
                provider=model_provider.key,
                model=model_provider.model,
                input=prompt,
                workspace=WorkspaceSpec.local(tmp_path),
                max_iterations=10,
                max_output_tokens=_output_budget(model_provider, 1200),
            )
            _print_report(
                journey="runtime_local_workspace_tool_loop",
                goal=(
                    "A user passes a local workspace to runtime.run, lets a real model "
                    "inspect/edit/run/snapshot through automatically registered workspace "
                    "tools, and receives a final answer plus events."
                ),
                workspace_provider="local-workspace",
                model_provider=model_provider,
                prompt=prompt,
                response_text=result.text,
                events=result.events,
                metadata=result.metadata,
                artifacts=result.artifacts,
                extra={
                    "payloads": _payload_summary(result.payloads),
                    "workspace_files": _safe_files(tmp_path),
                },
            )
        finally:
            await runtime.close()


async def test_journey_runtime_workspace_write_approval_resume(tmp_path: Path) -> None:
    _seed_demo_workspace(tmp_path)
    for model_provider in _available_model_providers(function_tools=True):
        runtime = _runtime_for(model_provider)
        prompt = (
            "Use workspace_write_file to create approvals/model_review.md with a short "
            "release-review note. If approval is required, wait for it and then continue. "
            "After the write, read the file and give a final two sentence summary."
        )
        try:
            events, approval_metadata = await _collect_runtime_stream_with_approval(
                runtime,
                provider=model_provider.key,
                model=model_provider.model,
                input=prompt,
                workspace=WorkspaceSpec.local(tmp_path),
                workspace_policy=WorkspaceWriteApprovalPolicy(),
                max_iterations=8,
                max_output_tokens=_output_budget(model_provider, 1000),
            )
            _print_report(
                journey="runtime_workspace_write_approval_resume",
                goal=(
                    "A user runs a real model with workspace write policy, approves the "
                    "pending workspace operation, and lets the model continue."
                ),
                workspace_provider="local-workspace",
                model_provider=model_provider,
                prompt=prompt,
                response_text=_collected_text(events),
                events=events,
                extra={
                    **approval_metadata,
                    "workspace_files": _safe_files(tmp_path),
                    "model_review": _safe_read(tmp_path / "approvals" / "model_review.md"),
                },
            )
        finally:
            await runtime.close()


async def test_journey_runtime_sandbox_workspace_tool_loop(tmp_path: Path) -> None:
    source = tmp_path / "sandbox-source"
    _seed_demo_workspace(source)
    for model_provider in _available_model_providers(function_tools=True):
        runtime = _runtime_for(model_provider)
        workspace_provider = SandboxWorkspaceProvider(JourneySandboxClient())
        prompt = (
            "You are helping evaluate a sandbox workspace backend. Use workspace tools "
            "before your final answer. List files under repo recursively, read "
            "repo/README.md, write repo/sandbox_summary.md, run a command in cwd repo, "
            "create a workspace snapshot named runtime-sandbox-after, then summarize the "
            "sandbox result."
        )
        try:
            result = await runtime.run(
                provider=model_provider.key,
                model=model_provider.model,
                input=prompt,
                workspace=WorkspaceSpec.sandbox(inputs={"repo": str(source)}),
                workspace_provider=workspace_provider,
                max_iterations=10,
                max_output_tokens=_output_budget(model_provider, 1200),
            )
            _print_report(
                journey="runtime_sandbox_workspace_tool_loop",
                goal=(
                    "A user passes a sandbox workspace provider to runtime.run, lets a "
                    "real model inspect/edit/run/snapshot through the sandbox-backed tool "
                    "contract, and receives a final answer."
                ),
                workspace_provider=workspace_provider.provider_id,
                model_provider=model_provider,
                prompt=prompt,
                response_text=result.text,
                events=result.events,
                metadata=result.metadata,
                artifacts=result.artifacts,
                extra={"payloads": _payload_summary(result.payloads)},
            )
        finally:
            await runtime.close()


async def test_journey_direct_sandbox_workspace_ports_and_state(tmp_path: Path) -> None:
    source = tmp_path / "sandbox-source"
    _seed_demo_workspace(source)
    provider = SandboxWorkspaceProvider(JourneySandboxClient())
    events: list[AgentEvent] = []

    ws = await provider.open(WorkspaceSpec.sandbox(inputs={"repo": str(source)}))
    _collect_provider_events(provider, events)
    files = await provider.list_files(ws, path="repo", recursive=True)
    _collect_provider_events(provider, events)
    await provider.write_file(ws, "repo/preview.md", "sandbox preview ready\n")
    _collect_provider_events(provider, events)
    command = await provider.run_command(ws, CommandSpec(command="python -m pytest -q", cwd="repo"))
    _collect_provider_events(provider, events)
    port = await provider.expose_port(ws, 8080, name="preview")
    _collect_provider_events(provider, events)
    state = provider.session_state(ws)
    attached = await provider.attach(state)
    _collect_provider_events(provider, events)
    snapshot = await provider.snapshot(attached, name="direct-sandbox-after")
    _collect_provider_events(provider, events)
    restored = await provider.restore(snapshot.ref)
    _collect_provider_events(provider, events)
    artifacts = await provider.list_artifacts(attached)
    exported = await provider.export_artifact(attached, snapshot.ref)
    _collect_provider_events(provider, events)
    await provider.close(ws)
    await provider.close(attached)
    await provider.close(restored, delete=True)
    _collect_provider_events(provider, events)

    _print_report(
        journey="direct_sandbox_workspace_ports_and_state",
        goal=(
            "A user uses a sandbox provider directly to open inputs, run commands, expose "
            "a preview port, snapshot, attach, restore, list artifacts, and close sessions."
        ),
        workspace_provider=provider.provider_id,
        prompt="Direct sandbox WorkspaceProvider API calls, no model prompt.",
        events=events,
        workspace=ws,
        session_state=state,
        extra={
            "files": files,
            "command": _command_summary(command),
            "port": _port_summary(port),
            "attached_workspace": _workspace_summary(attached),
            "restored_workspace": _workspace_summary(restored),
            "snapshot": _artifact_summary(snapshot),
            "exported": _artifact_summary(exported),
            "artifacts": _artifact_page_summary(artifacts),
        },
    )


async def test_journey_workspace_provider_capability_report() -> None:
    local = LocalWorkspaceProvider()
    sandbox = SandboxWorkspaceProvider(JourneySandboxClient())
    _print_report(
        journey="workspace_provider_capability_report",
        goal="A user inspects capability flags before choosing local or sandbox providers.",
        workspace_provider="mixed",
        prompt="Capability discovery only.",
        events=[],
        extra={
            "capabilities": {
                local.provider_id: _capability_summary(local.capabilities()),
                sandbox.provider_id: _capability_summary(sandbox.capabilities()),
            }
        },
    )


async def test_journey_workspace_path_safety_report(tmp_path: Path) -> None:
    _seed_demo_workspace(tmp_path)
    provider = LocalWorkspaceProvider()
    events: list[AgentEvent] = []
    errors: list[dict[str, str]] = []

    ws = await provider.open(WorkspaceSpec.local(tmp_path))
    _collect_provider_events(provider, events)
    for operation, call in [
        ("read_file", lambda: provider.read_file(ws, "../outside.txt")),
        ("write_file", lambda: provider.write_file(ws, "../outside.txt", "escape")),
        ("list_files", lambda: provider.list_files(ws, path="../")),
    ]:
        try:
            await call()
        except WorkspaceError as exc:
            errors.append({"operation": operation, "error": str(exc)})
    _collect_provider_events(provider, events)
    await provider.close(ws)
    _collect_provider_events(provider, events)

    _print_report(
        journey="workspace_path_safety_report",
        goal=(
            "A user attempts path-escape operations and receives workspace errors instead "
            "of touching files outside the workspace."
        ),
        workspace_provider=provider.provider_id,
        prompt="Direct WorkspaceProvider path safety checks, no model prompt.",
        events=events,
        workspace=ws,
        extra={"errors": errors},
    )


def _available_model_providers(
    *,
    function_tools: bool | None = None,
) -> list[LiveModelProvider]:
    providers = _all_configured_model_providers()
    requested = _requested_model_provider_keys()
    if requested is not None:
        providers = [provider for provider in providers if provider.key in requested]
    if function_tools is not None:
        providers = [
            provider
            for provider in providers
            if provider.supports_function_tools is function_tools
        ]
    if not providers:
        pytest.skip("No configured live model providers match this WorkspaceProvider journey.")
    return providers


def _all_configured_model_providers() -> list[LiveModelProvider]:
    providers: list[LiveModelProvider] = []
    if os.environ.get("OPENAI_API_KEY"):
        providers.append(
            LiveModelProvider(
                key="openai",
                model=os.environ.get(
                    "OPENAI_WORKSPACE_JOURNEY_MODEL",
                    os.environ.get("OPENAI_JOURNEY_MODEL", "gpt-4o-mini"),
                ),
                register=lambda runtime: runtime.registry.register_model(
                    OpenAIResponsesProvider(api_key=os.environ["OPENAI_API_KEY"])
                ),
            )
        )
    if os.environ.get("ANTHROPIC_API_KEY"):
        providers.append(
            LiveModelProvider(
                key="anthropic",
                model=os.environ.get(
                    "ANTHROPIC_WORKSPACE_JOURNEY_MODEL",
                    os.environ.get("ANTHROPIC_AGENT_JOURNEY_MODEL", "claude-haiku-4-5-20251001"),
                ),
                register=lambda runtime: runtime.registry.register_model(
                    AnthropicMessagesProvider(api_key=os.environ["ANTHROPIC_API_KEY"])
                ),
            )
        )
    google_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
    if google_key:
        providers.append(
            LiveModelProvider(
                key="google",
                model=os.environ.get(
                    "GEMINI_WORKSPACE_JOURNEY_MODEL",
                    os.environ.get("GEMINI_AGENT_JOURNEY_MODEL", "gemini-2.5-flash"),
                ),
                register=lambda runtime: runtime.registry.register_model(
                    GeminiGenerateContentProvider(api_key=google_key)
                ),
            )
        )
    if os.environ.get("XAI_API_KEY"):
        providers.append(
            LiveModelProvider(
                key="xai",
                model=os.environ.get(
                    "XAI_WORKSPACE_JOURNEY_MODEL",
                    os.environ.get("XAI_AGENT_JOURNEY_MODEL", os.environ.get("XAI_MODEL", "grok-4")),
                ),
                register=lambda runtime: runtime.registry.register_model(
                    XAIResponsesProvider(api_key=os.environ["XAI_API_KEY"])
                ),
            )
        )
    return providers


def _requested_model_provider_keys() -> set[str] | None:
    raw = os.environ.get("WORKSPACE_PROVIDER_JOURNEY_MODEL_PROVIDERS")
    if not raw:
        raw = os.environ.get("MODEL_PROVIDER_JOURNEY_PROVIDERS")
    if not raw:
        return None
    return {value.strip() for value in raw.split(",") if value.strip()}


def _runtime_for(provider: LiveModelProvider) -> AgentRuntime:
    runtime = AgentRuntime()
    provider.register(runtime)
    runtime.model_catalog.register_pricing(
        ModelPricing(
            provider=provider.key,
            model=provider.model,
            input_per_million=1.0,
            output_per_million=2.0,
            cache_read_input_per_million=0.25,
            cache_creation_input_per_million=1.25,
        )
    )
    return runtime


async def _collect_runtime_stream_with_approval(
    runtime: AgentRuntime,
    **kwargs: Any,
) -> tuple[list[AgentEvent], dict[str, Any]]:
    events: list[AgentEvent] = []

    async def collect() -> None:
        async for event in runtime.stream(**kwargs):
            events.append(event)

    task = asyncio.create_task(collect())
    approval_id: str | None = None
    for _ in range(600):
        for loop in runtime._active_loops.values():
            if loop._approvals:
                approval_id = next(iter(loop._approvals))
                await runtime.approve(
                    approval_id,
                    ApprovalDecision.approve("Approved by WorkspaceProvider journey."),
                )
                break
        if approval_id is not None or task.done():
            break
        await asyncio.sleep(0.1)

    timed_out = False
    try:
        await asyncio.wait_for(task, timeout=180)
    except TimeoutError:
        timed_out = True
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    return events, {
        "approval_id": approval_id,
        "approval_observed": approval_id is not None,
        "collector_timed_out": timed_out,
    }


def _seed_demo_workspace(root: Path) -> None:
    (root / "src").mkdir(parents=True, exist_ok=True)
    (root / "notes").mkdir(parents=True, exist_ok=True)
    (root / "README.md").write_text(
        "# Journey Demo\n\nThis workspace models a tiny coding-agent project.\n"
    )
    (root / "src" / "app.py").write_text("status = 'todo'\n")
    (root / "notes" / "obsolete.txt").write_text("remove me\n")


def _safe_files(root: Path) -> list[str]:
    if not root.exists():
        return []
    files: list[str] = []
    for path in root.rglob("*"):
        if path.is_file():
            files.append(path.relative_to(root).as_posix())
    return sorted(files)


def _safe_read(path: Path) -> str | None:
    try:
        return path.read_text()
    except OSError:
        return None


def _collect_provider_events(provider: Any, events: list[AgentEvent]) -> None:
    events.extend(provider.drain_events())


def _output_budget(provider: LiveModelProvider, requested: int) -> int:
    reasoning_heavy_prefixes = ("gpt-5", "o1", "o3", "o4")
    if provider.key == "openai" and provider.model.startswith(reasoning_heavy_prefixes):
        return max(requested, 2048)
    return requested


def _print_report(
    *,
    journey: str,
    goal: str,
    workspace_provider: str,
    prompt: str,
    events: Sequence[AgentEvent],
    model_provider: LiveModelProvider | None = None,
    response_text: str | None = None,
    workspace: WorkspaceRef | None = None,
    session_state: WorkspaceSessionState | None = None,
    metadata: dict[str, Any] | None = None,
    artifacts: Sequence[Artifact] | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    report = {
        "journey": journey,
        "goal": goal,
        "workspace_provider": workspace_provider,
        "model_provider": (
            {"provider": model_provider.key, "model": model_provider.model}
            if model_provider is not None
            else None
        ),
        "prompt": prompt,
        "response_text": _clip(response_text if response_text is not None else _collected_text(events)),
        "workspace": _workspace_summary(workspace),
        "session_state": _session_state_summary(session_state),
        "event_summary": _event_summary(events),
        "workspace_event_summary": _workspace_event_summary(events),
        "tool_results": _tool_results(events),
        "metadata": _jsonable(metadata or {}),
        "artifacts": [_artifact_summary(artifact) for artifact in artifacts or []],
        "extra": _jsonable(extra or {}),
    }
    print("\n=== WORKSPACE_PROVIDER_JOURNEY_REPORT ===")
    print(json.dumps(report, indent=2, sort_keys=True, default=str))
    print("=== END_WORKSPACE_PROVIDER_JOURNEY_REPORT ===\n")


def _event_summary(events: Sequence[AgentEvent]) -> dict[str, Any]:
    counts = Counter(event.type for event in events)
    return {
        "total_events": len(events),
        "counts": dict(sorted(counts.items())),
        "samples": [_event_sample(event) for event in events[:16]],
    }


def _event_sample(event: AgentEvent) -> dict[str, Any]:
    return {
        "type": event.type,
        "provider": event.provider,
        "item_id": event.item_id,
        "sequence": event.sequence,
        "data_keys": sorted(event.data),
        "tool_name": event.data.get("name") or event.data.get("tool"),
        "workspace_id": event.data.get("workspace_id"),
        "approval_id": event.data.get("approval_id"),
    }


def _workspace_event_summary(events: Sequence[AgentEvent]) -> list[dict[str, Any]]:
    return [
        {
            "type": event.type,
            "workspace_id": event.data.get("workspace_id"),
            "workspace_kind": event.data.get("workspace_kind"),
            "workspace_provider": event.data.get("workspace_provider"),
            "path": event.data.get("path"),
            "command": event.data.get("command"),
            "exit_code": event.data.get("exit_code"),
            "artifact_id": _artifact_id_from_event(event),
        }
        for event in events
        if str(event.type).startswith("workspace.") or event.type == EventTypes.ARTIFACT_CREATED
    ]


def _artifact_id_from_event(event: AgentEvent) -> str | None:
    artifact = event.data.get("artifact")
    if isinstance(artifact, Artifact):
        return artifact.id
    value = event.data.get("snapshot_id") or event.data.get("patch_id")
    return str(value) if value is not None else None


def _collected_text(events: Sequence[AgentEvent]) -> str:
    chunks: list[str] = []
    for event in events:
        if event.type == EventTypes.MODEL_TEXT_DELTA:
            value = event.data.get("delta") or event.data.get("message")
            if value is not None:
                chunks.append(str(value))
    return "".join(chunks)


def _tool_results(events: Sequence[AgentEvent]) -> list[dict[str, Any]]:
    return [
        {
            "call_id": event.data.get("call_id"),
            "name": event.data.get("name"),
            "content": _clip(str(event.data.get("content") or ""), limit=900),
            "metadata": _jsonable(event.data.get("metadata") or {}),
            "payload": _jsonable(event.data.get("payload") or {}),
        }
        for event in events
        if event.type == EventTypes.TOOL_CALL_COMPLETED
    ]


def _payload_summary(payloads: Sequence[Any]) -> list[dict[str, Any]]:
    return [
        {
            "tool_name": getattr(payload, "tool_name", None),
            "content": _clip(str(getattr(payload, "content", "") or ""), limit=700),
            "metadata": _jsonable(getattr(payload, "metadata", {}) or {}),
            "payload": _jsonable(getattr(payload, "payload", {}) or {}),
        }
        for payload in payloads
    ]


def _workspace_summary(workspace: WorkspaceRef | None) -> dict[str, Any] | None:
    if workspace is None:
        return None
    return {
        "id": workspace.id,
        "kind": workspace.kind,
        "provider": workspace.provider,
        "root": workspace.root,
        "provider_workspace_id": workspace.provider_workspace_id,
        "provider_session_id": workspace.provider_session_id,
        "metadata_keys": sorted(workspace.metadata),
        "snapshot_id": workspace.snapshot.id if workspace.snapshot is not None else None,
    }


def _session_state_summary(state: WorkspaceSessionState | None) -> dict[str, Any] | None:
    if state is None:
        return None
    return {
        "provider": state.provider,
        "workspace_id": state.workspace_id,
        "kind": state.kind,
        "provider_workspace_id": state.provider_workspace_id,
        "provider_session_id": state.provider_session_id,
        "root": state.root,
        "snapshot_id": state.snapshot_id,
        "serialized_state_keys": sorted(state.serialized_state),
        "metadata_keys": sorted(state.metadata),
    }


def _change_summary(change: Any) -> dict[str, Any]:
    if isinstance(change, FileChange):
        return {
            "path": change.path,
            "type": change.type,
            "old_path": change.old_path,
            "content_excerpt": _clip(change.content, limit=500),
        }
    return {
        "id": getattr(change, "id", None),
        "summary": getattr(change, "summary", None),
        "changes": [_change_summary(item) for item in getattr(change, "changes", [])],
    }


def _command_summary(command: CommandResult) -> dict[str, Any]:
    return {
        "command_id": command.command_id,
        "exit_code": command.exit_code,
        "succeeded": command.succeeded,
        "timed_out": command.timed_out,
        "stdout": _clip(command.stdout, limit=900),
        "stderr": _clip(command.stderr, limit=900),
        "metadata": _jsonable(command.metadata),
        "artifact_count": len(command.artifacts),
    }


def _artifact_page_summary(page: ArtifactPage) -> dict[str, Any]:
    return {
        "count": len(page.items),
        "has_more": page.has_more,
        "next_cursor": page.next_cursor,
        "items": [_artifact_summary(artifact) for artifact in page.items],
    }


def _artifact_summary(artifact: Artifact) -> dict[str, Any]:
    return {
        "id": artifact.id,
        "type": artifact.type,
        "name": artifact.name,
        "uri": artifact.uri,
        "data_present": artifact.data is not None,
        "metadata_keys": sorted(artifact.metadata),
    }


def _port_summary(port: Any) -> dict[str, Any]:
    return {
        "id": getattr(port, "id", None),
        "workspace_id": getattr(port, "workspace_id", None),
        "port": getattr(port, "port", None),
        "protocol": getattr(port, "protocol", None),
        "host": getattr(port, "host", None),
        "url": getattr(port, "url", None),
        "name": getattr(port, "name", None),
        "metadata": _jsonable(getattr(port, "metadata", {}) or {}),
    }


def _capability_summary(caps: WorkspaceProviderCapabilities) -> dict[str, bool]:
    return {
        "local_files": caps.supports_local_files,
        "sandbox": caps.supports_sandbox,
        "git_sources": caps.supports_git_sources,
        "cloud_refs": caps.supports_cloud_refs,
        "commands": caps.supports_commands,
        "streaming_command_output": caps.supports_streaming_command_output,
        "patches": caps.supports_patches,
        "snapshots": caps.supports_snapshots,
        "restore": caps.supports_restore,
        "artifacts": caps.supports_artifacts,
        "ports": caps.supports_ports,
        "approvals": caps.supports_approvals,
        "resume": caps.supports_resume,
    }


def _jsonable(value: Any) -> Any:
    try:
        json.dumps(value, default=str)
    except TypeError:
        return str(value)
    return value


def _clip(text: str | None, limit: int = 4000) -> str:
    if text is None:
        return ""
    if len(text) <= limit:
        return text
    return text[:limit] + "\n...[truncated]"
