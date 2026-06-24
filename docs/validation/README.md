# Validation reports

Point-in-time validation reports that check a framework's behavior against its
contract and against real provider/agent workflows. Unlike the framework PRDs
(`docs/MODEL.md`, `docs/AGENT.md`, `docs/WORKSPACE.md`), these are **reports**, not
contracts — they capture what was true when run and are refreshed as the surface
evolves.

| Report | Covers |
|---|---|
| [`MODEL_PROVIDER_WORKFLOW_VALIDATION.md`](MODEL_PROVIDER_WORKFLOW_VALIDATION.md) | `ModelProvider` adapters: workflow coverage, capability honesty, event mapping. |
| [`AGENT_PROVIDER_WORKFLOW_VALIDATION.md`](AGENT_PROVIDER_WORKFLOW_VALIDATION.md) | `AgentProvider` sessions: lifecycle, artifacts, approvals across backends. |

Related but **not** here:
- `tests/VALIDATION.md` — the live coverage tracker mapping behaviors → tests.
- [`docs/USE_CASE_VALIDATION.md`](../USE_CASE_VALIDATION.md) — demand-side
  validation against production agent patterns (a living design justification,
  not a frozen report, so it stays in `docs/`).
