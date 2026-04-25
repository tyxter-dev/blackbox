# PRD: Agent Runtime Core

**Working package name:** `agent_runtime`  
**Document version:** 0.1 draft  
**Date:** April 25, 2026  
**Owner:** Diego Carboni  
**Status:** Draft for implementation planning

## 1. Executive summary

Agent Runtime Core is a new Python library for running complete agent workflows and supervising both model-level LLM workflows and cloud-managed agent workflows through a unified, provider-native runtime.

The library is not a rewrite of `llm_factory_toolkit`. It is a new architecture, but it must preserve the original v1 product promise:

```text
User gives input.
Runtime runs the full agent/tool loop internally.
Model can request tools.
Runtime executes tools safely.
Model continues with tool results.
Runtime validates the final output.
User receives a final typed result.
```

That blackbox loop was the soul of `llm_factory_toolkit`: developers did not have to manually parse tool calls, dispatch functions, feed results back into the model, or reconstruct structured output. The new runtime must keep that high-level promise while adding modern provider-native sessions, events, artifacts, approvals, MCP, and cloud-agent support.

The new architecture is shaped around two first-class provider interfaces:

```text
ModelProvider
  Runs model turns.
  Examples: OpenAI Responses, Anthropic Messages, Gemini GenerateContent.

AgentProvider
  Runs agent sessions.
  Examples: OpenAI cloud/Codex-style agents, Anthropic Managed Agents, Google Agent Platform, local model-backed agents.
```

The core product thesis is:

> Modern AI applications no longer only call models and execute local tools. They supervise agents that use hosted tools, cloud runtimes, MCP servers, sandboxes, workspaces, approvals, artifacts, checkpoints, and event streams. The library should normalize the supervision layer, not flatten every provider into chat messages.

The companion product promise is:

> The default user experience should still be: give the runtime a task, tools, and an output schema; the runtime runs the agent correctly and returns a validated result.

The library must therefore be event-first, session-first, state-first, and provider-native. Chat messages may exist as an import/export compatibility layer, but they must not become the internal source of truth.

## 2. Why this product should exist

Older LLM libraries were designed around a simple loop:

```text
user message -> model response -> local tool call -> local tool result -> final answer
```

`llm_factory_toolkit` v1 existed because that loop was still hard and still novel. Developers could provide input, tools, context, and a structured output model, then rely on the library to own the iterative model/tool exchange inside a blackbox. That was the product value, not just an implementation detail.

That loop is still useful, and it must remain the easiest path. But by itself it is no longer enough for coding agents and production agents. Current provider offerings increasingly include hosted tools, cloud sandboxes, managed agents, remote MCP servers, persistent sessions, event streams, tool approvals, workspace state, generated artifacts, and deployment/evaluation workflows.

The result is a new integration problem. Developers need one library that can coordinate:

- direct model turns,
- local tool execution,
- hosted model tools,
- remote MCP connectors,
- local coding agents,
- cloud-managed coding agents,
- workspace files and patches,
- session event streams,
- approvals and human review,
- artifacts and logs,
- resumable state.

Existing provider-normalization libraries are useful for simple model calls, but they tend to normalize provider APIs into a shared text/chat format. That is not enough for agent supervision. This new library should preserve provider-native capabilities while exposing a common control plane.

The runtime should therefore serve two levels of use:

- **High-level task execution:** `input + tools + output schema -> AgentResult[T]`.
- **Low-level supervision:** provider-native model turns, agent sessions, events, artifacts, state, approvals, and traces.

The high-level path must be built on top of the low-level primitives, not replaced by them.

## 3. Product positioning

### 3.1 One sentence

Agent Runtime Core is a provider-native Python runtime that can either run a complete typed agent/tool loop for the user or expose lower-level supervision over model providers and cloud agent providers.

### 3.2 What it is

- A unified control plane for local model agents and cloud-managed agents.
- A high-level task runner for `input + tools + output schema -> typed result`.
- A runtime for supervising long-running agent sessions.
- A provider adapter layer that preserves provider-native event and state semantics.
- A tool/workspace/MCP layer that can be used by local agents or bridged to cloud agents.
- A testable SDK for coding-agent workflows.

### 3.3 What it is not

- Not a LiteLLM wrapper.
- Not a Chat Completions normalizer.
- Not a prompt-management platform.
- Not a UI framework.
- Not a replacement for cloud agent platforms.
- Not a provider-breadth race.

## 4. Design principles

