# Workspace Agent Package Contracts

Status: implemented as core contracts.

This spec defines reusable package metadata for governed workspace agents. It is
part of the runtime's third core surface alongside `ModelProvider` and
`AgentProvider`: workspace contracts. It intentionally stops at portable
contracts: product UI, org RBAC, OAuth, secret storage, queues, cron execution,
billing, and multi-tenant persistence belong in downstream applications.

## Core Surface

`agent_runtime.workspace_agents` exposes:

- `WorkspaceAgentSpec` - packaged agent definition with instructions, model
  preference, tools, hosted tools, MCP servers, connectors, permissions,
  schedules, skills, memory policy, publication metadata, version, and owner
  metadata.
- `ConnectorSpec` - external connector declaration with `end_user` or
  `agent_owned` authorization mode.
- `ToolPermission` and `ApprovalRequirement` - declarative tool/connector
  scopes and approval policy hints.
- `ScheduleSpec` and `ScheduledRunRef` - schedule description and run metadata;
  execution is delegated to downstream schedulers.
- `WorkspaceAgentRegistry` - persistence protocol, with
  `InMemoryWorkspaceAgentRegistry` for tests and prototypes.
- `workspace_agent_to_dict` / `workspace_agent_from_dict` - serialization
  helpers for the package dataclasses.
- `run_workspace_agent` - thin bridge into the existing high-level
  `AgentRuntime.run(...)` loop.

## Boundary

Belongs in core:

- package/schema contracts,
- workspace references, changes, command, patch, and snapshot contracts,
- connector auth mode declarations,
- permission and approval hints,
- schedule metadata,
- registry protocols,
- event/policy integration points,
- import/export helpers.

Belongs downstream:

- admin console and agent directory UI,
- org/team RBAC,
- OAuth/device flows and secret vaults,
- real cron/queue execution,
- approval UI,
- Slack/email/web delivery,
- billing and tenancy.

## Design Rule

The runtime may prepare, validate, serialize, and execute a package through
existing providers. It must not assume any particular business workspace,
database, scheduler, identity provider, or deployment platform.

## Future WorkspaceProvider

The current implementation has `WorkspaceRuntime` for local workspaces and
workspace-agent package contracts. A future `WorkspaceProvider` protocol should
make workspace backends pluggable in the same spirit as `ModelProvider` and
`AgentProvider`.

Candidate responsibilities:

- open or attach to local, git, sandbox, or cloud workspaces,
- read/write/delete files under policy gates,
- run commands under policy gates,
- create patch and snapshot artifacts,
- list/export artifacts,
- preserve provider-native workspace references for cloud backends.
