# 06 — Feature: MCP

**Package:** `src/blackbox/mcp/` · **Routing layer:** `MCPToolset` ·
`runtime.run(..., toolsets=[...])`

## Summary

MCP (Model Context Protocol) is a first-class connector family, not a
provider-specific hack. Two execution paths are supported and never merged:

1. **Local MCP dispatch** — the runtime connects to MCP servers and executes
   tools locally (`MCPConnector`).
2. **Provider-native remote MCP** — the provider receives an MCP server
   configuration and handles listing/calling tools remotely (`RemoteMCP`).

`MCPToolset` is the routing convenience layer that chooses local dispatch or
provider-native remote MCP based on the resolved trust policy — without merging
the two paths.

## Transports & auth

**Transports:** stdio, HTTP/SSE, and streamable HTTP (per current MCP spec).

**Auth:** a token-provider interface for credential acquisition, OAuth and
resource-indicator support for HTTP MCP servers, per-server credential scoping,
**no token leakage into logs or persisted events**, and explicit auth-challenge
events on the runtime stream.

## Tool identity

- Every MCP tool is namespaced by its server: `mcp:<server>.<tool>`.
- Stable `ToolRef` IDs.
- Per-server trust policy and allowlist/denylist support.

## Trust & security guardrails (security boundary)

MCP integration carries explicit, enforced security boundaries. **Trust is
enforced before tool exposure** — do not bypass `MCPTrustEvaluator`.

- **`MCPTrustLevel`:** `BLOCKED` / `UNTRUSTED` / `REVIEWED` / `TRUSTED` /
  `FIRST_PARTY`.
- **`MCPCapabilityRisk`:** eleven flags (`RUNS_COMMANDS`, `NETWORK_EGRESS`,
  `SECRET_ACCESS`, `SERVER_INITIATED_SAMPLING`, …).
- **`MCPServerTrustPolicy` / `MCPToolTrustPolicy`:** gate which servers/tools may
  run, route mode (local vs `RemoteMCP`), approval requirement, output redaction.
- **`MCPServerRiskProfile`:** computed at connection time by
  `build_server_risk_profile()` — read-only summary the evaluator consults.
- **`DefaultMCPTrustEvaluator`:** conservative default — blocks `BLOCKED`,
  requires approval for untrusted servers.
- **`MCPTaint`:** outputs honor taint redaction.

Adding a new MCP risk capability or trust level is part of the security-boundary
contract — **ask first**.

## Runtime control

`list_tools` result caching with explicit invalidation; max output size and
timeout per call; approval gating per tool or per server; raw + redacted event
preservation; per-provider capability negotiation.

## Requirements

| ID | Priority | Requirement |
|---|---|---|
| P2-R2 | P2 | Support local MCP dispatch and provider-native remote MCP connectors. |

(MCP is P2 in the original requirement table but the security guardrails are a
hard contract regardless of priority.)

## Hard constraints

- **MCP trust is enforced before tool exposure.** `BLOCKED` servers refuse to
  start; untrusted servers route through approval; outputs honor `MCPTaint`
  redaction. Don't bypass `MCPTrustEvaluator`.
- Local dispatch and provider-native remote MCP are **distinct execution paths**
  — `MCPToolset` routes; it does not merge them.
- No token leakage into logs or persisted events.

## Status & references

Local `MCPConnector` owns stdio/HTTP/SSE dispatch, lifecycle init, tool-discovery
cache invalidation, result normalization, redacted specs/auth, and namespaced
runtime-tool bridging. Provider-native remote MCP is represented by `RemoteMCP`.
Still pending: full browser/device OAuth and connector-level pending-approval
resume (the MCP pending-call store — Horizon 1); high-level runtime approval
pausing still applies before namespaced MCP tool execution. Tests:
`tests/unit/mcp/`. PRD §16; trust guardrails added in commit `e6a04c3`.

→ Next: [07 — Workspaces](07-workspaces.md)