1. **The complete agent loop is first-class.** The runtime must provide a high-level blackbox path that owns tool-call detection, dispatch, continuation, finalization, and structured output validation.
2. **ModelProvider and AgentProvider are separate.** A model turn and an agent session are different units of work.
3. **Events are the runtime stream.** Text output is only one event projection.
4. **Provider state is preserved.** Provider continuation IDs, native output items, tool IDs, reasoning metadata, checkpoints, and session references must survive across turns.
5. **Compatibility is explicit.** Chat messages and OpenAI-like payloads are compatibility projections, not the runtime truth.
6. **Cloud agents are first-class.** Do not model cloud agents as ordinary tools.
7. **Local tools are one backend among many.** Local tool execution remains important, but it should not define the whole architecture.
8. **Capabilities are negotiated, not assumed.** Each provider advertises model and agent capabilities.
9. **Provider-native escape hatches are required.** Every normalized event, artifact, and state object must allow raw provider data.
10. **Approvals are central.** Human review, permission checks, and tool confirmation must be part of the runtime, not a callback afterthought.
11. **Typed outputs are product surface, not decoration.** Structured output with Pydantic-style validation must be available at the top-level runtime API.
12. **Coding agents are the primary use case.** The library should understand workspaces, files, commands, patches, artifacts, and sessions.

## 5. Target users

| Persona | Need | Product value |
|---|---|---|
| Technical founder | Build product-specific coding agents without locking into one provider | Unified model and cloud-agent supervision |
| Backend engineer | Add reliable AI workflows to a Python service | Typed events, artifacts, approvals, provider routing |
| Agent platform engineer | Compare local, hosted, and managed agents | Shared session lifecycle and event stream |
| AI tooling developer | Build internal coding assistants | Workspace, patch, shell, MCP, and cloud-agent abstractions |
| QA/evaluation engineer | Observe and evaluate agent runs | Event logs, traces, artifacts, checkpoints, metrics |

## 6. Primary use cases

### UC0: Run a complete blackbox agent loop

A developer gives the runtime a task, registered tools, context, and an optional Pydantic-style output type. The runtime calls the model, detects tool requests, executes allowed tools, feeds tool results back to the model, repeats until completion, validates the final structured output, and returns `AgentResult[T]`.

This is the spiritual successor to `llm_factory_toolkit` v1 and must be the simplest path for ordinary users.

### UC1: Run a simple model turn

A developer calls an OpenAI, Anthropic, or Gemini model through a `ModelProvider`. The runtime streams normalized events and collects final text as a projection.

### UC2: Run a local model-backed agent

A developer creates a local agent that uses a model provider, local tools, workspace access, and MCP connectors. The agent emits tool, workspace, and model events.

### UC3: Run a cloud coding agent

A developer starts a provider-managed agent session to inspect a repository, run commands, modify files, and produce a patch or PR artifact. The runtime streams status, logs, tool use, file changes, and artifacts.

### UC4: Bridge cloud providers under one supervision API

The same app can start or observe OpenAI-style cloud agents, Anthropic Managed Agents, and Google Agent Platform workflows without treating them as equivalent internally. Provider-specific state remains available.

### UC5: Handle approvals and interruptions

When a provider session requires tool confirmation, MCP authorization, or custom approval, the runtime emits an approval event, pauses or marks the session waiting, accepts an approval decision, and resumes.

### UC6: Collect artifacts

Agent sessions can produce files, diffs, patches, logs, reports, test outputs, screenshots, or deployment metadata. The runtime stores and lists artifacts consistently.

### UC7: Evaluate and deploy agent workflows

For providers with platform lifecycle features, the runtime can create/evaluate/deploy/observe agent projects or delegate those operations to provider adapters.

## 7. Scope

### 7.1 MVP scope

The MVP must include:

- `ModelProvider` protocol.
- `AgentProvider` protocol.
- Provider registry and routing.
- Event model.
- Run/session state model.
- Session references.
- Artifact model.
- Approval model.
- Local tool registry/runtime with context injection.
- Tool registry facade on `AgentRuntime`.
- `AgentResult[T]` data contract.
- High-level `AgentRuntime.run(...)` contract for complete local model/tool loops.
- Local model-backed `AgentProvider`.
- Echo model provider for tests.
- OpenAI Responses-native model provider as the first real model adapter.
- Provider stubs for Anthropic, Gemini, OpenAI cloud agents, Anthropic Managed Agents, and Google Agent Platform.
- Pytest coverage for the contracts and local runtime.

### 7.2 Post-MVP scope

- Anthropic Messages-native model provider.
- Gemini GenerateContent-native model provider.
- OpenAI cloud/Codex-style agent provider.
- Anthropic Managed Agents provider.
- Google Agents CLI / Agent Platform provider.
- MCP local and remote connector support.
- Workspace file, patch, and command abstractions.
- Dynamic tool discovery and tool search bridging.
- Trace/event sinks.
- Provider-native structured output.
- Evals and deployment lifecycle support.

### 7.3 Explicit non-goals

- Do not chase 100+ providers initially.
- Do not use LiteLLM as a dependency.
- Do not hide provider-native behavior behind a lowest-common-denominator message type.
- Do not build frontend UI.
- Do not implement full prompt management.
- Do not implement a general database-backed orchestration service in the first version.
- Do not require cloud-agent providers for simple local model use.

## 8. Architecture overview

### 8.1 Conceptual architecture

