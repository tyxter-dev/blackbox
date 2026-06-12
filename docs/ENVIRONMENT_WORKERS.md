# Environment Workers — the inbound half of the connector thesis

Research note, 2026-06-12. Snapshot analysis of Anthropic's **Managed Agents
self-hosted sandboxes** feature (beta header `managed-agents-2026-04-01`) and
what blackbox should copy from, and improve on, when the project reactivates.

Source: <https://platform.claude.com/docs/en/managed-agents/self-hosted-sandboxes>
(plus the environments work API at `/docs/en/api/beta/environments/work`).
APIs described here are beta and will drift — re-verify against the live docs
before implementing.

Vocabulary follows `docs/TAXONOMY.md`. Forward-looking items land in
`ROADMAP.md` (Horizon 2½).

## Strategic framing

The market is splitting along a line that validates this project's thesis:

- **AI labs own the control plane.** Anthropic Managed Agents runs Claude,
  orchestration, sessions, and skills on their side. Expect OpenAI and Google
  equivalents.
- **Lib consumers own customer distribution.** Downstream products (Tyxter
  Studio and its peers) package agents for end customers and run them inside
  customer boundaries.
- **Blackbox is the connector infra between the two.**

Anthropic's self-hosted sandbox doc is the first published reference shape
for the customer-side half of that connector: an **environment worker** — a
daemon in customer infrastructure that receives tool-execution work from the
lab's control plane and runs it locally. Their partner list (Blaxel,
Cloudflare, Daytona, E2B, GKE Agent Sandbox, Modal, Namespace, Superserve,
Vercel) is nine companies each shipping a single-vendor integration guide for
exactly this connector. Nobody offers the neutral, multi-lab,
customer-governed version. That gap is the opportunity.

## Anthropic's model, condensed

Direction is inverted from blackbox's current design: the lab runs the model
and orchestration; the customer runs tool execution. Tool inputs/outputs
still transit the lab's control plane (the model must see results).

1. A `self_hosted` **environment** acts as a **work queue**. Sessions
   assigned to it become work items.
2. A customer-side **environment worker** polls the queue, **claims** a work
   item (leased; `reclaim_older_than_ms` lets another worker re-claim items
   from a dead worker), downloads the agent's **skills** to
   `<workdir>/skills/<name>/` (marking files executable), executes the tool
   calls locally, posts results back, and stops the work item. Keep-alive on
   the lease is handled inside the worker helpers.
3. Two worker shapes:
   - **Always-on**: long-running poller; needs only outbound HTTPS.
   - **Webhook-triggered**: a `session.status_run_started` webhook wakes a
     handler that drains the queue (`drain=True`, `block_ms=None`,
     `auto_stop=False`) and exits. Signature verification via a webhook
     signing key.
4. **Layered SDK helpers** (Python/TS/Go), each a deliberate step down in
   abstraction:
   - `EnvironmentWorker` — batteries-included: poll, setup, execute, post,
     graceful SIGTERM drain. `.run()` forever or `.handle_item()` once.
   - `work.poller()` — claims work and yields it; caller decides what to do
     (e.g. spawn a sandbox per session). Knobs: `drain`, `block_ms` (1–999 ms
     long-poll, `None` = non-blocking), `reclaim_older_than_ms`, `auto_stop`.
   - `sessions.events.tool_runner()` — execution layer only, given an
     already-claimed session and a tool list.
   - `AgentToolContext` + `beta_agent_toolset_20260401(env)` — execution
     context (workdir, path policy, skills download) and a **date-versioned**
     factory returning the standard tools (`bash`, `read`, `write`, `edit`,
     `glob`, `grep`).
5. **Per-session sandbox spawn**: the poller invokes a spawn script with
   `ANTHROPIC_SESSION_ID`, `ANTHROPIC_WORK_ID`, `ANTHROPIC_ENVIRONMENT_ID`,
   `ANTHROPIC_ENVIRONMENT_KEY` in the environment; the container handles one
   session and exits. Deliverables exit through a host directory bind-mounted
   at `/mnt/session/outputs`. `/workspace` is the canonical workdir.
6. **Credential split**: a low-privilege **environment key**
   (`sk-ant-oat01-...`, Console-generated) authenticates the worker to its
   queue; the **org API key** is ops-side only. The doc explicitly warns that
   setting the org key on the worker host exposes it to agent tool calls. On
   AWS the worker authenticates with IAM SigV4 instead — auth is
   deployment-specific.
7. **File staging**: the lab never mounts files into self-hosted sandboxes.
   Sessions carry references (S3 path, commit SHA) in `metadata`; the
   spawn/`--on-work` handler stages files into the workdir before execution.
