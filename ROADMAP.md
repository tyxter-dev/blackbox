# Roadmap

Forward-looking counterpart to `FEATURES.md` (what works today) and to the
PRD §22 milestones (the original build plan). This document says what to do
next and why, organized by horizon. It assumes the project is resuming after
a pause (active development ran April–May 2026).

Vocabulary follows `docs/TAXONOMY.md`.

## Strategic thesis

The project spans three layers. They are not equally valuable:

1. **Model adapters** (OpenAI Responses, Anthropic Messages, Gemini, xAI) —
   a commodity with a permanent maintenance treadmill. Vendors and other
   libraries solve this too. Keep it healthy; do not invest in breadth here
   beyond what the upper layers need.
2. **Agent sessions** (`AgentProvider`: local, OpenAI Agents SDK, Claude
   Code, cloud engines) — moderately differentiated. The normalized
   event/session/artifact contract over heterogeneous agent backends is
   genuinely useful and few libraries do it provider-natively.
3. **Workspace agent packages** (`WorkspaceAgentSpec`: portable, governed,
   schedulable, permissioned agent definitions) — the differentiated layer
   and the reason this project exists: *infrastructure for building and
   distributing agents and agent workspaces*. Nobody owns this contract yet.

Priority flows downhill: invest in layer 3, finish layer 2, maintain layer 1.
The zero-dependency core and the provider-native event/state design are
settled and not up for re-litigation.

## Horizon 0 — Re-entry (first sessions back)

Goal: trust the codebase again before building on it. Everything here is
verification and refresh, not features.

- [ ] **Refresh SDK pins.** `openai>=2,<3`, `anthropic>=0.40,<1`,
      `google-genai>=1,<2`, `openai-agents`, `claude-agent-sdk`, `mcp` —
      check current majors, bump, and run the offline suite.
- [ ] **Refresh bundled catalogs.** The provider model catalog and pricing
      catalog are snapshots. Known-stale example: xAI Grok 4.1 fast models
      carry a May 15, 2026 retirement date that has already passed. Re-pull
      model lists and prices, bump `BUNDLED_*_CATALOG_VERSION`.
- [ ] **Run the live suites** (`pytest -m integration_openai` etc., then
      `pytest tests/journey`) per provider and triage drift: renamed stream
      event types, new hosted tools, changed usage fields.
- [ ] **Reconcile status docs.** PRD §22 milestone "still pending" lists have
      drifted behind reality (e.g. M2 lists git/sandbox/cloud workspace kinds
      as pending; `workspaces/git.py`, `docker.py`, `cloud.py` exist). Make
      `FEATURES.md` the single status source and trim milestone status from
      the PRD.
- [ ] **Sweep upstream API news** for Responses, Messages, GenerateContent,
      Agents SDK, Claude Agent SDK, and MCP spec revisions since May 2026,
      and file the deltas as issues before coding.

## Horizon 1 — Close the started milestones

Goal: no half-open workstreams. These map to PRD M2–M5 and the honest gaps in
`FEATURES.md`.

- [ ] **Vertex AI Agent Engine provider** — currently an honest stub
      (`FEATURES.md`); the last README "next target". Implement or
      explicitly descope it; a stub is the worst state.
- [ ] **Cloud agent webhook ingress** — contract-only today
      (`AgentWebhookProvider`, `runtime.agents.ingest_webhook`). Ship one
      real verifying implementation end-to-end to prove the contract.
