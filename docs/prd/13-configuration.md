# 13 — Feature: Configuration (WorkflowProfile & RuntimeConfig)

**Types:** `WorkflowProfile`, `RuntimeConfig` · **Package:** `src/blackbox/runtime/`

## Summary

`RuntimeConfig` is a frozen value object — **not a new facade** — that bundles a
`profile_name` plus override kwargs and expands into the same keyword arguments
the runtime methods already accept. It can be loaded from a mapping, environment
variables, or a JSON/TOML/YAML file. `WorkflowProfile` provides tuned defaults
for common workloads.

```python
config = RuntimeConfig.profile("coding_agent").with_overrides(
    provider="openai:gpt-5.5",
    workspace=WorkspaceSpec.git(url=repo_url, ref="main"),
)
result = await runtime.run(input="Refactor module X", config=config)
```

## Precedence

Profile defaults apply first, file/env values next, then `with_overrides(...)` on
top. **Explicit method arguments always override config values.**

## Built-in profiles

| Profile | What it tunes |
|---|---|
| `fast_text` | low-latency text replies, no tools |
| `structured_extraction` | strict provider-native schema output |
| `tool_agent` | parallel tool calls + bounded budget |
| `retrieval_agent` | tool search + auto compaction |
| `coding_agent` | dynamic tools, workspace required, `approval_policy="risky_actions"` |
| `cloud_agent_session` | delegates lifecycle to an agent provider |
| `realtime_voice` | WebSocket transport, audio in/out |
| `eval_run` | low-variance runs for evals |
| `cost_sensitive` | capped tokens and tool surface |
| `high_reliability` | deterministic, careful, more iterations |

See `docs/WORKFLOW_PROFILES.md` for tradeoffs and required values per profile.

## Why this shape

Profiles encode the cross-cutting decisions (which output strategy, whether a
workspace is required, approval posture, tool budget, transport) so callers don't
re-specify them per run. `RuntimeConfig` keeps that ergonomic *without* becoming a
ninth facade: it expands to the existing kwargs rather than introducing a new
execution surface.

## Requirements

Configuration is an ergonomics layer over existing requirements; it has no
separate P0/P1/P2 entries. It must not become a new top-level facade (an
"ask first" change — see [01](01-architecture.md) §3).

## Hard constraints

- `RuntimeConfig` is a **frozen value object**, not a facade — it expands to the
  same kwargs the runtime methods already accept.
- Adding a new top-level facade on `AgentRuntime` is "ask first"; prefer
  extending an existing facade or this `RuntimeConfig`-backed surface.
- Frozen dataclasses for config objects (style convention).

## Status & references

`WorkflowProfile` + `RuntimeConfig` and all ten profiles shipped. Docs:
`docs/WORKFLOW_PROFILES.md`. PRD configuration section / CLAUDE.md
"Configuration via WorkflowProfile and RuntimeConfig".

→ Next: [14 — Environment workers](14-environment-workers.md)