```text
Application
  |
  v
AgentRuntime
  |-------------------------------|-------------------------------|
  v                               v                               v
AgentLoop / TaskRunner            ModelRuntimeFacade              AgentRuntimeFacade
  |                               |                               |
  |                               v                               v
  |                               ModelProvider                   AgentProvider
  |                               |                               |
  |                               |                               |-- cloud agent session
  |                               |                               |-- local agent session
  |                               |                               |-- managed environment
  |                               |                               |-- artifacts / approvals / logs
  |
  |-- autonomous tool loop
  |-- local/MCP/hosted tool dispatch bridge
  |-- structured output validation
  |-- AgentResult[T]
  |
  |-- model turn
  |-- streamed events
  |-- provider state
  |-- hosted tools / MCP / reasoning items
```

### 8.2 Package layout

```text
src/agent_runtime/
  core/
    events.py
    items.py
    state.py
    sessions.py
    capabilities.py
    artifacts.py
    approvals.py
    errors.py

  providers/
    base.py
    registry.py

  runtime.py

  models/
    openai_responses.py
    anthropic_messages.py
    gemini_generate_content.py
    echo.py

  agents/
    local.py
    openai_cloud.py
    anthropic_managed.py
    google_platform.py

  tools/
    registry.py
    runtime.py
    results.py
    catalog.py

  workspaces/
    spec.py
    changes.py

  mcp/
    spec.py
    connector.py

  observability/
    traces.py
    sinks.py
```

### 8.3 Core runtime split

`AgentRuntime` exposes a high-level task runner, a tool registry facade, and two lower-level supervision facades:

```python
runtime.run(...)
runtime.stream(...)

runtime.tools.register(...)
runtime.tools.call(...)
runtime.tools.list(...)

runtime.models.run(...)
runtime.models.stream(...)

runtime.agents.create_agent(...)
runtime.agents.create_session(...)
runtime.agents.stream(...)
runtime.agents.run(...)
runtime.agents.send_message(...)
runtime.agents.approve(...)
runtime.agents.cancel(...)
runtime.agents.list_artifacts(...)
```

The top-level `runtime.run(...)` and `runtime.stream(...)` APIs are the high-level task runner. They preserve the original blackbox promise: complete loop in, typed result out.

The `runtime.models.*` and `runtime.agents.*` facades are lower-level supervision APIs. This split prevents cloud agents from being squeezed into a `generate()` API while still keeping a simple default path.

### 8.4 AgentLoop / TaskRunner

`AgentLoop` is the execution layer between provider-native model turns and tool execution. It owns the iterative control flow for local model-backed agents.

Responsibilities:

- Start a model turn with task input, tool definitions, context, and provider state.
- Detect provider-native function/tool/MCP/hosted-tool requests.
- Dispatch local Python tools through `ToolRuntime` when the runtime owns execution.
- Bridge provider-native hosted tools and remote MCP calls without pretending they are local Python functions.
- Emit canonical events for every model, tool, approval, artifact, and state transition.
- Feed local tool results back into the provider using the provider's native continuation mechanism.
- Continue until a final answer, terminal event, failure, cancellation, or approval pause.
- Validate structured output into the requested output type.
- Return `AgentResult[T]` with typed output, text projection, events, items, artifacts, payloads, provider state, and metadata.

The loop must be implemented as a runtime layer, not buried inside one provider adapter. Provider adapters expose native events and continuation state; the loop decides how to continue the task.

## 9. Core provider contracts

### 9.1 ModelProvider

A `ModelProvider` runs model turns. It can be stateless or stateful, but any provider-native continuation state must be returned through `ProviderState`.

```python
class ModelProvider(Protocol):
    @property
    def provider_id(self) -> str: ...

    def capabilities(self, model: str | None = None) -> ModelCapabilities: ...

    async def stream_turn(self, request: TurnRequest) -> AsyncIterator[AgentEvent]: ...
```

Required behavior:

- Accept a `TurnRequest`.
- Emit `AgentEvent` instances.
- Preserve raw provider data in event `raw` or `data` fields.
- Emit canonical text deltas using `model.text.delta`.
- Emit tool, hosted tool, MCP, reasoning, and state events where supported.
- Return provider state through completion metadata or collected result.

### 9.2 AgentProvider

An `AgentProvider` runs sessions. It may represent a local runtime, a cloud coding agent, a managed provider environment, or a deployment platform.

```python
class AgentProvider(Protocol):
    @property
    def provider_id(self) -> str: ...

    def capabilities(self) -> AgentCapabilities: ...

    async def create_agent(self, spec: AgentSpec) -> AgentRef: ...
    async def start_session(self, agent: AgentRef | str, task: TaskSpec) -> AgentSession: ...
    async def stream_events(self, session: SessionRef | AgentSession) -> AsyncIterator[AgentEvent]: ...
    async def send_message(self, session: SessionRef | AgentSession, message: str) -> None: ...
    async def approve(self, approval_id: str, decision: ApprovalDecision) -> None: ...
    async def cancel(self, session: SessionRef | AgentSession) -> None: ...
    async def list_artifacts(self, session: SessionRef | AgentSession) -> list[Artifact]: ...
```

Required behavior:

