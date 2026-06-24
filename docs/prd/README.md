# Blackbox PRD

A navigable, feature-split edition of the product requirements for **blackbox** —
a provider-native Python runtime that runs complete typed agent/tool loops *and*
exposes lower-level supervision over model providers, agent providers, and
workspaces.

> **Relationship to `docs/PRD.md`.** The single-file [`docs/PRD.md`](../PRD.md)
> remains the canonical contract and is updated when product direction shifts.
> This directory is a structured expansion of the same material, organized one
> capability per file so it can be read, linked, and reviewed in pieces. Where
> the two disagree, `docs/PRD.md` wins until reconciled. Current "what works
> today" status lives in [`FEATURES.md`](../../FEATURES.md); forward plans live
> in [`ROADMAP.md`](../../ROADMAP.md); vocabulary is defined in
> [`docs/TAXONOMY.md`](../TAXONOMY.md).

## The product in one sentence

Give the runtime a task, tools, and an output schema; it runs the agent loop
correctly and returns a validated `AgentResult[T]` — or drop down to
provider-native facades to supervise model turns, agent sessions, workspaces,
realtime audio, prompts, and caches directly.

## Reading order

| # | Document | Read it for |
|---|---|---|
| — | [README](README.md) | This index. |
| 00 | [Product overview](00-product-overview.md) | What it is, why it exists, positioning, design principles, users, scope, success metrics, definition of done. |
| 01 | [Architecture](01-architecture.md) | The two protocols, `AgentRuntime` facades, `AgentLoop`, core data contracts, the event taxonomy, error hierarchy, package layout. |
| 02 | [The blackbox loop](02-blackbox-loop.md) | **Feature.** The high-level `run`/`stream` task runner and `AgentResult[T]` — the v1 product promise. |
| 03 | [Model providers](03-model-providers.md) | **Feature.** `ModelProvider` and the per-vendor model adapters. |
| 04 | [Agent providers](04-agent-providers.md) | **Feature.** `AgentProvider` sessions: local and cloud-managed. |
| 05 | [Tools](05-tools.md) | **Feature.** Local registry, context injection, toolsets, routing, hosted tools. |
| 06 | [MCP](06-mcp.md) | **Feature.** Local + provider-native MCP, trust and security guardrails. |
| 07 | [Workspaces](07-workspaces.md) | **Feature.** Where agents work: local, git, sandbox, Docker, cloud backends. |
| 08 | [Workspace agent packages](08-workspace-agents.md) | **Feature.** Portable, governed, schedulable agent definitions — the differentiated bet. |
| 09 | [Approvals & policy](09-approvals-and-policy.md) | **Feature.** Approval flow, `Policy` protocol, checkpoints, safety. |
| 10 | [Structured output](10-structured-output.md) | **Feature.** Output strategies, validation, `OutputSpec`. |
| 11 | [Observability](11-observability.md) | **Feature.** Events, sinks, traces, OpenTelemetry, replay/eval, presets. |
| 12 | [Realtime](12-realtime.md) | **Feature.** Low-latency voice/audio sessions. |
| 13 | [Configuration](13-configuration.md) | **Feature.** `WorkflowProfile` and `RuntimeConfig`. |
| 14 | [Environment workers](14-environment-workers.md) | **Feature.** The inbound half — claiming and executing lab-dispatched work. |
| 15 | [Roadmap & open questions](15-roadmap-and-open-questions.md) | Milestones, horizons, risks, settled and open decisions. |

## How to use this PRD

- **New contributor:** read 00 → 01, then the feature file(s) for the package you
  are touching.
- **Reviewing a change:** start from the feature file whose requirements the
  change implements; each lists P0/P1/P2 acceptance criteria and the relevant
  hard constraints.
- **Product/scope discussion:** 00 (scope, non-goals) and 15 (roadmap, open
  questions).

Every feature document follows the same shape: **Summary → Why it exists →
Requirements (with priority) → Key contracts → Hard constraints → Status &
references.**
