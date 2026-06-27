---
status: active
owner: blackbox-src
since: 2026-06-27
adr: docs/adr/README.md
prd: docs/prd/08-workspace-agents.md
---

# workspace_agents

`workspace_agents` owns portable package contracts for governed agents. It is a
schema and registry layer, not an execution backend.

## Belongs Here

- `WorkspaceAgentSpec` and supporting metadata/version/publication specs.
- Tool and connector permission declarations.
- Schedule declarations and scheduled-run references.
- Workspace-agent registry interfaces with in-memory and SQLite-backed
  implementations (every version kept; latest pointer for default reads).
- Spec validation (`validation.py`): typed issues for unresolvable refs,
  permission/connector mismatches, schedule sanity, and catalog-checked
  model availability; `ensure_valid_workspace_agent` gates pipelines.
- Serialization helpers for package specs.
- The on-disk package format (`package.py`): `agent.json` manifest +
  `instructions.md` + embedded `skills/<name>/` bundles, with
  save/load/pack/unpack/install helpers. Packages are diffable directories
  that zip into installable archives; unpacking is zip-slip guarded.
- Thin bridges that prepare or run a workspace-agent spec through the runtime.

## Does Not Belong Here

- Workspace filesystem, command, patch, or sandbox implementation.
- Provider-specific model or cloud-agent adapter logic.
- OAuth vaults, billing, tenancy, or UI concerns.

## Boundary Note

This package models what an agent package is allowed to use and how it is
published or scheduled. Actual execution is delegated to `runtime`, `agents`,
`tools`, `mcp`, and `workspaces`.