8. **Ops surface** (org-key, off-host):
   - `GET environments/{id}/work/stats` → `depth`, `pending`,
     `oldest_queued_at`, `workers_polling` (workers seen in last 30 s — the
     liveness signal).
   - `POST environments/{id}/work/{work_id}/stop` (graceful: finish in-flight
     tool call, post final status, release; `force: true` interrupts).
   - If no worker is connected, sessions queue rather than fail.
9. Fine print: memory not supported on self-hosted; Linux host with
   `/bin/bash` at that exact path required; deps resolved at fixed paths
   ignoring `PATH`; MCP tunnels are a separate, orthogonal product (execution
   location vs tool reachability).

## The structural finding

Blackbox today is entirely **call-initiator**: `AgentRuntime` orchestrates
and calls out to providers. The environment-worker model is the inverse — a
daemon that *receives* work from a remote control plane. Nothing in the repo
claims work, holds a lease, heartbeats, or registers a worker
(`AgentWebhookProvider` is the closest concept and is contract-only). For the
connector thesis, this inbound half is the missing hemisphere — and it is the
cheapest part to build, because the lab's doc fully specifies the contract
while blackbox already owns the hard interior (tool dispatch, workspaces,
policy, redaction, persistence).

## Copy list

1. **The claim/lease work-queue contract.** Enqueue → poll → claim with
   lease → keep-alive → post results → stop, plus dead-worker reclaim and the
   `drain`/`block_ms`/`auto_stop` knobs. Adopt this vocabulary verbatim in a
   `WorkSource` protocol — it is small and well-designed.
2. **The three-layer helper design.** Worker (batteries-included) → poller
   (claim, you decide) → tool-runner (execution only). `ToolRuntime`/
   `Toolset` already is the bottom layer; build the two above it.
3. **Per-session sandbox spawn via env-var handoff** with a bind-mounted
   outputs directory. `SandboxWorkspaceProvider`/`DockerWorkspaceProvider`
   can grow a "one workspace per claimed work item" mode nearly for free.
4. **Scoped credentials.** A worker-scoped key distinct from the org key,
   with the worker host never holding the privileged credential. Blackbox
   has no down-leveled-credential concept; it also strengthens the
   multi-tenant story (one environment key per tenant).
5. **Liveness and queue ops.** `workers_polling`-style liveness, queue
   `depth`/`oldest_queued_at` for scaling and alerting, graceful stop with
   drain-in-flight on SIGTERM. Fills the known no-heartbeat gap.
6. **File staging via session metadata** (references in, worker stages).
   Natural fit for workspace-open hooks; `WorkspaceAgentSpec` is the obvious
   thing to stage.
7. **Date-versioned toolset contracts** (`beta_agent_toolset_20260401`):
   tool implementations versioned to match what the model was prompted with.
   A connector compatible with multiple lab control planes over time needs
   this.

## Where blackbox improves on it (the differentiators)

Anthropic's worker is deliberately minimal and **Anthropic-only**. The
connector version:

- **Provider-agnostic worker.** One daemon, a `WorkSource` protocol, adapters
  per lab (Anthropic environments work API first; OpenAI/Google equivalents
  when they exist; a customer's own control plane as another adapter). None
  of the nine listed sandbox partners offers cross-lab.
- **Customer-side governance.** Their worker executes whatever arrives;
  isolation is the only control, and compliance is hand-waved to the
  customer. Blackbox already has the policy checkpoint vocabulary
  (`before_command`, `before_tool_call`, `before_workspace_write`), approval
  flows, MCP trust levels, and `RedactingEventSink`. Gating *the lab's* tool
  calls through customer-owned policy before execution is the enterprise
  feature their design omits. This is the moat.
- **Multi-tenant fan-out.** Their model is one environment = one queue = one
  worker fleet. The runtime-per-tenant pattern (`docs/MULTITENANCY.md`) lets
  one worker host serve many environments/tenants with isolated credentials
  and policy.
- **Unified tool reachability.** They keep MCP tunnels as a separate product;
  `MCPConnector` (transports + trust + auth) can live inside the same worker.

## Proposed shape (for reactivation, not now)

A `blackbox.workers` surface:

- `WorkSource` protocol — claim/lease/stop semantics copied from the
  environments work contract, lab-neutral.
- `AnthropicEnvironmentWorkSource` — first adapter, wrapping
  `client.beta.environments.work.*` from the Anthropic SDK.
- `EnvironmentWorker` — wires claimed work into the existing `ToolRuntime` +
  `WorkspaceProvider` + policy gates; always-on and webhook-triggered entry
  points; graceful drain; per-work-item workspace option.
- Ops facade — queue stats, liveness, graceful/forced stop.

Their queue contract on the outside, blackbox governance and workspace
machinery on the inside. Consistent with the "no OAuth/secret storage in
core" non-goal: the environment key is a constructor argument like every
other credential.