- [ ] **MCP pending-call store** — connector-level store for direct MCP
      approval resume outside the high-level loop (the other README "next
      target").
- [ ] **Approval-channel integration at workspace checkpoints** (PRD M2
      pending) — wire `before_command` / `before_workspace_write` gates to
      the approval event/decision flow the way MCP approvals already are.
- [ ] **Namespaced `ToolRef` IDs in the high-level API** — `FEATURES.md`
      marks this "not supported yet"; the high-level API still references
      tools by bare name while MCP tools are already `mcp:server.tool`.
- [ ] **Provider cache lifecycle breadth** — native create/delete is
      Gemini-only; map or explicitly mark unsupported for the rest.
- [ ] **Naming-collision fixes from `docs/TAXONOMY.md`** — rename
      `tools/routing.ToolBudget` → `ToolRoutingBudget` (pre-release, no
      compat shim needed); audit other flagged collisions.
- [x] **Use-case validation backlog** — all eight examples and the Tier 0
      fake MCP servers from `docs/USE_CASE_VALIDATION.md` are shipped;
      building them exposed and fixed a dynamic-toolset dispatch bug. Rule
      adopted there: an MCP example that cannot run offline (Tier 0) does
      not merge.
- [x] **Inbound multimodal model input** — `ContentItem` entries in
      `runtime.run(input=[...])` now map to provider-native multimodal input
      in the OpenAI Responses (and xAI), Anthropic Messages, and Gemini
      adapters; unmappable parts raise `UnsupportedFeatureError`.

Adoption-driven gaps from the first downstream consumer (a production
multi-tenant WhatsApp agent platform migrating off `llm_factory_toolkit`):

- [x] **Cross-provider fallback routing** —
      `runtime.run(fallback_providers=[...])` tries provider refs in order on
      provider availability/execution errors, with explicit state-transfer
      semantics: candidates incompatible with a present `provider_state` are
      skipped, and attempts are reported under `result.metadata["fallback"]`.
- [x] **Cross-run dynamic tool surface persistence** — the final
      model-visible surface of a dynamic run is emitted as a
      `TOOL_SET_CHANGED` event and surfaced as
      `result.metadata["tool_choice"]["visible_tools"]`; passing it back as
      `tools=` restores loaded tools without rediscovery.
- [x] **Multi-tenant provider pattern** — documented in
      `docs/MULTITENANCY.md` (cached runtime-per-tenant factory over shared
      stores, with the alias-registration alternative and its footgun);
      runnable recipe in `examples/multi_tenant_runtimes.py`.

## Horizon 2 — The differentiated layer: workspace agent packages v1

Goal: make `WorkspaceAgentSpec` a real distribution format, not just a
dataclass. This is the product bet; each item should be driven by a concrete
consuming application — the first downstream consumer's packaged-agent
concept is structurally a `WorkspaceAgentSpec`.

- [x] **Package format on disk** — `workspace_agents/package.py`:
      `agent.json` manifest (format marker + serialized spec) +
      `instructions.md` (diffable prompt) + embedded `skills/<name>/`
      bundles, with `save/load/pack/unpack/install_workspace_agent_package`
      helpers. Local skill sources embed on save and resolve to absolute
      paths on load; non-local sources stay verbatim; unpack is zip-slip
      guarded; loading a newer `format_version` fails loudly. Still open
      (folds into the versioning item): integrity checksums and signing.
- [ ] **Validation/linting** — `prepare_agent_spec` exists; grow it into a
      real validator: unresolvable tool refs, permission/connector mismatches,
      schedule sanity, model availability against the provider model catalog.
- [ ] **Persistent registry** — `WorkspaceAgentRegistry` has only the
      in-memory implementation; add a SQLite-backed one consistent with the
      existing stores.
- [x] **Schedule execution bridge** — `ScheduleExecutor` runs due cron and
      interval schedules through `run_workspace_agent`, gated by the
      `before_scheduled_run` policy checkpoint, producing `ScheduledRunRef`s;
      drive it from external cron via `run_due(now=...)` or with the built-in
      `serve()` loop. `calendar` triggers remain downstream.
- [ ] **Permission enforcement at run time** — `ToolPermission` is metadata
      today; enforce scopes/connector bindings in the loop's policy gates so
      a package's grants actually constrain execution.
- [ ] **Connector auth contract** — define how `ConnectorSpec.auth_mode`
      resolves to credentials at run time without pulling OAuth/secret
      storage into core (callback/provider interface, applications implement).
- [ ] **Versioning and upgrade story** — semver rules for packages,
      compatibility checks on install, migration notes between spec versions.
- [ ] **Reference "agent workspace" example app** — a small downstream app
      that installs packages from a registry, schedules them, and surfaces
      approvals; proves the layer end-to-end and becomes the flagship demo.

## Horizon 2½ — The inbound half: environment workers

Goal: make blackbox the lab-neutral *worker* side of the connector, not only
the call-initiating orchestrator. Anthropic's Managed Agents self-hosted
sandboxes (June 2026) published the reference contract for this — a
customer-side daemon that claims tool-execution work from a lab control
plane, runs it locally, and posts results back. Full analysis, copy list,
and differentiators in `docs/ENVIRONMENT_WORKERS.md`. The strategic logic:
labs own control planes, lib consumers own customer distribution, blackbox
is the connector — and the inbound hemisphere is entirely missing today.

- [x] **`WorkSource` protocol** — `blackbox.workers.WorkSource`: lab-neutral
      claim/lease/complete/stop/stats contract with dead-worker reclaim
      (`reclaim_older_than_ms`) and a full in-memory reference
      implementation (`InMemoryWorkSource`) covering the offline suite.
- [x] **`AnthropicEnvironmentWorkSource` adapter** — wraps
      `client.beta.environments.work.*` (injected client, feature-detected,
      `ProviderNotConfiguredError` on drift). Built from the 2026-06-12 doc
      snapshot and tested against fakes only — **run it against the live
      beta API before first production use**.
- [x] **`EnvironmentWorker`** — always-on (`run`) and webhook-triggered
      (`drain`) entry points, lease keep-alive during handlers,
      control-plane stop cancellation, graceful `stop()` drain, injected
      `WorkHandler` (`anthropic_sdk_session_handler` ships as the SDK
      delegate). Still open: a packaged one-workspace-per-work-item sandbox
      spawn recipe over `SandboxWorkspaceProvider`.
- [x] **Customer-side governance on inbound work** — every claimed item is
      gated at the new `before_work_claim` checkpoint (deny/require_approval
      ⇒ skipped without execution). Per-tool-call gating flows through
      handlers built on `ToolRuntime` (existing `before_tool_call`/
      `before_command` gates); wrapping the Anthropic SDK toolset itself
      remains open pending its tool-object interface.
- [x] **Scoped worker credentials** — `WorkerCredentials` (environment id +
      key, key excluded from `repr`); docs state the org key never reaches
      the worker host.
- [x] **Worker ops surface** — `WorkSource.stats()` (depth / pending /
      oldest_queued_at / workers_polling) and `EnvironmentWorker.status()`
      (state, last poll, in-flight item, per-outcome counters incl. lost
      leases); `request_stop(force=...)` on the reference source.

## Horizon 3 — Release and ecosystem

Goal: other people can depend on this.

- [ ] **v0.1 to PyPI** — PRD §27 definition-of-done is substantially met and
      the wheel builds cleanly. Verified 2026-06-12: the `blackbox` name on
      PyPI is **taken** by an unrelated package — pick a distribution name
      (e.g. `blackbox-runtime`; the import name can remain `blackbox`),
      then publish. Until then, consumers install via git dependency.
- [ ] **Versioning policy** — declare what is stable (top-level exports) vs
      provisional (adapter modules) so the adapter treadmill doesn't force
      majors.
- [ ] **Catalog refresh automation** — script (or scheduled CI job) that
      regenerates bundled model/pricing catalogs from provider docs and opens
      a PR; staleness must be cheap to fix or it won't be fixed.
- [ ] **Docs site** — the package READMEs and `docs/` are strong; publish
      them; decide PRD open question 8 (a `blackbox` CLI for running
      sessions/dumping events) which doubles as a demo surface.
- [ ] **Realtime hardening** — decide whether realtime stays first-class or
      is demoted to "experimental" in the docs; it widens the treadmill and
      should earn its keep.

## Deliberate non-goals

Unchanged from the PRD, restated to resist scope creep on return:

- No chat-message internal model; chat stays a compatibility projection.
- No admin UI, org RBAC, OAuth flows, or secret storage in core — those
  belong to downstream applications consuming the package contracts.
- No LiteLLM-style lowest-common-denominator normalization; capability
  profiles + escape hatches instead.
- No pursuit of provider breadth for its own sake; new adapters must be
  justified by a consuming use case.

## Decision log (PRD §26 open questions, settled)

| # | Question | Decision |
|---|---|---|
| 1 | Package name | `blackbox` (repo dir `agent_runtime` is legacy-local). Confirm PyPI availability before release. |
| 2 | First real `AgentProvider` | OpenAI Agents SDK, then Claude Code. Vertex remains open (Horizon 1). |
| 3 | Workspaces in core? | Yes — shipped in core. |
| 4 | Dataclasses vs Pydantic for schemas | Dataclasses in core; Pydantic optional (`validate` extra). |
| 5 | Persistence interfaces immediately? | Yes — store protocols + JSONL/SQLite impls shipped. |
| 9 | Structured-output validators | Pydantic, dataclasses, raw JSON Schema all supported. |
| 10 | Retry on validation failure | Fail-fast default (`posthoc_parse`); opt-in `posthoc_parse_with_retry`. |

Still open: 6 (sink sync/async stance is de facto async — confirm and close),
7 (how much provider API through `extra` vs adapter methods — revisit per
adapter), 8 (CLI — Horizon 3).
