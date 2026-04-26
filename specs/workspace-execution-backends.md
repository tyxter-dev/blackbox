# Workspace Execution Backends for Single-Run Autonomous Agents

Status: active architecture target.

This spec positions workspace and sandbox support as execution substrate for
the existing multi-provider runtime. Sandbox is not a fourth agent workflow
profile. It is one workspace backend that lets an autonomous run operate on
files, commands, patches, logs, snapshots, ports, and artifacts.

## Product Promise

The primary surface remains `AgentRuntime.run(...)`:

```python
result = await runtime.run(
    provider="openai:gpt-5.4",
    input="Inspect the repo, fix the failing test, run pytest, and return the patch.",
    workspace=WorkspaceSpec.sandbox(
        image="python:3.12",
        inputs={"repo": "./my_repo"},
    ),
)
```

The developer gives a task, tools, optional output schema, and optional
workspace. The runtime owns the loop until final result, failure, approval,
or cancellation. Application code should not need to manually parse tool
calls, dispatch workspace operations, append tool results, collect workspace
artifacts, or validate final JSON.

## Workflow Profiles

Agent workflow profile means how the task is supervised. Workspace backend
means where actions happen.

The project has three profiles:

- Single-run autonomous agent: `runtime.run(...)` owns model/tool/workspace
  iteration and returns `AgentResult[T]`.
- Provider-managed agent session: provider-native long-running session where
  a backend may expose hosted files, sandboxes, approvals, and artifacts.
- Governed workspace agent package: portable package metadata for tools,
  hosted tools, MCP, connectors, permissions, schedules, skills, memory, and
  workspace requirements.

Workspace backends can be used by any profile:

- local filesystem workspace,
- subprocess or Docker sandbox,
- provider-hosted sandbox,
- git-backed checkout,
- cloud workspace.

Sandbox is therefore a backend, not a profile.

## Runtime-Centered Integration

`AgentRuntime.run(...)` and `AgentRuntime.stream(...)` should accept:

```python
workspace=WorkspaceSpec.local("./repo")
workspace=WorkspaceSpec.sandbox(image="python:3.12", inputs={"repo": "./repo"})
workspace_provider=SandboxWorkspaceProvider(client=...)
```

Runtime responsibilities:

- open or attach the workspace,
- materialize inputs,
- expose model-facing workspace tools internally,
- run the model/tool/workspace loop,
- handle policy and approvals,
- stream canonical workspace events,
- collect patch, log, command output, port, and snapshot artifacts,
- persist workspace session state in result metadata,
- close or preserve the workspace according to runtime configuration.

The existing `runtime.tools.register_workspace(...)` API remains supported as
an advanced escape hatch for applications that need explicit provider/ref/tool
control.

## Provider Layer

`WorkspaceProvider` is infrastructure, parallel in seriousness to
`ModelProvider` and `AgentProvider`, but not the canonical application story.

Implementations:

- `LocalWorkspaceProvider`
- `SandboxWorkspaceProvider`
- `GitWorkspaceProvider`
- `CloudWorkspaceProvider`
- `OpenAISandboxWorkspaceProvider`
- provider-specific adapters such as Claude Code workspace bridges

Provider responsibilities:

- truthfully advertise capabilities,
- validate paths so relative paths cannot escape the workspace root,
- emit canonical workspace events,
- preserve provider-native IDs in `WorkspaceRef` and `WorkspaceSessionState`,
- return typed artifacts or provider references,
- support snapshot and restore where advertised,
- raise typed runtime errors for unsupported or failed operations.

## Sandbox Layer

`SandboxWorkspaceProvider` wraps a `SandboxClient`.

`SandboxClient` owns the concrete execution environment:

- create, attach, close, restore session,
- read/write/delete/list files,
- run or stream commands,
- snapshot,
- expose ports when supported,
- return serializable session state.

The core package must not add a hard dependency on Docker, Kubernetes, Modal,
E2B, OpenAI Agents SDK, or other sandbox providers. Docker support should use
the Docker CLI through stdlib subprocess calls. Provider-hosted adapters should
be optional extras.

Docker/local sandboxing must not be described as a complete security boundary.
It is an execution backend whose isolation depends on the configured client.

## Artifacts and Events

Workspace events are first-class runtime events and appear in
`AgentResult.events`:

- `workspace.opened`
- `workspace.closed`
- `workspace.file.read`
- `workspace.file.changed`
- `workspace.file.listed`
- `workspace.command.started`
- `workspace.command.output`
- `workspace.command.completed`
- `workspace.patch.created`
- `workspace.snapshot.created`
- `workspace.snapshot.restored`
- `workspace.port.exposed`
- `workspace.artifact.exported`
- `artifact.created`

Artifacts appear in `AgentResult.artifacts`:

- `file`
- `patch`
- `diff`
- `log`
- `command_output`
- `workspace_snapshot`
- `provider_ref`
- `port`

Provider-hosted artifacts should remain provider references unless explicitly
exported. No backend should silently download large remote artifacts while
listing artifacts.

## Approvals

Workspace policy gates use the same approval flow as local tools:

- `before_workspace_open`
- `before_workspace_read`
- `before_workspace_write`
- `before_command`
- `before_artifact_export`
- `before_workspace_restore`
- `before_port_expose`

When approval is required, workspace providers raise a resumable workspace
approval exception. The agent loop emits `approval.requested`, waits for an
approval decision, resumes the operation if approved, or returns a failed tool
result if denied.

## Resume State

Workspace state is not model provider continuation state. It belongs in run
metadata:

```python
result.metadata["workspaces"][workspace_id]["session_state"]
```

This keeps `ProviderState` focused on provider-native model continuation while
workspace resume uses `WorkspaceSessionState` or snapshot artifacts.

## Package Layer

`WorkspaceAgentSpec` can declare workspace requirements, but packages do not
own sandbox execution. A package can ask for capabilities:

```python
workspace=WorkspaceRequirement(
    required=True,
    capabilities=["files", "commands", "patches", "snapshots"],
    isolation="sandbox_preferred",
)
```

The runtime or downstream application chooses an actual workspace backend for
the run.
