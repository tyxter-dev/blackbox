# 15 — Roadmap, Milestones & Open Questions

Forward-looking companion to the feature docs. `FEATURES.md` is the single source
of "what works today"; `ROADMAP.md` is the canonical forward plan; this file
summarizes both in PRD terms.

## Strategic thesis

The project spans three layers of unequal value (priority flows downhill):

1. **Model adapters** (OpenAI, Anthropic, Gemini, xAI) — a commodity with a
   permanent maintenance treadmill. Keep healthy; don't chase breadth. → [03](03-model-providers.md)
2. **Agent sessions** (`AgentProvider`) — moderately differentiated; the
   normalized event/session/artifact contract over heterogeneous backends is
   genuinely useful. → [04](04-agent-providers.md)
3. **Workspace agent packages** (`WorkspaceAgentSpec`) — the differentiated layer
   and the reason the project exists. Nobody owns this contract yet. → [08](08-workspace-agents.md)

Invest in layer 3, finish layer 2, maintain layer 1. The zero-dependency core and
provider-native event/state design are settled.

## Original milestones (PRD §22)

| Milestone | Theme | Status |
|---|---|---|
| M0 | Local loop works | complete |
| M1 | OpenAI Responses-native model provider | complete |
| M2 | Policy, approvals, workspace | started (approval-channel at workspace checkpoints pending) |
| M3 | MCP | started (transports + trust shipped; pending-call store pending) |
| M4 | Cloud / coding-agent providers | OpenAI + Claude Code shipped; Vertex stub |
| M5 | Observability & persistence adapters | started → largely shipped |
| M6 | Lifecycle (evals + deployments) | partial |
| M7 | Hardening & release | in progress |

(Milestone "still pending" lists in the PRD have drifted; treat `FEATURES.md` as
authoritative and the horizons below as the live plan.)

## Live horizons (from ROADMAP.md)

- **Horizon 0 — Re-entry.** Refresh SDK pins and bundled model/pricing catalogs;
  run live suites and triage drift; reconcile status docs; sweep upstream API
  news. Verification, not features.
- **Horizon 1 — Close started milestones.** Vertex AI Agent Engine provider
  (implement or descope the stub); cloud agent webhook ingress (one real
  verifying impl); MCP pending-call store; approval-channel integration at
  workspace checkpoints; namespaced `ToolRef` IDs in the high-level API; provider
  cache lifecycle breadth; naming-collision fixes (`ToolBudget` →
  `ToolRoutingBudget`).
- **Horizon 2 — Workspace agent packages v1.** Run-time permission enforcement;
  connector auth contract; versioning/upgrade story; portable skill packs
  (`SkillSpec`). → [08](08-workspace-agents.md)
- **Horizon 2½ — Environment workers.** The inbound hemisphere. → [14](14-environment-workers.md)
- **Horizon 3 — Release & ecosystem.** Publish to PyPI under a chosen
  distribution name (the `blackbox` PyPI name is taken — import name can remain
  `blackbox`); versioning policy; catalog refresh automation; docs site + CLI;
  realtime hardening decision.

## Risks & mitigations (PRD §25)

| Risk | Mitigation |
|---|---|
| Provider cloud-agent APIs are unstable | Keep raw provider state, isolate adapters, contract-test normalized events. |
| Over-abstraction hides provider strengths | Preserve raw payloads + capability flags; expose provider-specific extras. |
| Scope grows too wide | OpenAI Responses + local agent first; delay provider breadth. |
| Runtime becomes only an abstraction kit | Keep `runtime.run(..., tools=..., output_type=...)` first-class and tested as a product contract. |
| Sessions hard to persist | Design `ProviderState`, `SessionRef`, event log early (done). |
| Inconsistent approvals | Centralize approval event + decision types. |
| Opaque cloud artifacts | Normalize artifact metadata; keep provider-native references. |

## Open questions

**Settled** (PRD §26 decision log in `ROADMAP.md`):

| # | Question | Decision |
|---|---|---|
| 1 | Package name | `blackbox` import name; confirm PyPI distribution name before release (taken). |
| 2 | First real `AgentProvider` | OpenAI Agents SDK, then Claude Code; Vertex open. |
| 3 | Workspaces in core? | Yes — shipped. |
| 4 | Dataclasses vs Pydantic | Dataclasses in core; Pydantic optional (`validate` extra). |
| 5 | Persistence interfaces now? | Yes — protocols + JSONL/SQLite shipped. |
| 9 | Structured-output validators | Pydantic, dataclasses, raw JSON Schema. |
| 10 | Retry on validation failure | Fail-fast default; opt-in `posthoc_parse_with_retry`. |

**Still open:** 6 (sink sync/async stance — de facto async, confirm and close),
7 (how much provider API through `extra` vs adapter methods — revisit per
adapter), 8 (a `blackbox` CLI for running sessions / dumping events — Horizon 3).

## Deliberate non-goals (restated)

- No chat-message internal model; chat stays a compatibility projection.
- No admin UI, org RBAC, OAuth flows, or secret storage in core.
- No LiteLLM-style lowest-common-denominator normalization.
- No provider breadth for its own sake; new adapters need a consuming use case.

## Quality gates (release discipline)

All three gates must pass before committing: `pytest -q`, `ruff check src tests
examples`, `mypy src` (strict). Focused structural gate: `pytest -q tests/unit
tests/runtime tests/contracts tests/golden` (baseline **479 passing + 1
skipped**). Broader `tests/integration/*` and `tests/journey/*` are
network-gated.

← Back to [README](README.md)
