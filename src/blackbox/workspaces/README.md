# workspaces

`workspaces` owns execution substrates for coding agents: files, commands,
patches, snapshots, artifacts, preview ports, and resumable workspace state.

## Belongs Here

- `WorkspaceProvider` contract and workspace data specs.
- Local, git, sandbox, docker, cloud, and fake workspace providers.
- Workspace command/file/patch/snapshot/port behavior.
- Policy and approval handling for workspace operations.
- Workspace-backed tool registration helpers.

## Does Not Belong Here

- Model-provider or agent-provider API mapping.
- Workspace-agent package metadata, publication, scheduling, or permissions.
- Business-domain tools unrelated to workspace execution.

## Boundary Note

`workspace_agents/` describes governed agent packages. `workspaces/` describes
where an agent acts. A workspace agent may reference workspaces, but it should
not own filesystem or sandbox execution semantics.