- Treat sessions as first-class resources.
- Stream session, model, tool, cloud-agent, workspace, approval, and artifact events.
- Preserve provider session IDs and raw event payloads.
- Support cancellation when the provider supports it.
- Support resumption when the provider supports it.
- Support artifacts when the provider exposes them.

## 10. Data contracts

### 10.1 AgentEvent

`AgentEvent` is the main stream object.

Required fields:

- `id`: unique event ID.
- `type`: canonical event type string.
- `session_id`: optional session ID.
- `provider`: provider key.
- `item_id`: optional item/tool/artifact ID.
- `data`: normalized event payload.
- `raw`: raw provider-native payload.
- `timestamp`: event time.

### 10.2 RunItem

`RunItem` represents durable items created during a run or session.

Examples:

- message item,
- reasoning item,
- function call,
- function result,
- hosted tool call,
- hosted tool result,
- MCP list tools event,
- MCP call,
- approval request,
- workspace file change,
- patch artifact.

### 10.3 ProviderState

`ProviderState` preserves provider-native continuation information.

Required fields:

- `provider`,
- `conversation_id`,
- `previous_response_id`,
- `native_history`,
- `reasoning_state`,
- `tool_state`,
- `continuation`.

Provider adapters may extend `continuation` with provider-specific fields.

### 10.4 AgentSession

`AgentSession` represents a running or resumable session.

Required statuses:

```text
created
running
waiting
completed
failed
cancelled
```

The `waiting` state is used for approvals, custom tool results, user clarification, or provider-side pauses.

### 10.5 Artifact

Artifacts represent provider or runtime outputs.

Artifact types should include:

- `file`,
- `patch`,
- `diff`,
- `log`,
- `report`,
- `command_output`,
- `workspace_snapshot`,
- `deployment`,
- `evaluation`.

Each artifact must include metadata and may include provider-native references.

### 10.6 ApprovalDecision

Approval decisions must support:

- allow,
- deny,
- optional reason,
- optional modified arguments or policy metadata.

### 10.7 AgentResult[T]

`AgentResult[T]` is the collected output of a high-level `AgentRuntime.run(...)` call.

Required fields:

- `output`: validated structured output of type `T`, or plain text when no output type is requested,
- `text`: final text projection,
- `events`: collected `AgentEvent` stream,
- `items`: durable `RunItem` records,
- `artifacts`: produced artifacts,
- `payloads`: structured tool payloads returned for application use,
- `provider_state`: final provider continuation state when available,
- `metadata`: usage, timing, provider, model, and diagnostic metadata.

When `output_type` is provided, the runtime must validate the final model output before returning. Validation failures should be typed runtime errors and should include enough context for retry or debugging without leaking sensitive tool context.

## 11. Canonical event taxonomy

The library must define stable constants for common lifecycle events. Providers may emit additional provider-specific event types, but should map shared concepts to these canonical names.

| Domain | Event examples |
|---|---|
| Run/session | `run.started`, `run.completed`, `session.created`, `session.started`, `session.completed`, `session.failed`, `session.cancelled` |
| Model | `model.request.started`, `model.item.created`, `model.text.delta`, `model.reasoning.delta`, `model.completed` |
| Tools | `tool.call.requested`, `tool.call.started`, `tool.call.completed`, `tool.call.failed` |
| Tool search | `tool_search.requested`, `tool_search.completed` |
| MCP | `mcp.list_tools.completed`, `mcp.approval.required`, `mcp.call.started`, `mcp.call.completed` |
| Cloud agents | `cloud_agent.status.changed`, `cloud_agent.log`, `cloud_agent.checkpoint.created` |
| Workspace | `workspace.file.read`, `workspace.file.changed`, `workspace.command.started`, `workspace.command.completed`, `workspace.patch.created` |
| Approval | `approval.requested`, `approval.approved`, `approval.denied` |
| Artifacts | `artifact.created`, `artifact.updated` |
| Evaluation | `eval.started`, `eval.completed` |

## 12. Functional requirements

### 12.1 P0 requirements

| ID | Requirement | Acceptance criteria |
|---|---|---|
| P0-R1 | Provider registry | Register and resolve `ModelProvider` and `AgentProvider` by provider key. |
| P0-R2 | ModelProvider contract | Echo provider and at least one real provider implement `stream_turn`. |
| P0-R3 | AgentProvider contract | Local agent provider implements create/start/stream/cancel/list artifacts stubs. |
| P0-R4 | Event model | Runtime emits canonical events and preserves raw provider payloads. |
| P0-R5 | Session model | Agent sessions expose ID, provider, status, task, model, metadata, and refs. |
| P0-R6 | Provider state | Model runs can return and accept provider continuation state. |
| P0-R7 | Tool runtime | Local tools can be registered, called, context-injected, timed out, and run concurrently. |
| P0-R8 | Result collection | Model runtime can collect streamed text into a `TurnResult`. |
| P0-R9 | Capability flags | Providers expose explicit capability objects. |
| P0-R10 | Test scaffold | Unit tests cover registry, echo model, local agent, and tool runtime. |
| P0-R11 | High-level agent loop contract | `AgentRuntime.run(...)` API exists and defines the blackbox `input + tools + output_type -> AgentResult[T]` contract, even if the first implementation supports only local model-backed loops. |
| P0-R12 | Structured result contract | `AgentResult[T]` exists with typed output, text, events, items, artifacts, payloads, provider state, and metadata. |
| P0-R13 | Tool facade | `AgentRuntime` exposes a tool registry facade usable by the high-level task runner. |

