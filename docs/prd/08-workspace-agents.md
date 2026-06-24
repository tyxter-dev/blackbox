# 08 — Feature: Workspace Agent Packages (the differentiated bet)

**Package:** `src/blackbox/workspace_agents/` · **Core type:** `WorkspaceAgentSpec`

## Summary

Workspace agent packages are **portable, governed, schedulable, permissioned
agent definitions**. This is the differentiated layer and the reason the project
exists: *infrastructure for building and distributing agents and agent
workspaces*. Nobody owns this contract yet.

A `WorkspaceAgentSpec` bundles the model, prompt, tools, connectors, schedules,
permissions, skills, and publication metadata for an agent into a single
core-modeled contract — **without** forcing a downstream UI, scheduler, OAuth
provider, or database. Downstream products publish, schedule, permission, and
audit agents; execution still flows through model, agent, tool, MCP, and
workspace primitives. Packages are not a third execution loop.

## Why it exists

Priority flows downhill (`ROADMAP.md` strategic thesis): invest in this layer 3,
finish agent sessions (layer 2), maintain model adapters (layer 1). The model and
agent layers are commodity-to-moderate; the package contract is where blackbox is
uniquely positioned.

## What's in a package

- **Spec** — `WorkspaceAgentSpec`: model ref, instructions, tool refs, output
  spec, policy, workspace, connectors, schedules, permissions, skills, publication
  metadata.
- **`ConnectorSpec`** — external service binding with an `auth_mode`.
- **`ScheduleSpec`** — cron / interval / (future) calendar triggers.
- **`ToolPermission`** — scopes and connector bindings.
- **`SkillBundleRef` / skills** — progressive-disclosure capability bundles.

## On-disk package format (shipped)

`workspace_agents/package.py`:

- `agent.json` manifest (format marker + serialized spec)
- `instructions.md` (diffable prompt)
- embedded `skills/<name>/` bundles
- helpers: `save` / `load` / `pack` / `unpack` / `install_workspace_agent_package`

Local skill sources embed on save and resolve to absolute paths on load;
non-local sources stay verbatim; unpack is zip-slip guarded; loading a newer
`format_version` fails loudly. Open (folds into versioning): integrity checksums
and signing.

## Validation & linting (shipped)

`validate_workspace_agent` / `ensure_valid_workspace_agent` return typed
`ValidationIssue`s: unresolvable tool refs (local/hosted/MCP-server/
connector-backed), permission/connector mismatches, duplicates, schedule
expression + timezone sanity (calendar triggers warn), and model availability
against the bundled catalog (warning — the catalog is a snapshot).

## Registry (shipped)

`SQLiteWorkspaceAgentRegistry` keeps every saved version keyed by
`(agent_id, version)` with a latest pointer; publish/deprecate transforms are
shared with the in-memory registry. `run_workspace_agent(...)` executes a spec.

## Scheduling (shipped)

`ScheduleExecutor` runs due cron and interval schedules through
`run_workspace_agent`, gated by the `before_scheduled_run` policy checkpoint,
producing `ScheduledRunRef`s. Drive from external cron via `run_due(now=...)` or
the built-in `serve()` loop. `calendar` triggers remain downstream.

## Open work (Horizon 2)

- **Permission enforcement at run time** — `ToolPermission` is metadata today;
  enforce scopes/connector bindings in the loop's policy gates so grants actually
  constrain execution.
- **Connector auth contract** — define how `ConnectorSpec.auth_mode` resolves to
  credentials at run time without pulling OAuth/secret storage into core
  (callback/provider interface; applications implement).
- **Versioning & upgrade story** — semver rules, install-time compatibility
  checks, migration notes between spec versions.
- **Portable skill packs (`SkillSpec`)** — today skills are inert (the package
  embeds `skills/<name>/` but nothing parses `SKILL.md` or activates it at run
  time). Add a `SkillSpec` that parses `SKILL.md` frontmatter and *compiles* into
  existing primitives (prompt fragments with progressive disclosure, toolsets,
  hosted/MCP tools, output spec, policy, workspace), exposed as
  `runtime.run(..., skills=[...])` — a kwarg, not a new facade. Two backends
  behind one compiler: the in-house model-adapter path (compile-to-primitives)
  and the Claude Code path (stage the bundle into `.claude/skills/` and set
  `setting_sources=["project"]`; the `setting_sources` forwarding prerequisite
  already landed). Full design in `docs/SKILLS.md`.

## Requirements

| ID | Priority | Requirement |
|---|---|---|
| (MVP) | P0 | Workspace package contracts exist: `WorkspaceAgentSpec`, connector auth modes, permissions, schedules, publication metadata, skills, serialization, and a registry protocol. |
| Design principle 13 | — | Sharing, scheduling, connector auth mode, tool permissions, skills, and publication metadata are modeled in core without forcing downstream UI/scheduler/OAuth/database. |

## Hard constraints

- **Packages are core contracts, not an execution loop** — they configure model/
  agent/tool/MCP/workspace primitives; they don't replace them.
- **No OAuth flows, secret storage, org RBAC, or admin UI in core** — those
  belong to downstream applications consuming the contract.

## Status & references

Package format, validation, SQLite registry, and schedule execution shipped.
Reference app: `examples/diet_coach.py` (pinned by
`tests/e2e/test_diet_coach_example.py`) — validates its spec, ships as an on-disk
package, installs from zip into the SQLite registry, fires a 9 AM cron schedule
through `ScheduleExecutor`, and reflects a conversational preference change the
next morning; fully offline (scripted model, faked Cal.com/Slack). Tests:
`tests/unit/workspace_agents/`. `ROADMAP.md` Horizon 2; gh #1 (skill packs).

→ Next: [09 — Approvals & policy](09-approvals-and-policy.md)
