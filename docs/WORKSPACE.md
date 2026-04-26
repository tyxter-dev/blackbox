# WorkspaceProvider Framework PRD

**Framework:** `WorkspaceProvider`
**Parent PRD:** `PRD.md` (Agent Runtime Core)
**Document status:** Framework PRD, implementation-aligned draft
**Last reviewed against code:** 2026-04-26

## 1. Product Purpose

`WorkspaceProvider` is the execution-substrate framework for giving agents a
safe, observable place to work: local directories, sandboxes, future git
checkouts, future cloud workspaces, files, commands, patches, snapshots, ports,
artifacts, policy gates, approvals, and resumable workspace state.

It exists because serious coding-agent workflows are not only model calls and
tool dispatch. Agents need governed access to files, commands, diffs, logs,
previews, snapshots, and provider-native workspace references.

The product promise is:

```text
Applications can pass a workspace to the runtime or use a workspace provider
directly, and agents can inspect, modify, execute, snapshot, and export work
through one policy-gated contract.
```

## 2. Relationship To The Core PRD

The root PRD separates model, agent, and workspace surfaces:

```text
ModelProvider     -> runs model turns
AgentProvider     -> supervises agent sessions
WorkspaceProvider -> provides places where agents act
```

This document expands the `WorkspaceProvider` layer. It is not a fourth
workflow profile and not a replacement for `AgentProvider`. A sandbox is a
workspace backend, not the product's main abstraction.

Workspace-agent package contracts (`WorkspaceAgentSpec`, registries,
permissions, schedules, connector declarations, publication metadata) remain a
separate schema layer. They can reference workspaces, but they do not own
filesystem or command execution.

## 3. Framework Boundary

### In Scope

`WorkspaceProvider` owns:

```text
open / attach / close workspace
read / write / delete / list files
apply patches
run commands
stream command output
create and restore snapshots
list and export artifacts
expose preview ports where supported
serialize workspace session state
enforce workspace policy checkpoints
surface approval-required operations
emit canonical workspace events
normalize patch, log, snapshot, port, and provider-ref artifacts
```

### Out Of Scope

`WorkspaceProvider` does not own:

```text
model turn execution
agent session lifecycle
provider-managed agent creation
business workspace package registry
team directory / UI / RBAC
OAuth or secret vault implementation
Slack/email/web delivery
scheduler/cron infrastructure
billing/tenancy
```

## 4. Current Implementation Snapshot

| Area | Current evidence | State |
|---|---|---|
| Core protocol | `src/agent_runtime/workspaces/provider.py` | Implemented. `WorkspaceProvider` includes open/attach/close, file ops, patches, commands, snapshots, restore, ports, artifacts, approvals, state, and event draining. |
| Data contracts | `src/agent_runtime/workspaces/spec.py`, `src/agent_runtime/workspaces/changes.py` | Implemented. `WorkspaceSpec`, `WorkspaceRef`, `WorkspaceSessionState`, capabilities, ports, commands, file changes, patches, and patch artifacts exist. |
| Local backend | `src/agent_runtime/workspaces/local.py` | Implemented. Local files, commands, patches, snapshots, restore, artifacts, policy gates, approvals, and path safety. |
| Sandbox backend | `src/agent_runtime/workspaces/sandbox.py` | Implemented. Client-backed sandbox workspace with commands, streaming output, patches, snapshots, restore, ports when client supports them, artifacts, approvals, and state. |
| Sandbox clients | `src/agent_runtime/workspaces/fake.py`, `src/agent_runtime/workspaces/docker.py`, `src/agent_runtime/workspaces/sandbox_client.py` | Implemented. Fake client supports deterministic tests; Docker client starts containers and maps host workspace state. |
| Runtime integration | `src/agent_runtime/runtime.py` | Implemented. `runtime.run/stream(..., workspace=...)` opens local workspaces automatically and requires an explicit provider for sandbox/non-local kinds. |
| Workspace tools | `src/agent_runtime/workspaces/tools.py` | Implemented. Registers read/write/delete/list/apply_patch/run_command/snapshot/expose_port tools against a provider/ref. |
| Backward-compatible alias | `src/agent_runtime/workspaces/runtime.py` | Implemented. `WorkspaceRuntime` aliases the local provider for older local-workspace tests/imports. |
| Tests | `tests/unit/test_workspace_runtime.py`, `tests/unit/test_local_workspace_provider.py`, `tests/unit/test_sandbox_workspace_provider.py`, `tests/unit/test_workspace_provider_contracts.py`, `tests/runtime/test_workspace_tool_loop.py` | Current behavior is covered by unit, contract, and runtime loop tests. |

