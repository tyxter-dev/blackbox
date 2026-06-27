---
status: active
owner: blackbox-src
since: 2026-06-27
adr: docs/adr/README.md
prd: docs/prd/14-environment-workers.md
---

# `blackbox.workers` — environment workers

The inbound half of the connector. AI-lab control planes (Anthropic Managed
Agents today; others as they ship equivalents) enqueue agent sessions as
**work items** in a per-environment queue; this package is the customer-side
daemon that claims those items, executes them inside the customer boundary
under customer-owned policy, and posts results back.

Design analysis and the upstream contract snapshot live in
`docs/ENVIRONMENT_WORKERS.md`. Vocabulary follows `docs/TAXONOMY.md`.

## Layers

Mirroring the platform SDK's layering, lowest first:

| Layer | What it gives you |
|---|---|
| `WorkSource` (protocol) | Lab-neutral claim/lease/complete/stop/stats contract. Implement it to point the worker at any control plane. |
| `InMemoryWorkSource` | Reference implementation: FIFO claim, heartbeat leases, dead-worker reclaim (`reclaim_older_than_ms`), ops-side `enqueue`/`request_stop`, stats. Backs the offline tests and is the seam for a customer-owned control plane. |
| `EnvironmentWorker` | The daemon: claims, policy-gates, executes via an injected `WorkHandler`, keeps the lease alive, posts the terminal `WorkResult`. |
| `AnthropicEnvironmentWorkSource` | First lab adapter, over `client.beta.environments.work` (beta `managed-agents-2026-04-01`). Beta-tracking: re-verify before depending on it. |

## Always-on worker

```python
import anthropic
from blackbox import (
    AnthropicEnvironmentWorkSource,
    EnvironmentWorker,
    WorkerCredentials,
    anthropic_sdk_session_handler,
)

creds = WorkerCredentials(environment_id="env_...", environment_key="sk-ant-oat01-...")
client = anthropic.AsyncAnthropic(auth_token=creds.environment_key)

worker = EnvironmentWorker(
    source=AnthropicEnvironmentWorkSource(client, creds),
    handler=anthropic_sdk_session_handler(client, creds, workdir="/workspace"),
    policy=my_policy,  # optional: gate inbound work at before_work_claim
)
# Wire SIGTERM to worker.stop() in your entrypoint; in-flight work drains first.
await worker.run()
```

## Webhook-triggered worker

Wake on the lab's session-started webhook and drain the queue instead of
running an idle poller:

```python
async def on_session_started(event) -> None:
    await worker.drain()
```

## Governance

- **`before_work_claim`** — the worker consults its `Policy` for every
  claimed item (action = session id; arguments carry work id, environment,
  and metadata). `deny` and `require_approval` complete the item as
  `skipped` without executing it; there is no approval channel at the worker
  boundary yet, so `require_approval` is conservative.
- **Per-tool-call gating** belongs inside the handler. Handlers built on the
  blackbox `ToolRuntime` get `before_tool_call` / `before_command`
  checkpoints for free; the SDK-delegating handler accepts a `tools` factory
  as the seam for wrapping the standard toolset.

## Credentials

`WorkerCredentials` is the scoped pair (environment id + environment key)
that authenticates a worker to its queue. The key is excluded from `repr`.
The organization/provider API key is ops-side only — never set it on the
worker host, where agent tool calls could read it.

## Ops

- `await source.stats()` → `WorkQueueStats(depth, pending, oldest_queued_at,
  workers_polling)` — scale on `depth`, alert on `workers_polling`.
- `worker.status()` → `WorkerStatus` (state, last poll, in-flight item,
  per-outcome counters including `lost` for reclaimed leases).
- Control-plane stop: when `stop_requested` turns true mid-flight, the
  worker cancels the handler and posts `stopped`. Worker-side graceful stop
  is `worker.stop()`.

## What is deliberately not here

- No OAuth/secret storage (core non-goal): the environment key is a
  constructor argument like every other credential.
- No multi-node coordination: run one worker per process; the queue's lease
  + reclaim semantics make worker fleets safe without it.
- The Anthropic adapter's `stop_requested` always reports `False` and
  `heartbeat` is a no-op — the platform manages both inside its own helpers;
  see the module docstring for the mapping rationale.
