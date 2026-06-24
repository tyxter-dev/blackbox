# 05 — Feature: Tools

**Facade:** `runtime.tools` · **Packages:** `src/blackbox/tools/`,
`src/blackbox/tools/hosted/`

## Summary

Tools are how the loop acts on the world. Blackbox distinguishes six tool
backends and never collapses them: local Python tools, hosted provider tools,
remote MCP tools, cloud-agent tools, workspace tools, and platform lifecycle
tools. Local tools are one backend among many — important, but not the shape of
the whole architecture.

Three layers, increasing in scope:

- **`ToolRegistry` / `ToolDefinition`** (`tools/registry.py`) — flat callable
  registration with metadata (category, tags, risk, scopes, side effects,
  examples). The normal path: `runtime.tools.register(...)`.
- **`Toolset`** (`tools/toolsets.py`) — a named, composable group loaded as a
  unit. For large catalogs that don't all need to be visible every turn.
- **`ToolRoutingSpec`** (`tools/routing.py`) — runtime-side routing: modes
  `explicit` / `auto` / `hybrid` / `model_discovery` / `disabled`, with a
  budget capping max visible tools, schema tokens, and MCP/agent/handoff counts.

## Local tool registry

Supports: function registration, optional JSON schema, category/tags/group
metadata, blocking-tool offload, context injection, mock execution, usage
metadata, and local execution policies. The facade exposes `register`, `get`,
`all_tools`, `to_provider_tools`, `call(..., mock=…)`, and `session()`.

`runtime.tools.session()` returns a per-run isolated registry seeded from globals
— register run-scoped tools without leaking them into later runs.

Tools may be referenced by bare name (`tools=["create_ticket"]`) or by namespaced
refs (MCP, hosted, agent).

## Context injection (core differentiator)

Tool functions can request private runtime values by parameter name. These values
are **never** exposed to the model as tool-schema fields unless explicitly
configured.

```python
def create_ticket(title: str, user_id: str, db: Database) -> ToolResult:
    ...
```

The model supplies `title`; the runtime injects `user_id` and `db`.

## ToolResult

`ToolResult` splits four concerns so app-facing data never leaks into the model
context and vice versa:

- `content` — LLM-facing result text.
- `payload` — app-facing structured result (surfaced in `AgentResult.payloads`).
- `metadata` — diagnostics.
- `error` — optional error status.

## Hosted tools

Hosted-tool **specs** live in `tools/hosted/specs.py` and describe capabilities
executed on the *provider's* servers, not local callables: `WebSearch`,
`WebFetch`, `FileSearch`, `RemoteMCP`, `Memory`, `TextEditor`, `CodeInterpreter`,
`Shell`, `ApplyPatch`, `ImageGeneration`, `ComputerUse`, `URLContext`,
`ToolSearch`. The loop **bridges** hosted tools and remote MCP — it does not
dispatch them as local Python functions.

## Routing and dynamic discovery

`ToolRoutingSpec` decides which tools the model sees each turn. Dynamic discovery
(`auto`/`hybrid`/`model_discovery`) plus tool search supports large catalogs
without making meta-tools the only mechanism. The final model-visible surface is
emitted as `TOOL_SET_CHANGED` and surfaced as
`result.metadata["tool_choice"]["visible_tools"]` for cross-run reuse (see
[02](02-blackbox-loop.md)).

## Requirements

| ID | Priority | Requirement |
|---|---|---|
| P0-R7 | P0 | Local tools can be registered, called, context-injected, timed out, and run concurrently. |
| P0-R13 | P0 | `runtime.tools` exposes `register / get / all_tools / to_provider_tools / call(..., mock=…)`. |
| P2-R1 | P2 | Dynamic tool discovery via catalog/tool-search, without meta-tools being the only mechanism. |

## Hard constraints

- **Hosted/provider tools are not fake local tools** — the six backends stay
  distinct (PRD §14.4).
- Context-injected parameters are never surfaced to the model unless explicitly
  configured.
- New tool-dispatch semantics belong in `AgentLoop`, not per-adapter.
- Naming cleanup pending: `tools/routing.ToolBudget` → `ToolRoutingBudget`
  (pre-release, no compat shim).

## Status & references

Registry, context injection, toolsets, routing, hosted-tool specs, and run-scoped
sessions shipped. Namespaced `ToolRef` IDs in the high-level API are not yet
plumbed (MCP tools already are `mcp:server.tool`; bare-name refs for the rest —
`FEATURES.md`, Horizon 1). Tests: `tests/unit/tools/`. PRD §14.

→ Next: [06 — MCP](06-mcp.md)
