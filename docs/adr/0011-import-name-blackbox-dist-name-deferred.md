# ADR-0011 — Import name `blackbox`; PyPI distribution name deferred

- **Status:** Accepted
- **Recorded:** 2026-06-24 (PyPI availability verified 2026-06-12)
- **Sources:** `ROADMAP.md` Horizon 3 + decision log #1; PRD §26 Q1

## Context

The package needs a name for both the import (`import blackbox`) and the PyPI
distribution. The `blackbox` name on PyPI is **taken** by an unrelated package
(verified 2026-06-12), so it cannot be the distribution name as-is.

## Decision

Keep the import name `blackbox` (the in-repo development directory `agent_runtime`
is legacy-local). Choose a distinct **distribution** name (e.g.
`blackbox-runtime`) before publishing; the import name can remain `blackbox`.
Until a distribution name is chosen and published, consumers install via a git
dependency.

## Consequences

- Import ergonomics stay unchanged (`import blackbox`).
- Distribution name will differ from the import name — a known, accepted
  papercut.
- v0.1 release to PyPI is blocked on choosing the distribution name (tracked in
  `ROADMAP.md` Horizon 3).

## Alternatives considered

- **Rename the import to a PyPI-free name** — rejected: needless churn across
  code, docs, and examples for a cosmetic gain.
- **Publish under `blackbox`** — not possible: name is taken.