## 5. Primary Users

| User | Need | Value |
|---|---|---|
| Technical founder | Build coding agents without committing to one execution backend | Local and sandbox now; git/cloud later. |
| Backend engineer | Let agents safely inspect, edit, and run commands | One policy-gated execution contract. |
| Agent platform engineer | Compare local, sandbox, and provider-hosted workspaces | Shared events, artifacts, and state. |
| QA/evaluation engineer | Replay what an agent did | File, command, patch, snapshot, and artifact event logs. |
| Security/platform engineer | Gate risky operations | Standard policy checkpoints and approval events. |

## 6. Core Workflows

### W1: Run A Task With A Local Workspace

```python
result = await runtime.run(
    provider="openai:gpt-5.4",
    input="Fix the failing test and return a patch.",
    workspace=WorkspaceSpec.local("./repo"),
    output_type=FixResult,
)
```

The runtime opens the local workspace, registers workspace tools into the
run-scoped tool session, emits workspace events, and closes the workspace when
the run finishes.

### W2: Run A Task In A Sandbox

```python
workspace_provider = SandboxWorkspaceProvider(
    client=DockerSandboxClient(image="python:3.12"),
)

result = await runtime.run(
    provider="anthropic:claude-sonnet",
    input="Fix the bug, run pytest, and return the diff.",
    workspace=WorkspaceSpec.sandbox(inputs={"repo": "./repo"}),
    workspace_provider=workspace_provider,
)
```

Sandbox is a backend implementation of `WorkspaceProvider`. It should not
become a separate agent profile.

### W3: Use The Provider Directly

```python
provider = LocalWorkspaceProvider()
ws = await provider.open(WorkspaceSpec.local("./repo"))

content = await provider.read_file(ws, "src/app.py")
result = await provider.run_command(ws, CommandSpec(command="pytest -q"))
snapshot = await provider.snapshot(ws)
```

Direct use is for advanced orchestration, tests, and integrations. The normal
coding-agent path should be `runtime.run(..., workspace=...)`.

### W4: Preserve Workspace State

```python
state = provider.session_state(ws)
reattached = await provider.attach(state)
```

Local and sandbox providers serialize enough state to reattach when the backing
workspace still exists.

### W5: Approval-Gated Writes Or Commands

```text
agent requests workspace_run_command("rm -rf build")
policy returns require_approval
runtime emits approval.requested
human approves or denies
runtime resumes or returns a denied tool result
```

Direct provider calls raise `WorkspaceApprovalRequired`. Calls through
`AgentLoop` convert the pending workspace operation into approval events.

## 7. Public Contract

The current protocol is:

```python
@runtime_checkable
class WorkspaceProvider(Protocol):
    @property
    def provider_id(self) -> str:
        ...

    def capabilities(self) -> WorkspaceProviderCapabilities:
        ...

    async def open(self, spec: WorkspaceSpec) -> WorkspaceRef:
        ...

    async def attach(self, state: WorkspaceSessionState) -> WorkspaceRef:
        ...

    async def close(self, ws: WorkspaceRef, *, delete: bool = False) -> None:
        ...

    async def read_file(self, ws: WorkspaceRef, path: str) -> str:
        ...

    async def write_file(self, ws: WorkspaceRef, path: str, content: str) -> FileChange:
        ...

    async def delete_file(self, ws: WorkspaceRef, path: str) -> FileChange:
        ...

    async def list_files(
        self,
        ws: WorkspaceRef,
        *,
        path: str = ".",
        recursive: bool = False,
        limit: int = 1000,
    ) -> list[str]:
        ...

    async def apply_patch(self, ws: WorkspaceRef, patch: Patch) -> PatchArtifact:
        ...

    async def run_command(self, ws: WorkspaceRef, spec: CommandSpec) -> CommandResult:
        ...

    def stream_command(
        self,
        ws: WorkspaceRef,
        spec: CommandSpec,
    ) -> AsyncIterator[AgentEvent]:
        ...

    async def snapshot(self, ws: WorkspaceRef, *, name: str | None = None) -> Artifact:
        ...

    async def restore(
        self,
        snapshot: ArtifactRef,
        *,
        spec: WorkspaceSpec | None = None,
    ) -> WorkspaceRef:
        ...

    async def expose_port(
        self,
        ws: WorkspaceRef,
        port: int,
        *,
        protocol: str = "http",
        name: str | None = None,
    ) -> WorkspacePort:
        ...

    async def list_artifacts(
        self,
        ws: WorkspaceRef,
        *,
        type: str | None = None,
        after: str | None = None,
        limit: int = 100,
    ) -> ArtifactPage:
        ...

    async def export_artifact(self, ws: WorkspaceRef, ref: ArtifactRef) -> Artifact:
        ...

    async def approve(self, approval_id: str, decision: ApprovalDecision) -> None:
        ...

    def session_state(self, ws: WorkspaceRef) -> WorkspaceSessionState:
        ...

    def drain_events(self) -> list[AgentEvent]:
        ...
```

