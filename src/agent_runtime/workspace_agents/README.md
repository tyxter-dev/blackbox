# workspace_agents

`workspace_agents` owns portable package contracts for governed agents. It is a
schema and registry layer, not an execution backend.

## Belongs Here

- `WorkspaceAgentSpec` and supporting metadata/version/publication specs.
- Tool and connector permission declarations.
- Schedule declarations and scheduled-run references.
- Workspace-agent registry interfaces and in-memory registry.
- Serialization helpers for package specs.
- Thin bridges that prepare or run a workspace-agent spec through the runtime.

## Does Not Belong Here

- Workspace filesystem, command, patch, or sandbox implementation.
- Provider-specific model or cloud-agent adapter logic.
- OAuth vaults, billing, tenancy, or UI concerns.

## Boundary Note

This package models what an agent package is allowed to use and how it is
published or scheduled. Actual execution is delegated to `runtime`, `agents`,
`tools`, `mcp`, and `workspaces`.
