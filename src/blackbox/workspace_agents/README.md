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

## Runtime permission contract

`WorkspaceAgentSpec.permission_mode` is `inherit` by default, including hydrated
old manifests. `allowlist_v1` compiles permissions into immutable snapshots at
`run_workspace_agent` entry. Empty grants deny tools. Nested package runs retain
outer restrictions; caller kwargs, config, and custom allow policies cannot
replace the boundary. `prepare_agent_spec` and `WorkspaceAgentSpec.to_agent_spec()` reject restricted
packages; use the
execution bridge so enforcement remains attached.

Refs are exact: bare local names and `local:<name>` are equivalent;
`mcp:<server>.<tool>`, `workspace:<operation>`, and `hosted:<kind>` identify other
backends. Hosted `bash`/`computer_use` normalize to `shell`/`computer`. No wildcard
matching is performed. Local required scopes come from `ToolDefinition.scopes`,
with `execute` for unannotated tools. `admin` covers operation scopes but does
not waive connector identity or connector scopes. Connector identity and OAuth
requirements come from tool metadata `connector` and `connector_scopes`; each
declared connector must list the granted ref in `tool_refs` and contain the
required scopes. Permission metadata cannot override these security fields.

Workspace registrations preserve canonical operation refs across custom tool-name
prefixes and declare read, write, delete, or execute scopes; patch
requires both write and delete because patches can delete files. MCP operation
scopes use `permission_scopes` when annotated, otherwise destructive/read-only
annotations determine delete/read, with execute as the fallback. MCP OAuth
requirements remain separate from operation scopes and MCP trust gates remain
active. Grant approvals are composed with runtime policy and bind to the checked
callable and metadata; replacement after approval must pass fresh authorization.

Static and dynamic exposure, search/load results, and actual local dispatch are
checked. Internal search/load tools remain available to navigate the filtered
catalog; structured-output finalizers remain available to finish a run.
`LocalAgentProvider` uses the same loop, snapshots per-session constraints, and
restores the caller context between yielded events and on close/cancellation.

Managed agent providers advertise `supports_package_permissions=False` and fail
before workspace staging or agent/session creation. Local is the implemented
agent-session backend; packages referring to an existing `agent_id` are rejected
in allowlist mode. WebSearch is representable as a read grant with no connector
or per-call approval. Shell(execution="local"), ApplyPatch, ComputerUse,
TextEditor, and Memory can dispatch through runtime handlers with execute grants.
Client-hosted handlers are configured on model runs; local package sessions reject
client-hosted specs, MCP toolset materialization, and workspace attachment because
those setup surfaces are not implemented by LocalAgentProvider.
Their existing handler path returns denied continuation when approval needs an
unavailable channel. Other provider-executed hosted tools, raw hosted payloads,
provider-native ToolSearch/RemoteMCP and extra parameters are rejected explicitly.
Enabled `ToolSearchControl` is also rejected before adapter dispatch, including
config-supplied controls; disabled controls remain valid.
Tool implementations and credential binding are application-owned; the grant
boundary is not a sandbox for arbitrary Python callable internals.