## 8. Data Contracts

Primary contracts:

```text
WorkspaceSpec
WorkspaceRef
WorkspaceMount
WorkspaceSessionState
WorkspaceProviderCapabilities
WorkspacePort
CommandSpec
CommandResult
FileChange
Patch
PatchArtifact
Artifact
ArtifactRef
ArtifactPage
AgentEvent
ApprovalDecision
PendingWorkspaceOperation
WorkspaceApprovalRequired
```

`WorkspaceSpec.kind` currently supports:

```text
local
git
sandbox
cloud
```

Implemented providers support `local` and `sandbox`. `git` and `cloud` are
contract placeholders until adapters land.

## 9. Event Taxonomy

Workspace providers should emit:

```text
workspace.opened
workspace.closed
workspace.file.read
workspace.file.changed
workspace.file.listed
workspace.command.started
workspace.command.output
workspace.command.completed
workspace.patch.created
workspace.snapshot.created
workspace.snapshot.restored
workspace.artifact.exported
workspace.port.exposed
workspace.port.closed
artifact.created
approval.requested
approval.approved
approval.denied
```

Events should carry `workspace_id`, `workspace_kind`, and `workspace_provider`
where possible so downstream traces and results can group artifacts and actions
by workspace.

## 10. Requirements

### P0 - Required

| ID | Requirement | Current state | Acceptance criteria |
|---|---|---|---|
| W-P0-1 | Stable provider identity | Implemented | Every provider exposes `provider_id`. |
| W-P0-2 | Capability honesty | Implemented | Providers advertise local files, sandbox, commands, patches, snapshots, restore, artifacts, ports, approvals, and resume conservatively. |
| W-P0-3 | Open/attach workspace | Implemented for local and sandbox | Providers return `WorkspaceRef` and can attach from `WorkspaceSessionState` when backing state still exists. |
| W-P0-4 | Path safety | Implemented | File and command paths cannot escape workspace root. |
| W-P0-5 | File operations | Implemented | Read/write/delete/list produce typed results and workspace events. |
| W-P0-6 | Command operations | Implemented | Commands run under policy gates and return `CommandResult` with stdout/stderr/exit/timing metadata. |
| W-P0-7 | Patch artifacts | Implemented | `apply_patch(...)` applies structured changes and returns `PatchArtifact`. |
| W-P0-8 | Snapshot artifacts | Implemented | `snapshot(...)` creates a workspace snapshot artifact. |
| W-P0-9 | Workspace events | Implemented | Providers emit canonical workspace events and expose `drain_events()`. |
| W-P0-10 | Runtime integration | Implemented | `runtime.run/stream(..., workspace=...)` opens workspaces and registers workspace tools for the run. |
| W-P0-11 | Result integration | Implemented | Workspace artifacts emitted by tools appear in `AgentResult.artifacts` and result metadata. |
| W-P0-12 | Approval integration | Implemented | Policy-required approvals surface as `WorkspaceApprovalRequired` directly and approval events through `AgentLoop`. |

### P1 - Important

