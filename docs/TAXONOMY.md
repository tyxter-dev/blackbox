# Blackbox Taxonomy

Canonical definitions for the concepts in this project. Many blackbox concepts
are deliberately adjacent (turn/run/session, event/item/artifact, four kinds of
"catalog", three ways MCP appears). This document is the tie-breaker: when
prose, docstrings, or new code need a word, use the word defined here, with the
qualifiers prescribed here.

Conventions used below:

- **Term** — the canonical name. Code symbols are shown in backticks.
- *Is not* — the adjacent concept it is most often confused with.
- Terms marked ⚠ have a known naming hazard, collected in
  [Known naming collisions](#known-naming-collisions).

---

## 1. Quick disambiguation table

| If you mean... | Say | Not |
|---|---|---|
| One model request/response cycle | **turn** | run, call |
| One `runtime.run(...)` invocation (the whole loop) | **run** | session, task |
| A long-lived provider-managed work unit | **session** | run, conversation |
| One user-triggered message/task inside a session | **invocation** | turn, run |
| The orchestration algorithm inside a run | **the (blackbox) loop** / `AgentLoop` | runtime |
| Something that happened, in order | **event** (`AgentEvent`) | item, log |
| A durable noun a run produced/consumed | **run item** (`RunItem`) | event |
| A file-like output | **artifact** | item, result |
| Provider-native continuation data | **provider state** (`ProviderState`) | history, transcript |
| A Python callable the runtime executes | **local tool** | tool, function |
| A tool executed on the provider's infrastructure | **hosted tool** | remote tool, native tool |
| A tool discovered from an MCP server | **MCP tool** | hosted tool |
| What a provider/model *can* do | **capability profile** | profile |
| A preset of run arguments the app *wants* | **workflow profile** | profile, config |
| Model identity/lifecycle metadata | **provider model catalog** | model catalog |
| Pricing and billing data | **pricing catalog** (`ModelCatalog`) | model catalog |
| The place where a coding agent works (files, commands) | **execution workspace** | workspace |
| A portable governed agent package | **workspace agent package** (`WorkspaceAgentSpec`) | agent, workspace |

---

## 2. Execution surfaces

**Runtime** (`AgentRuntime`) — the composition root. Owns the provider
registry, tool registry, stores, catalogs, and observability wiring. Its
`run(...)`/`stream(...)` methods are the high-level blackbox loop.
*Is not* a provider: the runtime never talks to a vendor SDK directly.

**Facade** (`runtime.models`, `runtime.agents`, `runtime.workspaces`,
`runtime.realtime`, `runtime.chat`, `runtime.tools`, `runtime.caches`,
`runtime.prompts`) — a domain-scoped sub-API of the runtime. Facades are
supervision surfaces: lower-level than `runtime.run`, higher-level than a
provider. *Is not* a provider or a registry; a facade routes through both.

**Provider** — a *role contract* (a `Protocol`) for an execution surface:

- `ModelProvider` — runs **turns** (`stream_turn(TurnRequest)`).
- `AgentProvider` — runs **sessions** (`start_session`, `stream_events`,
  `send_message`, `approve`, `cancel`, `list_artifacts`).
- `WorkspaceProvider` — runs **workspace operations** (files, commands,
  patches, snapshots).
- `RealtimeProvider` — runs **realtime sessions** (bidirectional audio/text).
- `AgentWebhookProvider` — optional extension: verified webhook ingress for
  cloud agents.

**Adapter** — a concrete implementation of a provider protocol that maps one
vendor SDK/API to the contract (`providers/model_adapters/...`,
`providers/agent_adapters/...`). "Provider" names the role; "adapter" names
the implementation. `OpenAIResponsesProvider` is an adapter that fills the
`ModelProvider` role.

**Registry** (`ProviderRegistry`) — name → provider-instance routing. Resolves
`ProviderRef` strings like `"openai:gpt-5.4"` into a registered adapter plus a
model id. *Is not* a catalog: registries hold live objects; catalogs hold
metadata.

---

## 3. Units of execution

Ordered smallest to largest:

**Turn** — one model request/response cycle. Input: `TurnRequest` (model,
input, provider state, tools, controls). Output: an event stream collected
into a `TurnResult`. A turn never dispatches local tools; it only *requests*
them. Surface: `ModelProvider.stream_turn`, `runtime.models.run`.

**Loop** (`AgentLoop`, "the blackbox loop") — the orchestration algorithm:
model turn → detect tool calls → dispatch local tools → feed results back →
repeat → terminate at final output, failure, cancellation, approval pause, or
`max_iterations`. The loop is an *algorithm*, not a unit of identity — it has
no id of its own.

**Run** — one `runtime.run(...)` / `runtime.stream(...)` invocation: one
execution of the loop, comprising one or more turns and tool dispatches.
Mints a `run_id`, stamps it on every event, and ends in an `AgentResult[T]`.
*Is not* a session: a run is bounded and owned by the caller's process; a
session is open-ended and owned by a provider.

**Session** (`AgentSession`) — a long-lived, provider-managed work unit under
an `AgentProvider`, with a status lifecycle (`created` → `running` /
`waiting` → `completed` / `failed` / `cancelled`). Referenced by
`SessionRef`. Collected form: `AgentSessionResult`.
⚠ "Session" is overloaded; see [collisions](#known-naming-collisions).

**Invocation** (`InvocationRef`) — one user-triggered unit *inside* a session:
each `send_message` call or follow-up task. A session hosts many invocations.
*Is not* a turn: a provider may run many internal turns per invocation.

**Task** (`TaskSpec`) — the work statement submitted to an `AgentProvider` to
start a session: prompt, optional model, workspace, inputs, hosted tools,
response spec. A task describes intent; the session is its execution.

**Scheduled run** (`ScheduledRunRef`) — an execution of a workspace agent
package triggered by a `ScheduleSpec`/`ScheduleTrigger` rather than a caller.

---

## 4. Data: events, items, artifacts, content

Rule of thumb: **events are verbs, items are nouns, artifacts are files,
state is how to continue.**

**Event** (`AgentEvent`) — a normalized, ordered, ephemeral stream element:
"this happened." Carries correlation (`run_id`, `sequence`, trace/span ids,
provider request/trace ids) and preserves the provider-native payload on
`.raw`. Canonical names live in `EventTypes` (dot-separated
`domain.noun.verb`). Events are the primary public stream type.

**Run item** (`RunItem`) — a durable noun produced or consumed by a run: a
message, reasoning block, function call/result, hosted tool call, MCP call,
approval, workspace change, artifact, or error. Canonical types live in
`ItemTypes`. Items outlive the stream that announced them; events about an
item reference it via `item_id`.

**Artifact** (`Artifact` / `ArtifactRef`) — a file-like output of a run,
session, or workspace, addressed by reference. `ArtifactRef` is the pointer;
`Artifact` is the resolved record. *Is not* a run item — an artifact may be
*described* by an item of type `artifact`.

**Content part** (`ContentPart`: `TextPart`, `ImagePart`, `AudioPart`,
`FilePart`, `VideoFramePart`, `ToolResultPart`, `ProviderNativePart`) — typed
multimodal building blocks for model input/output. `ContentItem` groups parts.

**Raw payload / `RawEnvelope`** — a provider-native payload tagged with
sensitivity and storage metadata so sinks (`RedactingEventSink`) can scrub it.
"Raw" always means *provider-native and unnormalized*.

**`ChatMessage`** — a compatibility projection only (`runtime.chat`,
`blackbox.compat.chat`). Chat messages are an import/export format and never
the runtime's internal truth. *Is not* `AgentMessage`, which is a typed
assistant message inside results (`agent_response_messages`).

---

## 5. State, references, and persistence

**Provider state** (`ProviderState`) — provider-native continuation data:
response ids (`previous_response_id`), conversation ids, native history
objects, thought signatures, tool state. Explicitly *not* a chat transcript.
Owned by adapters; opaque to the loop; round-trips through
`runtime.run(..., provider_state=...)` for resumption.

**Run state** (`RunState`) — the runtime-side snapshot of one run: session id,
provider/model, accumulated `RunItem`s, and the embedded `ProviderState`.
What you persist to a `RunStore` to checkpoint/resume.

**Agent session state** (`AgentSessionState`) — the persistence record for
managed agent sessions: event cursors (`SessionEventCursor`), pending
approvals (`PendingApprovalState`), invocation state. Used by `SessionStore`
implementations.

**Reference types** (`AgentRef`, `SessionRef`, `InvocationRef`,
`WorkspaceRef`-style handles, `ArtifactRef`, `ScheduledRunRef`) — stable,
serializable pointers into a provider's world. Refs never hold live
resources; they are safe to store and pass across processes.

**Stores** — pluggable persistence protocols and their implementations:
`EventStore` (`JSONLEventStore`), `RunStore` (`SQLiteRunStore`),
`SessionStore` (`InMemory`/`JSONL`/`SQLiteSessionStore`),
`ProviderCacheStore` (`InMemory`/`SQLiteProviderCacheStore`). A store persists;
a registry routes; a catalog describes.

---

## 6. Tools

The densest cluster. Four axes matter: **where it executes**, **where it is
declared**, **how it is selected**, and **what it returns**.

### 6.1 Tool kinds (where it executes)

**Local tool** (`ToolDefinition`) — a Python callable registered with the
runtime and executed in-process by `ToolRuntime` (timeouts, concurrency caps,
context injection, blocking offload).

**Hosted tool** (`HostedToolSpec`: `WebSearch`, `FileSearch`,
`CodeInterpreter`, `ImageGeneration`, `ComputerUse`, `RemoteMCP`, ...) — a
tool executed on the *provider's* infrastructure, declared via typed specs in
`hosted_tools=[...]` and mapped by adapters to native tool configuration.
Hosted tools are never registered as local callables. `HostedToolRaw` is the
escape hatch for unmapped provider payloads.

**Client-executed hosted tool** (`Shell`, `ApplyPatch`, `TextEditor`,
`ComputerUse` + `HostedToolHandlers`) — a hosted-tool *protocol* where the
model requests the action through provider-native plumbing but the
*application* executes it via a registered handler. Hybrid of the two above.

**MCP tool** — a tool discovered from an MCP server. Not a kind of execution
by itself: it is *routed* either into local dispatch (the runtime calls the
server) or into provider-native remote MCP (the provider calls the server).
See [§7](#7-mcp).

### 6.2 Tool containers (where it is declared)

**`ToolRegistry`** — the global named registry (`runtime.tools.register`).
**`ToolSession`** — a run-scoped overlay registry seeded from the global one;
temporary registrations do not leak back.
**`Toolset`** — a collection routed *as a unit*, supporting deferred/dynamic
loading (`search_tools`/`load_tools`) for large catalogs. `MCPToolset` is the
MCP-backed variant.
**`ToolCatalog`** — a searchable *metadata index* over tools (relevance
scoring). A catalog describes; a registry resolves; a session scopes; a
toolset routes.

### 6.3 Tool selection (how it is chosen) ⚠

Four similarly-named things, kept distinct:

- **`ToolSearch`** (hosted spec) — exposes searchable tool *namespaces* to the
  provider as a hosted tool.
- **`ToolSearchControl`** (request control) — tunes *provider-side* tool
  search behavior for a turn.
- **Tool routing** (`ToolRoutingSpec`, `ToolCandidate`, `ResolvedToolPlan`,
  `ToolSelector`) — *runtime-side* selection of which tools a turn sees.
- **`ToolCatalog` search** — plain application-side lookup; no model involved.

### 6.4 Tool results (what it returns)

**`ToolResult`** — `content` goes back to the model; `payload` is collected
for the application (`AgentResult.payloads`, the *deferred payload pattern*).
Non-`ToolResult` returns are coerced.

---

## 7. MCP

MCP appears in exactly three ways. Name which one you mean:

1. **Local MCP dispatch** — `MCPConnector` manages the transport (stdio,
   streamable HTTP), lists/calls tools, and exposes them as namespaced local
   tools (`mcp:server.tool`). The runtime is the MCP client.
2. **Provider-native remote MCP** — the *provider* is the MCP client. Declared
   per-turn with the `RemoteMCP` hosted-tool spec, or chosen by routing.
3. **Packaged MCP** — `MCPServerSpec`/`MCPToolset` entries serialized inside a
   `WorkspaceAgentSpec`, resolved into (1) or (2) at run time.

Core vocabulary:

- **`MCPServerSpec`** — how to *connect* (transport, url/command, allowed
  tools, approval mode). A connection description, not a tool.
- **`MCPToolset`** — a server spec plus *routing preference*
  (`mode="local" | "provider_native" | "auto"`). `auto` keeps stdio and
  private URLs local and goes provider-native only when the capability
  profile supports it (`MCPRouteMode`).
- **Trust** (`MCPServerTrustPolicy`, `MCPToolTrustPolicy`, `MCPTrustLevel`,
  `MCPTrustDecision`, `MCPTaint`, `trust_fingerprint`) — policy gating around
  servers and tools, distinct from **approval** (per-call human consent,
  `MCPApprovalMode`, `ApprovalRequest`/`ApprovalDecision`).

---

## 8. Workspaces and agent packages ⚠

"Workspace" is the most overloaded word in the project. Two senses:

### 8.1 Execution workspace (where an agent works)

The filesystem/sandbox context for coding-style agents: files, commands,
patches, tests, snapshots, ports, artifacts.

- **`WorkspaceSpec`** — how to *open* one (`local`, `git`, docker/sandbox,
  cloud ref). A description.
- **Workspace handle / ref** — the opened workspace returned by
  `runtime.workspaces.open(...)`; what `TaskSpec.workspace` carries.
- **`WorkspaceProvider`** — the protocol (local, git, Docker sandbox, cloud
  implementations).
- **`WorkspaceRuntime`** — the direct local implementation/orchestration API.
- **`WorkspaceRuntimeFacade`** — `runtime.workspaces`, the runtime-integrated
  surface.

### 8.2 Workspace agent package (how a governed agent is distributed)

Here "workspace" means the *organizational* workspace a packaged agent is
distributed into — not a filesystem. Prefer the full phrase **workspace agent
package** in prose.

- **`WorkspaceAgentSpec`** — the portable package: instructions,
  model/provider preference, tools, hosted tools, MCP servers/toolsets,
  connectors, permissions, schedules, skills, memory policy, publication and
  version metadata. The unit of distribution and governance.
- Satellite specs: `ConnectorSpec` (external account/integration),
  `ToolPermission` (scoped grant binding a tool ref to a connector),
  `ApprovalRequirement`, `ScheduleSpec`/`ScheduleTrigger`, `SkillBundleRef`,
  `MemorySpec`, `PublicationSpec`, `WorkspaceAgentVersion`,
  `WorkspaceAgentMetadata`.
- **`WorkspaceAgentRegistry`** — where packages are registered/looked up.
- **`run_workspace_agent(...)`** — the bridge that expands a package into a
  normal `runtime.run(...)`.

### 8.3 The three "agent definition" specs

| Spec | Level | Owner | Used for |
|---|---|---|---|
| `WorkspaceAgentSpec` | Distribution/governance | Application/org | Packaging a governed agent with permissions, schedules, publication |
| `AgentSpec` | Provider | `AgentProvider.create_agent` | Materializing an agent inside a provider (local or cloud) |
| `TaskSpec` | Invocation | `AgentProvider.start_session` | One task submitted to an agent |

A `WorkspaceAgentSpec` may be *lowered* into an `AgentSpec` or a plain
`runtime.run` call; never use the three interchangeably.

---

## 9. Configuration, capabilities, and controls ⚠

Direction of fit distinguishes them:

**Capability profile** (`ModelCapabilities`, `ModelCapabilityProfile`,
`AgentCapabilities`, `RealtimeCapabilityProfile`, `HostedToolSupport`,
`CapabilityDetail`, `CapabilityConstraint`) — what a provider/model **can**
do. Mind-to-world: describes reality. Powers pre-flight validation
(`UnsupportedFeatureError`).

**Workflow profile** (`RuntimeConfig`, `WorkflowProfile`,
`RuntimeConfig.profile("coding_agent")`) — what the application **wants**: a
typed preset that expands into `runtime.run(...)` keyword arguments.
World-to-mind: describes intent.

Always qualify the bare word "profile" with *capability* or *workflow*.

**Controls** (`ModelRequestControls` and its members `ModelCacheControl`,
`ToolSearchControl`, `CompactionControl`) — per-turn request knobs mapped by
adapters to provider-native fields. Controls configure *one turn*; workflow
profiles configure *a run*; capability profiles configure *nothing* (they
only validate).

---

## 10. Output handling

**`output_type=Cls`** — the shortcut: validate final text into a
Pydantic model, dataclass, or `str`, using the default fail-fast
`posthoc_parse` strategy.

**`OutputSpec`** — the full contract: `schema` + `strategy` +
`max_validation_retries`. Strategies:

- `provider_native` — provider enforces the JSON schema in-band.
- `finalizer_tool` — runtime injects a hidden `submit_final_output` tool and
  validates its arguments.
- `posthoc_parse` — parse/validate final text, fail fast.
- `posthoc_parse_with_retry` — on failure, feed a repair prompt back to the
  model.

**`OutputSchema`** — the normalized schema representation adapters consume.
*Is not* `OutputSpec`: the spec carries policy; the schema carries shape.

---

## 11. Catalogs and accounting ⚠

Four "catalogs", three of them about models:

| Catalog | Symbol | Holds | Monetary? |
|---|---|---|---|
| Provider model catalog | `ProviderModelCatalog` / `runtime.provider_model_catalog` | Identity: aliases, lifecycle, modalities, capacity hints, source URLs | No |
| Pricing catalog | `ModelCatalog` / `runtime.model_catalog` | `ModelPricing`, billing policies (`MarkupPolicy`) | Yes |
| Tool catalog | `ToolCatalog` | Searchable tool metadata | No |
| Bundled catalogs | `bundled_provider_model_catalog`, `bundled_provider_pricing` | Versioned seed data | — |

Accounting vocabulary, in precedence order:

- **Usage** (`ModelUsage`, `result.metadata["usage"]`) — token counts the
  provider reported. Facts.
- **Provider cost** (`result.metadata["provider_cost"]`; legacy alias
  `cost`) — estimated spend, resolved from user-registered pricing, then
  bundled pricing, else absent. An estimate, not an invoice.
- **Billable** (`result.metadata["billable"]`) — what the application charges
  *its* users: user billable pricing, else markup policy over provider cost.
- **Accounting** (`result.metadata["accounting"]`) — the grouped view.

---

## 12. Observability

**Event correlation** — every runtime-stamped event carries `run_id` +
`sequence` (replay/audit identity) and `trace_id`/`span_id`/`parent_span_id`
(workflow trace; default trace id = run id). Provider-native ids
(`provider_request_id`, `provider_trace_id`) are preserved *alongside*, never
*as*, runtime ids.

**Trace / span** — reconstructed from events (`trace_from_events`); exported
via `OpenTelemetryTraceExporter`. Span vocabulary: `agent.run`, `model.turn`,
`tool.call`, `mcp.call_tool`, `workspace.command`, `approval.wait`, etc.

**Replay** (`replay_run`) — reconstructing a persisted run from its event log
without calling a model. *Is not* resume: replay re-reads the past; resume
(`provider_state`) continues into the future.

**Eval** (`evaluate_trace`, `eval.*` events) — scoring a reconstructed trace.

**Preset** (`ObservabilityPreset`) — one-switch wiring of trace/metric/log
backends with redaction defaults.

---

## 13. Test taxonomy

| Suite | Network | Selected by default | Purpose |
|---|---|---|---|
| `tests/unit` | No | Yes | Single-module behavior |
| `tests/contracts` | No | Yes | Protocol conformance |
| `tests/golden` | No | Yes | Provider event-mapping fixtures (fake clients) |
| `tests/runtime` | No | Yes | Loop/facade behavior |
| `tests/perf` | No | Yes | Offline benchmark smoke |
| `tests/integration` | Yes | **No** (deselected at collection) | Live provider smoke, marker-gated (`integration_*`) |
| `tests/journey` | Yes | **No** (deselected at collection) | Live goal-oriented report-producing journeys (`journey_*`) |

**Golden test** — asserts exact normalized output for recorded/fake provider
payloads. **Journey test** — live, intentionally not assertion-driven; emits a
reviewable report. **Integration test** — live, assertion-driven smoke.

---

## Known naming collisions

Documented hazards to resolve (or at least not worsen):

1. **`ToolBudget` × 2** — `blackbox.tools.toolsets.ToolBudget` (runtime
   visibility/call limits; the public export) vs
   `blackbox.tools.routing.ToolBudget` (selection-time limits inside
   `ToolRoutingSpec`). Different fields, same name. When writing prose, say
   *toolset budget* vs *routing budget*. Candidate fix: rename the routing one
   to `ToolRoutingBudget`.
2. **"profile"** — capability profile (`ModelCapabilityProfile`) vs workflow
   profile (`RuntimeConfig.profile`). Always qualify.
3. **"workspace"** — execution workspace (files/sandbox, §8.1) vs the
   organizational workspace implied by *workspace agent package* (§8.2).
   Always qualify in prose.
4. **`ModelCatalog` vs `ProviderModelCatalog`** — pricing vs identity. The
   monetary one has the *less* specific name. When in doubt, say *pricing
   catalog* and *provider model catalog*.
5. **"session"** — `AgentSession` (provider work unit) vs `ToolSession`
   (scoped tool registry) vs `ManagedRealtimeSession` (realtime connection)
   vs `RunState.session_id` (minted even for plain runs with no agent
   session). Default meaning is `AgentSession`; qualify the others as *tool
   session*, *realtime session*, *run session id*.
6. **`blackbox.models` / `blackbox.agents`** — compatibility namespaces for
   old imports, not the model/agent domains (those live under
   `blackbox.providers.*` and the `runtime.models`/`runtime.agents` facades).
7. **Repository directory name** — `agent_runtime` is a legacy local name;
   the canonical project/package name is `blackbox`.
