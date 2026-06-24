# ADR-0009 — MCP trust & risk as an enforced security boundary

- **Status:** Accepted
- **Recorded:** 2026-06-24 (decision made during 2026-04/05 design; guardrails in commit `e6a04c3`)
- **Sources:** `AGENTS.md` → MCP guardrails / Hard constraints; PRD §16

## Context

MCP servers can run commands, egress the network, access secrets, and initiate
sampling. Exposing their tools to the model without gating is unsafe, and trust
cannot be an afterthought applied after a tool has already run.

## Decision

MCP trust is enforced **before tool exposure**. Each server carries an
`MCPServerTrustPolicy` and a computed `MCPServerRiskProfile`
(`build_server_risk_profile()`), which the `MCPTrustEvaluator` consults:

- `MCPTrustLevel`: `BLOCKED` / `UNTRUSTED` / `REVIEWED` / `TRUSTED` /
  `FIRST_PARTY`.
- `MCPCapabilityRisk`: eleven flags (`RUNS_COMMANDS`, `NETWORK_EGRESS`,
  `SECRET_ACCESS`, `SERVER_INITIATED_SAMPLING`, …).
- `BLOCKED` servers refuse to start; untrusted servers route through approval;
  outputs honor `MCPTaint` redaction.

The `DefaultMCPTrustEvaluator` is conservative (blocks `BLOCKED`, requires
approval for untrusted). Don't bypass `MCPTrustEvaluator`. **Adding a new MCP risk
capability or trust level is a change to this security-boundary contract** and
must be reviewed as such.

## Consequences

- Safe-by-default MCP integration; auditable trust decisions on the event stream
  (`mcp.trust.evaluated`).
- More configuration than "just connect a server."
- Conservative defaults may block or gate servers until explicitly reviewed.

## Alternatives considered

- **Trust-all MCP** (connect and expose) — rejected: unacceptable for servers
  that run commands or touch secrets.
