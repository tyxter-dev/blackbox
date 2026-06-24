# 00 — Product Overview

**Package:** `blackbox` (import name) · **Status:** active, post-MVP · **Owner:** Diego Carboni

## 1. Executive summary

Blackbox is a Python library for running complete agent workflows and supervising
both model-level LLM workflows and cloud-managed agent workflows through a
unified, provider-native runtime.

It is the successor in spirit to `llm_factory_toolkit`, not a mechanical rewrite.
The original v1 product promise is still load-bearing:

```text
developer gives input + tools + output schema
runtime runs the model/tool loop internally
runtime executes allowed tools and feeds results back
runtime stops only at final output, failure, cancellation, or approval pause
developer receives a validated AgentResult[T]
```

That **blackbox loop** is the product soul. Users should not have to parse
provider tool calls, dispatch Python functions, append tool results, juggle
continuation IDs, or hand-validate JSON just to complete a normal agent task.

The runtime now spans direct model turns, local Python tools, hosted provider
tools, MCP servers, workspaces, approvals, artifacts, provider-native state,
cloud-managed agent sessions, and realtime audio/voice — all without flattening
anything into chat messages.

## 2. Why this product should exist

Older LLM libraries were designed around a single loop:

```text
user message -> model response -> local tool call -> local tool result -> final answer
```

That loop is still useful and must remain the easiest path, but it is no longer
enough. Modern provider offerings include hosted tools, cloud sandboxes, managed
agents, remote MCP servers, persistent sessions, event streams, tool approvals,
workspace state, generated artifacts, and deployment/evaluation workflows.

The result is a new integration problem: one library must coordinate direct
model turns, local tool execution, hosted model tools, remote MCP connectors,
local and cloud-managed coding agents, workspace files and patches, session
event streams, approvals and human review, artifacts and logs, and resumable
state.

Existing provider-normalization libraries flatten provider APIs into a shared
text/chat format. That is enough for simple model calls but not for agent
supervision. Blackbox preserves provider-native capabilities while exposing a
common control plane.

## 3. Positioning

### One sentence

Blackbox is a provider-native Python runtime that can either run a complete typed
agent/tool loop for the user *or* expose lower-level supervision over model
providers and agent providers.

### What it is

- A unified control plane for local model agents and cloud-managed agents.
- A high-level task runner for `input + tools + output schema -> AgentResult[T]`.
- A runtime for supervising long-running agent sessions.
- A provider adapter layer that preserves provider-native event and state semantics.
- A tool/workspace/MCP layer usable by local agents or bridged to cloud agents.
- A testable SDK for coding-agent workflows.

### What it is **not**

- Not a LiteLLM wrapper. **(No LiteLLM dependency, ever — see [01](01-architecture.md).)**
- Not a Chat Completions normalizer.
- Not a prompt-management platform.
- Not a UI framework.
- Not a replacement for cloud agent platforms.
- Not a provider-breadth race.

## 4. Design principles

1. **The complete agent loop is first-class.** The runtime owns tool-call
   detection, dispatch, continuation, finalization, and structured output
   validation.
2. **ModelProvider, AgentProvider, and workspace contracts are separate.** A
   model turn, an agent session, and the workspace/package governance layer are
   different units of work.
3. **Events are the runtime stream.** Text output is only one event projection.
4. **Provider state is preserved.** Continuation IDs, native output items, tool
   IDs, reasoning metadata, checkpoints, and session references survive turns.
5. **Compatibility is explicit.** Chat messages and OpenAI-like payloads are
   compatibility projections, not the runtime truth.
6. **Cloud agents are first-class.** They are not modeled as ordinary tools.
7. **Local tools are one backend among many.**
8. **Capabilities are negotiated, not assumed.** Each provider advertises
   capabilities and must raise a typed error rather than silently no-op.
9. **Provider-native escape hatches are required.** Every normalized event,
   artifact, and state object allows raw provider data.