### 12.2 P1 requirements

| ID | Requirement | Acceptance criteria |
|---|---|---|
| P1-R1 | OpenAI Responses-native model provider | Maps Responses output items to events without flattening to chat messages. |
| P1-R2 | Anthropic Messages-native model provider | Maps text, tool_use, tool_result, server tools, and reasoning blocks to events/items. |
| P1-R3 | Gemini-native model provider | Preserves parts, function-call IDs, thought signatures, and provider state. |
| P1-R4 | OpenAI cloud agent provider | Can start/resume a coding-agent or SDK-backed session and stream status/artifacts. |
| P1-R5 | Anthropic Managed Agents provider | Supports agent/environment/session/event concepts and approval pauses. |
| P1-R6 | Google Agent Platform provider | Wraps Agents CLI/Agent Platform create, run, eval, deploy, and observe workflows. |
| P1-R7 | Artifact handling | Session artifacts can be listed and typed consistently. |
| P1-R8 | Approval flow | Approval events can pause and resume local or provider sessions. |
| P1-R9 | Workspace abstraction | Represent repo/local/sandbox/cloud workspace specs and file changes. |
| P1-R10 | Observability | Event sinks can receive all runtime events. |
| P1-R11 | Local autonomous tool loop | Local model-backed runtime detects tool calls, executes local tools, feeds results back through provider-native continuation, and stops only on final answer, error, cancellation, or approval pause. |
| P1-R12 | Structured output validation | Runtime validates final output against Pydantic models, dataclasses, TypedDict-like schemas, or provider-native structured-output mechanisms where available. |

### 12.3 P2 requirements

| ID | Requirement | Acceptance criteria |
|---|---|---|
| P2-R1 | Dynamic tool discovery | Add catalog/tool-search support without making meta-tools the only mechanism. |
| P2-R2 | MCP local and remote support | Support local MCP dispatch and provider-native remote MCP connectors. |
| P2-R3 | Deployment lifecycle | Providers with deployment support expose deploy/status/log operations. |
| P2-R4 | Evals | Providers with eval support can run and stream evaluation events. |
| P2-R5 | Chat compatibility export | Export chat messages when needed, clearly marked as a projection. |
| P2-R6 | Persistence adapters | Persist sessions, events, artifacts, and provider state to external stores. |
| P2-R7 | Cost and usage accounting | Aggregate usage across model turns, tools, and cloud sessions. |
| P2-R8 | Policy engine | Centralized permission, approval, and data-access policy layer. |

## 13. Provider-specific requirements

### 13.1 OpenAI model provider

Target adapter: `OpenAIResponsesProvider`.

Requirements:

- Use provider-native Responses API semantics.
- Map native output items to `AgentEvent` and `RunItem`.
- Preserve response IDs and continuation metadata in `ProviderState`.
- Represent hosted tools, tool search, MCP, function calls, and reasoning as typed events/items.
- Do not convert to Chat Completions internally.

Initial event mappings:

| Provider concept | Runtime event/item |
|---|---|
| Text delta | `model.text.delta` |
| Reasoning item/delta | `model.reasoning.delta` or reasoning `RunItem` |
| Function call | `tool.call.requested` or function-call `RunItem` |
| Function output | `tool.call.completed` or function-result `RunItem` |
| Hosted web/file/code/shell/computer tool | hosted tool event/item |
| Tool search call | `tool_search.requested` |
| Tool search output | `tool_search.completed` |
| Remote MCP list/call | `mcp.list_tools.completed`, `mcp.call.started`, `mcp.call.completed` |

### 13.2 OpenAI cloud/Codex-style agent provider

Target adapter: `OpenAICloudAgentProvider`.

Requirements:

- Model cloud/coding-agent work as sessions, not tool calls.
- Support thread/session creation where provider SDKs expose it.
- Support run/resume/cancel where available.
- Stream agent logs, workspace changes, command execution, patches, and artifacts.
- Preserve provider thread IDs and raw event payloads.
- Support local SDK wrapper mode if cloud API is not available yet.

### 13.3 Anthropic model provider

Target adapter: `AnthropicMessagesProvider`.

Requirements:

- Preserve native Messages content blocks.
- Map `tool_use` and `tool_result` to typed events/items.
- Preserve server-side tool use as hosted tool events.
- Preserve thinking/reasoning metadata where supported.
- Avoid requiring alternating chat-message normalization in the runtime core.

### 13.4 Anthropic Managed Agents provider

Target adapter: `AnthropicManagedAgentProvider`.