| ID | Requirement | Current state | Acceptance criteria |
|---|---|---|---|
| W-P1-1 | Sandbox provider | Implemented | `SandboxWorkspaceProvider` works with fake and Docker clients. |
| W-P1-2 | Restore snapshots | Implemented | Snapshot artifacts can create a new local or sandbox workspace. |
| W-P1-3 | Command streaming | Partial | Sandbox streams command output events; local provider currently runs then drains output events. |
| W-P1-4 | Artifact export | Implemented | Providers can export known artifacts and represent provider refs. |
| W-P1-5 | Port exposure | Partial | Protocol and sandbox support exist; local and Docker clients correctly raise unsupported. |
| W-P1-6 | Workspace state persistence | Partial | Providers serialize state; storing/reloading it across durable runs needs a standard integration. |
| W-P1-7 | Workspace policy checkpoints | Implemented | Open/read/write/command/export/restore/port gates exist through the shared policy protocol. |
| W-P1-8 | Workspace tool adapter | Implemented | Existing workspace operations register as runtime tools with workspace metadata and artifacts. |
| W-P1-9 | Automatic non-local opening | Partial | Local workspaces auto-open; sandbox/git/cloud require explicit providers. |

### P2 - Later

| ID | Requirement | Acceptance criteria |
|---|---|---|
| W-P2-1 | Git provider | Clone/checkout/branch/diff/commit support behind `WorkspaceProvider`. |
| W-P2-2 | Cloud workspace provider | Opaque provider-hosted workspace refs and artifact export. |
| W-P2-3 | Provider sandbox adapters | OpenAI/Claude/provider-native sandboxes can be normalized when APIs expose them. |
| W-P2-4 | Workspace memory | Durable lessons/facts remain separate from provider state and workspace snapshots. |
| W-P2-5 | Rich diff metadata | Downstream UIs get patch hunks, file status, preview metadata, and review hints. |
| W-P2-6 | Port lifecycle | Port close/reopen/list semantics are consistent across capable providers. |

## 11. Runtime Integration

Canonical high-level path:

```text
runtime.run(input, provider, workspace, tools, output_type)
  -> resolve ModelProvider
  -> resolve/open WorkspaceProvider
  -> register run-scoped workspace tools
  -> AgentLoop drives model turns
  -> model requests workspace operations
  -> workspace provider executes under policy
  -> events/artifacts flow back through ToolResult
  -> runtime validates final output
  -> AgentResult[T]
```

Important constraints:

1. The user should not have to manually register workspace tools for ordinary
   `runtime.run(..., workspace=...)` usage.
2. Non-local workspace kinds require an explicit `workspace_provider` until the
   runtime has safe defaults for those backends.
3. Workspace tools must be run-scoped so one task does not leak another task's
   workspace into the global tool registry.
4. Workspace artifacts and session state must remain visible in result metadata.

## 12. Non-Goals

`WorkspaceProvider` must not:

```text
become a cloud-agent provider
become a business workspace agent registry
own scheduling
own org/team RBAC
own OAuth/device auth flows
own billing or tenancy
turn sandbox into the main product abstraction
force all providers to expose a local filesystem
```

## 13. Gaps And Decisions

1. **Git backend:** `WorkspaceSpec.git(...)` exists, but no provider implements
   clone/checkout/diff/commit yet.
2. **Cloud backend:** `WorkspaceSpec.kind="cloud"` exists, but no cloud
   workspace provider implements opaque remote refs or export semantics.
3. **Local streaming:** Local `stream_command(...)` should either stream process
   output incrementally or advertise that it is post-run event replay.
4. **Port lifecycle:** `expose_port(...)` exists, but close/list/reopen behavior
   and provider URL security rules need a standard.
5. **Durable state storage:** Providers serialize workspace state. Decide how
   `RunStore` or a future workspace store persists and reattaches it.
6. **Provider-native workspace handoff:** Align `TaskSpec.workspace` for
   `AgentProvider` sessions with `WorkspaceProvider` refs so cloud agents and
   local agents share one workspace vocabulary.
7. **Artifact export policy:** Export currently uses provider policy gates.
   Define redaction, size limits, and remote-download behavior before cloud
   providers land.

## 14. Definition Of Done

The framework is successful when:

```text
1. LocalWorkspaceProvider and SandboxWorkspaceProvider share one contract and
   pass provider contract tests.
2. runtime.run(..., workspace=...) works without manual workspace tool
   registration for ordinary local tasks.
3. Workspace operations emit canonical events and preserve workspace metadata.
4. Workspace artifacts appear in AgentResult.artifacts and result metadata.
5. Write, command, export, restore, and port operations can require approval.
6. Workspace state can be serialized, persisted, and reattached.
7. Unsupported backend capabilities raise typed errors.
8. Sandbox remains a backend, not a separate agent profile.
9. Workspace-agent package contracts remain separate from execution backends.
```