10. **Approvals are central**, not a callback afterthought.
11. **Typed outputs are product surface, not decoration.**
12. **Coding agents are the primary use case** — workspaces, files, commands,
    patches, artifacts, sessions.
13. **Workspace agent packages are core contracts** — sharing, scheduling,
    connector auth mode, tool permissions, skills, and publication metadata are
    modeled in core without forcing a downstream UI, scheduler, OAuth provider,
    or database.

## 5. Target users

| Persona | Need | Product value |
|---|---|---|
| Technical founder | Build product-specific coding agents without locking into one provider | Unified model and cloud-agent supervision |
| Backend engineer | Add reliable AI workflows to a Python service | Typed events, artifacts, approvals, provider routing |
| Agent platform engineer | Compare local, hosted, and managed agents | Shared session lifecycle and event stream |
| AI tooling developer | Build internal coding assistants | Workspace, patch, shell, MCP, and cloud-agent abstractions |
| QA/evaluation engineer | Observe and evaluate agent runs | Event logs, traces, artifacts, checkpoints, metrics |

## 6. Primary use cases

- **UC0 — Run a complete blackbox agent loop.** Task + tools + optional output
  type ⇒ `AgentResult[T]`. The spiritual successor to v1; the simplest path.
- **UC1 — Run a simple model turn.** Stream normalized events; collect text.
- **UC2 — Run a local model-backed agent** with local tools, workspace access,
  and MCP connectors.
- **UC3 — Run a cloud coding agent** that inspects a repo, runs commands,
  changes files, and produces a patch/PR artifact.
- **UC4 — Bridge cloud providers under one supervision API** without treating
  them as internally equivalent.
- **UC5 — Handle approvals and interruptions** (tool confirmation, MCP auth,
  custom approval) via pause/resume.
- **UC6 — Collect artifacts** (files, diffs, logs, reports, snapshots).
- **UC7 — Evaluate and deploy agent workflows** where providers expose lifecycle.

## 7. Scope

### In scope (shipped or active)

The MVP proved the complete local agent loop end-to-end against a deterministic
`ScriptedModelProvider`. Beyond it, the runtime now ships: real model adapters
(OpenAI Responses, Anthropic Messages, Gemini, xAI, Echo); local + cloud agent
providers; local tools with context injection and routing; MCP with trust
guardrails; workspace backends; workspace-agent packages; approvals and policy;
all four structured-output strategies; observability presets; realtime; and
configuration via `WorkflowProfile`/`RuntimeConfig`. See each feature document
and [`FEATURES.md`](../../FEATURES.md) for status.

### Explicit non-goals

- Do not chase 100+ providers; new adapters must be justified by a use case.
- Do not use LiteLLM.
- Do not hide provider-native behavior behind a lowest-common-denominator type.
- Do not build frontend UI, full prompt management, org RBAC, OAuth flows, or
  secret storage in core — those belong to downstream applications.
- Do not require cloud-agent providers for simple local model use.

## 8. Success metrics

**Developer experience.** First local model turn < 5 min; first complete tool
loop with typed output < 10 min; first local agent session < 10 min; minimal
examples < 25 lines.

**Runtime quality.** 100% pass rate for provider contract tests; no adapter uses
chat messages as canonical state; every normalized event keeps raw provider data
where available; every capability flag has test coverage (positive *and*
negative); context injection is tested; high-level `run` tests cover tool
dispatch, continuation, and structured output validation.

**Product.** Preserve the original v1 experience: complete a useful task with
tools and a typed result without writing your own agent loop.

## 9. Definition of done for v0.1 (met)

Package installs editable; all P0 models/protocols exist; echo + local providers
work; tool runtime supports context injection; registry resolves model and agent
providers; runtime streams events and collects text; `AgentRuntime.run(...)` and
`AgentResult[T]` contracts exist; unit tests pass; README has minimal examples;
nothing depends on LiteLLM; nothing treats chat messages as internal canonical
state.

→ Next: [01 — Architecture](01-architecture.md)