Requirements:

- Support four provider concepts: agent, environment, session, events.
- Support server-side event streaming.
- Support custom tool-use pauses and tool results.
- Support tool confirmation approval flows.
- Support session resumption and checkpoint metadata where available.
- Preserve raw provider events.

### 13.5 Gemini model provider

Target adapter: `GeminiGenerateContentProvider`.

Requirements:

- Preserve native Content/Part structure.
- Preserve function-call IDs where available.
- Preserve thought signatures and part ordering.
- Emit text, function call, function response, and reasoning-related events.
- Avoid synthetic IDs except as a legacy fallback.

### 13.6 Google Agent Platform provider

Target adapter: `GoogleAgentPlatformProvider`.

Requirements:

- Wrap Agents CLI / Agent Platform lifecycle commands or APIs.
- Support project scaffolding where appropriate.
- Support run/eval/deploy/observe workflows.
- Support Agent Development Lifecycle metadata.
- Preserve cloud project, deployment, eval, and observability references.
- Represent CLI outputs as typed runtime events and artifacts.

## 14. Tooling requirements

### 14.1 Local tool registry

The local tool system should support:

- function registration,
- optional JSON schema,
- category/tags/group metadata,
- blocking tool offload,
- context injection,
- mock execution,
- usage metadata,
- local execution policies.

### 14.2 Context injection

Context injection remains a core differentiator. Tool functions can request private runtime values by parameter name. These values are never exposed to the model as tool schema fields unless explicitly configured.

Example:

```python
def create_ticket(title: str, user_id: str, db: Database) -> ToolResult:
    ...
```

The model supplies `title`; the runtime injects `user_id` and `db`.

### 14.3 Tool results

`ToolResult` must split:

- `content`: LLM-facing result text,
- `payload`: app-facing structured result,
- `metadata`: diagnostics,
- `error`: optional error status.

### 14.4 Local vs hosted tools

The runtime must distinguish:

- local Python tools,
- hosted provider tools,
- remote MCP tools,
- cloud-agent tools,
- workspace tools,
- platform lifecycle tools.

Hosted/provider tools should not be fake local tools.

### 14.5 Tool dispatch loop

The high-level task runner must own the complete local tool loop:

```text
provider emits tool call
runtime validates tool name and arguments
runtime checks policy and approval requirements
runtime executes the tool or pauses for approval
runtime captures content, payload, metadata, and errors
runtime sends the result back through provider-native continuation
provider continues generation
```

Applications may observe or override parts of this loop, but ordinary users should not need to implement it by hand.

## 15. Workspace requirements

The workspace layer should represent where agent work happens.

Workspace types:

- local directory,
- Git repository,
- provider sandbox,
- cloud container,
- mounted artifact bundle,
- remote workspace reference.

Workspace events:

- file read,
- file write,
- file edit,
- file delete,
- command started,
- command completed,
- patch created,
- workspace snapshot created.

The runtime should not assume that all workspaces are locally accessible. Cloud providers may return opaque references.

## 16. MCP requirements

MCP must be a first-class connector family, not a provider-specific hack.

The library should support two paths:

1. **Local MCP dispatch:** runtime connects to MCP servers and executes tools locally.
2. **Provider-native remote MCP:** provider is given an MCP server configuration and handles listing/calling tools remotely.

Requirements:

- MCP server specs for stdio and HTTP.
- Namespaced tool identities.
- Approval policy support.
- Event emission for list/call/approval/result.
- Raw provider MCP event preservation.
- Capability checks per provider.

## 17. Approval and safety requirements

The runtime must support approval workflows for:

- dangerous or irreversible local tools,
- cloud-agent tool confirmation,
- MCP tool confirmation,
- workspace write operations,
- command execution,
- deployment actions,
- data exfiltration-sensitive operations.

Approval flow:

```text
provider/runtime emits approval.requested
session status becomes waiting when needed
application returns ApprovalDecision
runtime/provider resumes or denies action
approval.approved or approval.denied is emitted
```

Policies should be pluggable. Initial implementation may use simple callback policies; later versions can add declarative policy rules.

## 18. Observability requirements

The runtime must support event sinks.

Event sinks should receive:

- all canonical events,
- provider raw events where allowed,
- timing metadata,
- usage and cost metadata,
- session status transitions,
- tool and workspace operations,
- artifact creation events,
- approval decisions.

The runtime should support:

- in-memory event collection,
- callback sink,
- JSONL sink,
- OpenTelemetry-style trace adapter later.

## 19. Error handling requirements

Errors should be typed and actionable.

Suggested hierarchy:

```text
AgentRuntimeError
  ConfigurationError
  ProviderNotFoundError
  CapabilityError
  ProviderExecutionError
  ToolExecutionError
  ApprovalError
  SessionError
  ArtifactError
  WorkspaceError
  MCPError
```

Provider adapters must avoid leaking sensitive values in exception messages.

## 20. Testing requirements

### 20.1 Unit tests

Required unit test suites:

- provider registry,
- provider capability checks,
- event model,
- state model,
- local tool runtime,
- context injection,
- artifact model,
- approval model,
- local agent provider,
- echo model provider,
- runtime text collection,
- cancellation behavior,
- error handling.

### 20.2 Contract tests

Every provider adapter must pass provider contract tests:

- emits required event types,
- preserves raw provider payloads,
- returns provider state,
- respects capability flags,
- supports cancellation/resumption only if advertised,
- does not claim unsupported hosted/cloud features.

### 20.3 Integration tests

Integration tests should be provider-gated by environment variables and marked separately.

Suggested markers:

```text
integration_openai
integration_anthropic
integration_gemini
integration_google_platform
integration_cloud_agent
integration_mcp
```

### 20.4 Golden event tests

For each provider, capture representative raw provider responses/events and assert normalized event output against golden fixtures.

## 21. Success metrics

### 21.1 Developer experience metrics

- Time to run first local model turn: under 5 minutes.
- Time to run first complete tool loop with typed output: under 10 minutes.
- Time to run first local agent session: under 10 minutes.
- Time to inspect event stream and artifacts: under 10 minutes.
- Minimal examples use fewer than 25 lines of code.

### 21.2 Runtime quality metrics

- 100% pass rate for provider contract tests.
- No provider adapter may use chat messages as internal canonical state.
- Every emitted normalized event keeps raw provider data where available.
- Every capability flag has test coverage.
- Local tool context injection is covered by tests.
- High-level `runtime.run(...)` tests cover tool dispatch, continuation, and structured output validation.

### 21.3 Product metrics

- Support at least one real `ModelProvider` and one real or SDK-backed `AgentProvider` by v0.2.
- Support OpenAI, Anthropic, and Google model or agent surfaces by v0.5.
- Support local tools, MCP, workspace events, approvals, and artifacts by v0.6.
- Preserve the original v1 experience: users can complete a useful task with tools and a typed result without writing their own agent loop.

## 22. Milestones

### M0: Scaffold complete

Status: initial scaffold.

Deliverables:

- package structure,
- protocols,
- event/state/session/artifact/approval models,
- registry,
- echo model provider,
- local agent provider,
- local tool runtime,
- `AgentResult[T]` contract,
- high-level `AgentRuntime.run(...)` contract,
- baseline tests.

### M1: OpenAI Responses-native model provider

Deliverables:

- real provider adapter,
- text streaming,
- provider state,
- function-call events,
- hosted tool events where available,
- tool-search event mapping,
- golden tests.

### M2: Local coding-agent runtime

Deliverables:

- complete local model/tool loop,
- provider-native tool-call continuation,
- structured output validation,
- local workspace spec,
- file/patch events,
- command execution policy hooks,
- local tools integrated into model loop,
- approval callbacks,
- artifacts.

### M3: OpenAI cloud/Codex-style agent provider

Deliverables:

- create/resume session,
- stream logs/events,
- list artifacts,
- cancel sessions,
- preserve provider raw data,
- integration tests where possible.

### M4: Anthropic Managed Agents provider

Deliverables:

- agent/environment/session mapping,
- SSE/event stream mapping,
- custom tool-use result flow,
- tool confirmation approval flow,
- resumption metadata.

### M5: Google Agent Platform provider

Deliverables:

- Agents CLI wrapper,
- create/run/eval/deploy/observe surfaces,
- structured event parsing,
- artifacts for generated projects, evals, deployments, and logs.

### M6: MCP and dynamic tool loading

Deliverables:

- local MCP connector,
- remote MCP provider config support,
- tool catalog,
- provider-native tool-search bridge,
- approval policy support.

### M7: Hardening and release

Deliverables:

- docs,
- examples,
- contract tests,
- integration test gates,
- type checking,
- ruff,
- changelog,
- release candidate.

## 23. API examples

### 23.1 Complete agent loop with typed output

```python
from pydantic import BaseModel

from agent_runtime import AgentRuntime
from agent_runtime.models.openai_responses import OpenAIResponsesProvider


class TicketDecision(BaseModel):
    should_escalate: bool
    priority: str
    summary: str


runtime = AgentRuntime()
runtime.registry.register_model(OpenAIResponsesProvider(api_key="..."))
runtime.tools.register(create_ticket)
runtime.tools.register(search_customer)

result = await runtime.run(
    provider="openai/gpt-5.4",
    input="Review this customer report and create a ticket if needed.",
    tools=["search_customer", "create_ticket"],
    output_type=TicketDecision,
)

decision: TicketDecision = result.output
print(decision.priority)
```

The user does not parse tool calls, call tools manually, append tool results, or validate JSON manually. The runtime owns the loop.

### 23.2 Stream the complete agent loop

```python
async for event in runtime.stream(
    provider="openai/gpt-5.4",
    input="Find the failing test and propose a fix.",
    tools=["read_file", "run_tests"],
):
    print(event.type, event.data)
```

### 23.3 Model turn

