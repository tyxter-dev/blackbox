# 07 — Feature: Workspaces

**Facade:** `runtime.workspaces` · **Package:** `src/blackbox/workspaces/`

## Summary

The workspace layer represents *where* agent work happens — the filesystem,
repository, or sandbox an agent reads, writes, and runs commands against.
`WorkspaceProvider` is a first-class facade with local, git, sandbox, Docker, and
cloud backends. The runtime does **not** assume all workspaces are locally
accessible; cloud providers may return opaque references.

## Workspace types

- local directory
- Git repository
- provider sandbox
- cloud container
- mounted artifact bundle
- remote workspace reference

## Backends

`local.py`, `git.py`, `sandbox.py`, `docker.py`, `cloud.py` — all present in
`src/blackbox/workspaces/`. (Note: PRD §22 M2 once listed git/sandbox/cloud as
pending; those have since shipped — `FEATURES.md` is the single status source.)

## Data contracts

`WorkspaceRef`, `WorkspaceMount`, `FileChange`, `PatchArtifact`, `CommandSpec`,
`CommandResult`. Workspace tools are registered through
`runtime.tools.register_workspace(...)`.

## Workspace events

`workspace.file.read`, `workspace.file.changed` (and write/edit/delete),
`workspace.command.started`, `workspace.command.completed`,
`workspace.patch.created`, `workspace.snapshot.created`. Each maps to a `RunItem`
where durable.

## Policy gates

Workspace operations pass through policy checkpoints (see [09](09-approvals-and-policy.md)):

- `before_workspace_write`
- `before_command`
- `before_artifact_export`

The local `WorkspaceRuntime` implements file read/write/delete, patch artifact,
snapshot, and command events behind these gates.

## Requirements

| ID | Priority | Requirement |
|---|---|---|
| P1-R9 | P1 | Represent repo/local/sandbox/cloud workspace specs and file changes. |

## Hard constraints

- Don't assume local accessibility — cloud workspaces may be opaque references
  with provider-native handles preserved.
- Workspace tools are a distinct backend (not local Python tools) — see
  [05](05-tools.md).

## Status & references

Local + git + sandbox + Docker + cloud backends shipped. Still pending:
approval-channel integration at workspace checkpoints (wire `before_command` /
`before_workspace_write` to the approval event/decision flow the way MCP
approvals already are — Horizon 1); richer artifact export and replay behavior.
Artifacts produced here flow through `ArtifactPage`. Docs: `docs/WORKSPACE.md`.
Tests: `tests/unit/workspaces/`. PRD §15, §22 M2.

→ Next: [08 — Workspace agent packages](08-workspace-agents.md)
