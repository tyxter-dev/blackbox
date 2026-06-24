# 14 — Feature: Environment Workers (the inbound half)

**Package:** `src/blackbox/workers/` · **Protocol:** `WorkSource` ·
**Worker:** `EnvironmentWorker`

## Summary

Everything else in this PRD describes blackbox as the *call-initiating
orchestrator* — it reaches out to models and agents. Environment workers are the
**inbound hemisphere**: blackbox as the lab-neutral *worker* that claims
tool-execution work from a lab control plane, runs it locally, and posts results
back. This makes blackbox the connector between labs (who own control planes) and
lib consumers (who own customer distribution).

The reference contract is Anthropic's Managed Agents self-hosted sandboxes
(June 2026): a customer-side daemon that claims work, runs it in a local sandbox,
and returns results.

## Why it exists

Labs own control planes; library consumers own customer distribution; blackbox is
the connector. The inbound hemisphere was entirely missing — without it, blackbox
could only *initiate* agent work, never *serve* it. Full analysis, copy list, and
differentiators in `docs/ENVIRONMENT_WORKERS.md`.

## Contracts

- **`WorkSource` protocol** — `blackbox.workers.WorkSource`: lab-neutral
  claim / lease / complete / stop / stats contract with dead-worker reclaim
  (`reclaim_older_than_ms`). Reference impl: `InMemoryWorkSource` (covers the
  offline suite).
- **`AnthropicEnvironmentWorkSource` adapter** — wraps
  `client.beta.environments.work.*` (injected client, feature-detected,
  `ProviderNotConfiguredError` on drift). Built from the 2026-06-12 doc snapshot,
  tested against fakes only — **run against the live beta API before first
  production use**.
- **`EnvironmentWorker`** — always-on (`run`) and webhook-triggered (`drain`)
  entry points; lease keep-alive during handlers; control-plane stop
  cancellation; graceful `stop()` drain; injected `WorkHandler`
  (`anthropic_sdk_session_handler` ships as the SDK delegate).

## Governance on inbound work

Inbound work is governed the same way outbound work is:

- Every claimed item is gated at the **`before_work_claim`** policy checkpoint
  (deny / require_approval ⇒ skipped without execution). See [09](09-approvals-and-policy.md).
- Per-tool-call gating flows through handlers built on `ToolRuntime` (existing
  `before_tool_call` / `before_command` gates).
- **Scoped worker credentials** — `WorkerCredentials` (environment id + key, key
  excluded from `repr`). The org key never reaches the worker host.

## Worker ops surface

`WorkSource.stats()` — depth / pending / oldest_queued_at / workers_polling.
`EnvironmentWorker.status()` — state, last poll, in-flight item, per-outcome
counters (including lost leases). `request_stop(force=...)` on the reference
source.

## Open work

- Packaged one-workspace-per-work-item sandbox spawn recipe over
  `SandboxWorkspaceProvider`.
- Wrapping the Anthropic SDK toolset itself for per-tool-call gating (pending its
  tool-object interface).
- Live-API validation of `AnthropicEnvironmentWorkSource`.

## Hard constraints

- **Customer-side governance applies to inbound work** — the `before_work_claim`
  checkpoint is the entry gate; the org key never reaches the worker host.
- The `WorkSource` contract is **lab-neutral** — adapters wrap specific control
  planes; the protocol does not bake one in.

## Status & references

`WorkSource`, `InMemoryWorkSource`, `AnthropicEnvironmentWorkSource`,
`EnvironmentWorker`, `WorkerCredentials`, governance gate, and ops surface
shipped (offline-tested). Docs: `docs/ENVIRONMENT_WORKERS.md`. Tests:
`tests/unit/` workers coverage. `ROADMAP.md` Horizon 2½.

→ Next: [15 — Roadmap & open questions](15-roadmap-and-open-questions.md)