```python
from agent_runtime import AgentRuntime
from agent_runtime.models.openai_responses import OpenAIResponsesProvider

runtime = AgentRuntime()
runtime.registry.register_model(OpenAIResponsesProvider(api_key="..."))

result = await runtime.models.run(
    provider="openai",
    model="gpt-5.4",
    input="Explain the failing test in this traceback.",
)

print(result.text)
```

### 23.4 Stream model events

```python
async for event in runtime.models.stream(
    provider="openai/gpt-5.4",
    input="Review this patch.",
):
    if event.type == "model.text.delta":
        print(event.data["delta"], end="")
```

### 23.5 Start an agent session

```python
session = await runtime.agents.create_session(
    provider="local",
    agent="repo-maintainer",
    task="Find why CI is failing and prepare a patch.",
    model="openai/gpt-5.4",
)

async for event in runtime.agents.stream(session):
    print(event.type, event.data)
```

### 23.6 Approve a paused action

```python
await runtime.agents.approve(
    provider="anthropic-managed",
    approval_id="approval_123",
    decision=ApprovalDecision(allow=True, reason="Safe to run tests."),
)
```

### 23.7 List artifacts

```python
artifacts = await runtime.agents.list_artifacts(session)
for artifact in artifacts:
    print(artifact.type, artifact.name, artifact.uri)
```

## 24. Compatibility strategy

The library should allow compatibility exports, but not use them internally.

Supported compatibility projections:

- `TurnResult.text`,
- `TurnResult.to_chat_messages()` later,
- provider-specific raw payload access,
- OpenAI-like event or tool-call export if needed.

Compatibility import/export must be clearly labeled as lossy when appropriate.

## 25. Risks and mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Provider cloud-agent APIs are unstable | Adapters may churn | Keep raw provider state, isolate adapters, contract-test normalized events. |
| Over-abstraction hides provider strengths | Library becomes another lowest-common-denominator wrapper | Preserve raw payloads and capability flags; expose provider-specific extras. |
| Scope grows too wide | MVP stalls | Implement OpenAI Responses and local agent first; delay full provider breadth. |
| Runtime becomes only a provider/session abstraction | Product loses the original v1 value | Make `runtime.run(..., tools=..., output_type=...)` a first-class API and test it as a product contract. |
| Agent sessions are hard to persist | Resumption and observability suffer | Design `ProviderState`, `SessionRef`, and event log early. |
| Tool approvals become inconsistent | Safety and UX regress | Centralize approval event and decision types. |
| Cloud agents produce opaque artifacts | Application cannot consume outputs | Normalize artifact metadata and keep provider-native references. |

## 26. Open questions

1. What is the package name: `agent_runtime`, `agentkit`, `agentflow`, or another brand?
2. Should the first real `AgentProvider` be OpenAI cloud/Codex-style, Anthropic Managed Agents, or Google Agent Platform?
3. Should workspaces be part of core in v0.1 or introduced in v0.2?
4. Should tools use dataclasses or Pydantic models for schemas?
5. Should the runtime include persistence interfaces immediately?
6. Should event sinks be synchronous, asynchronous, or both?
7. How much provider-specific API should be exposed through `extra` fields versus provider-specific adapter methods?
8. Should there be a CLI for running sessions and dumping events/artifacts?
9. Which structured-output validators should be supported in the first release: Pydantic only, dataclasses, TypedDict-like schemas, provider-native schemas, or all of them?
10. Should high-level `runtime.run(...)` default to strict structured-output retry behavior when validation fails, or fail fast and let the application decide?

## 27. Definition of done for v0.1

v0.1 is complete when:

- The package installs in editable mode.
- All P0 models and protocols exist.
- Echo model provider works.
- Local agent provider works.
- Tool runtime supports context injection.
- Provider registry resolves model and agent providers.
- Runtime streams events and collects text.
- High-level `AgentRuntime.run(...)` contract exists.
- `AgentResult[T]` contract exists.
- Unit tests pass.
- README includes minimal model and local-agent examples.
- No component depends on LiteLLM.
- No component treats chat messages as the internal canonical state.

## 28. Implementation stance

Start fresh, but preserve the soul. The previous library validated useful ideas: complete blackbox tool loops, context injection, payload separation, local tool orchestration, structured Pydantic output, dynamic tool loading, mockability, and multi-provider routing. Those ideas should be ported selectively.

The old architecture should not be preserved as the core because it centers on a shared chat-message loop. The old product promise should be preserved: users should be able to give the library a task, tools, context, and output schema, then receive a final validated result without implementing the loop themselves.

The new product should be optimized for the agent era:

```text
sessions > generate calls
events > chunks
provider state > chat history
artifacts > final strings
approvals > opaque callbacks
cloud agents + local tools > local tools only
```

The new scaffold should therefore avoid two failure modes:

- becoming only a low-level provider adapter kit,
- rebuilding the old local-only loop without modern provider-native state, events, approvals, artifacts, MCP, and cloud-agent sessions.

The target is both:

```text
blackbox task runner for everyday use
provider-native supervision runtime for advanced use
```
