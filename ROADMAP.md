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
- [ ] **Use-case validation backlog** — execute the prioritized example
      backlog and the Tier 0 fake-MCP-server strategy from
      `docs/USE_CASE_VALIDATION.md`, which grounds example coverage in a
      610-agent production dataset. Top gaps: dynamic toolsets, human
      escalation/approvals, conversation resume, media parts. Rule adopted
      there: an MCP example that cannot run offline (Tier 0) does not merge.

## Horizon 2 — The differentiated layer: workspace agent packages v1

Goal: make `WorkspaceAgentSpec` a real distribution format, not just a
dataclass. This is the product bet; each item should be driven by a concrete
consuming application (Tyxter products are the natural first consumers).

- [ ] **Package format on disk** — a serialized layout (manifest + skills +
      prompts) that can be checked into a repo, zipped, published, and
      installed; today the spec only round-trips through dicts.
- [ ] **Validation/linting** — `prepare_agent_spec` exists; grow it into a
      real validator: unresolvable tool refs, permission/connector mismatches,
      schedule sanity, model availability against the provider model catalog.
- [ ] **Persistent registry** — `WorkspaceAgentRegistry` has only the
      in-memory implementation; add a SQLite-backed one consistent with the
      existing stores.
- [ ] **Schedule execution bridge** — `ScheduleSpec` is declarative only;
      provide a reference executor (or a documented bridge to external cron)
      that produces `ScheduledRunRef`s and runs packages.
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

## Horizon 3 — Release and ecosystem

Goal: other people can depend on this.

- [ ] **v0.1 to PyPI** — PRD §27 definition-of-done is substantially met;
      verify the checklist, confirm the `blackbox` name is available (else
      pick the public name now), build, publish.
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
