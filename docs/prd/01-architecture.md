# 01 — Architecture

How the runtime is put together: the two protocols, the facades, the shared
execution loop, the core data contracts, the event taxonomy, and the
non-negotiable constraints that keep them honest.

## 1. Architecture in five points

1. **Two protocols, not one.** `ModelProvider` runs a single model turn
   (`stream_turn`); `AgentProvider` runs sessions
   (`create_agent`/`start_session`/`stream_events`/…). Cloud agents are **not**
   model calls with more tools — these surfaces stay separate. See
   [03](03-model-providers.md) and [04](04-agent-providers.md).
2. **`AgentRuntime` exposes eight facades plus the high-level path.** (§3.)
3. **`AgentLoop` is the shared execution layer.** Both `LocalAgentProvider` and
   `AgentRuntime.run` delegate to it. New tool/approval/policy semantics belong
   here, not duplicated across providers. (`src/blackbox/runtime/agent_loop.py`;
   `loop.py` and `hosted_tools.py` are compatibility shims — don't add logic
   there.)
4. **Events are the runtime stream.** `AgentEvent` carries a `run_id` (UUID per
   `run`/`stream` invocation) and a monotonic `sequence`. ~148 event-type
   constants in `core/events.py` cover run/session lifecycle, model turns, tool
   calls (local, hosted, agent, MCP), routing/search, prompt planning, MCP
   lifecycle/trust, workspace ops, approvals, handoffs, guardrails, retries,
   evals, cloud-agent status, and realtime. Every event flows through
   `runtime.event_store` (an `EventStore` Protocol; in-memory by default).
5. **`ProviderState` is provider-native, not chat history.** Adapters preserve
   native continuation IDs (`previous_response_id`, conversation IDs,
   response-output items, Anthropic tool/MCP state, Gemini grounding/file
   metadata) inside `ProviderState.tool_state`. Never reduce this to a
   chat-message transcript in the core runtime.

## 2. Conceptual architecture

```text
Application
  |
  v
AgentRuntime
  |-------------------------------|-------------------------------|
  v                               v                               v
AgentLoop / TaskRunner            models facade                   agents facade
  |                               |                               |
  |                               v                               v
  |                           ModelProvider                   AgentProvider
  |                               |                               |-- cloud agent session
  |-- autonomous tool loop        |-- model turn                  |-- local agent session
  |-- local/MCP/hosted dispatch   |-- streamed events             |-- managed environment
  |-- structured output           |-- provider state              |-- artifacts/approvals/logs
  |-- AgentResult[T]              |-- hosted tools / MCP / reasoning
  |
  |-- workspace package contracts (connectors, schedules, permissions, publication)
  |-- workspace files / commands / patches / snapshots
```

## 3. AgentRuntime: facades plus the high-level path

The product has two public levels.

**High-level (the blackbox surface):**
- `runtime.run(...)` / `runtime.stream(...)` — the task runner; returns
  `AgentResult[T]`. See [02](02-blackbox-loop.md).

**Eight provider-native supervision facades:**

| Facade | Purpose | Feature doc |
|---|---|---|
| `runtime.tools` | Local tool registry (`register`, `get`, `all_tools`, `to_provider_tools`, `call(..., mock=…)`, `session()`). | [05](05-tools.md) |
| `runtime.models` | Direct model turns. | [03](03-model-providers.md) |
| `runtime.agents` | Agent session lifecycle (cloud or local). | [04](04-agent-providers.md) |
| `runtime.workspaces` | Workspace provider registry/lifecycle. | [07](07-workspaces.md) |
| `runtime.realtime` | Realtime/voice session connect, mutate, close. | [12](12-realtime.md) |
| `runtime.prompts` | Prompt dry-run composition (returns a `PromptBundle`, no model call). | [10](10-structured-output.md) |
| `runtime.caches` | Provider cache lifecycle helpers. | — |
| `runtime.chat` | Explicit chat-shaped compatibility facade (export only; never the runtime's truth). | — |

Adding a *new* top-level facade is a "ask first" change. Prefer extending an
existing facade or adding a `RuntimeConfig`-backed surface. See
[13](13-configuration.md).

## 4. Core data contracts

These are load-bearing; renaming or repurposing them requires asking first.

### AgentEvent
The main stream object. Fields: `id`, `type` (canonical string), `session_id?`,
`provider`, `item_id?`, `data` (normalized payload), `raw` (provider-native
payload), `timestamp`, plus `run_id` and `sequence` for correlation. Update
frozen events with `replace(event, ...)`; never mutate.

### RunItem
Durable items created during a run/session: message, reasoning, function call,
function result, hosted tool call/result, MCP list-tools, MCP call, approval
request, workspace file change, patch artifact.

### ProviderState
Preserves native continuation: `provider`, `conversation_id`,
`previous_response_id`, `native_history`, `reasoning_state`, `tool_state`,
`continuation`. Adapters may extend `continuation`/`tool_state`.

### AgentSession
Statuses: `created`, `running`, `waiting`, `completed`, `failed`, `cancelled`.
`waiting` covers approvals, custom tool results, user clarification, and
provider-side pauses.

### Artifact
Types include `file`, `patch`, `diff`, `log`, `report`, `command_output`,
`workspace_snapshot`, `deployment`, `evaluation`. Each carries metadata and may
include provider-native references. `ArtifactPage` paginates listing.

### AgentResult[T] / AgentSessionResult[T]
The collected outputs of `runtime.run(...)` and `runtime.agents.run(...)`
respectively. See [02](02-blackbox-loop.md) and [04](04-agent-providers.md).

## 5. Canonical event taxonomy

Constants live in `EventTypes` in `core/events.py`. New event types go there, not
invented per-adapter. Shape is `<domain>.<verb>`.

| Domain | Examples |
|---|---|
| Run/session | `run.started`, `run.completed`, `session.created`, `session.started`, `session.completed`, `session.failed`, `session.cancelled` |
| Model | `model.request.started`, `model.item.created`, `model.text.delta`, `model.reasoning.delta`, `model.completed` |
| Tools | `tool.call.requested`, `tool.call.started`, `tool.call.completed`, `tool.call.failed` |
| Tool search | `tool_search.requested`, `tool_search.completed` |
| MCP | `mcp.list_tools.completed`, `mcp.approval.required`, `mcp.call.started`, `mcp.call.completed`, `mcp.trust.evaluated` |
| Cloud agents | `cloud_agent.status.changed`, `cloud_agent.log`, `cloud_agent.checkpoint.created` |
| Workspace | `workspace.file.read`, `workspace.file.changed`, `workspace.command.started`, `workspace.command.completed`, `workspace.patch.created` |
| Approval | `approval.requested`, `approval.approved`, `approval.denied` |
| Artifacts | `artifact.created`, `artifact.updated` |
| Prompt | `prompt.bundle.created` |
| Realtime | `realtime.turn.completed`, … (~50 constants in the realtime family) |
| Evaluation | `eval.started`, `eval.completed` |

Reserved for future delegation work: `handoff.requested/.started/.completed/.failed`
and `agent_tool.call.started/.completed` (P2). See [04](04-agent-providers.md).

## 6. Error hierarchy

All exceptions descend from `AgentRuntimeError` (`core/errors.py`). Use the
closest-matching subclass: `ConfigurationError`, `ProviderNotFoundError`,
`ProviderNotConfiguredError`, `ProviderExecutionError`, `ToolExecutionError`,
`ApprovalError`, `SessionError`, `OutputValidationError`, `CapabilityError`,
`UnsupportedFeatureError`, `ArtifactError`, `WorkspaceError`, `MCPError`. Don't
add bare `Exception` subclasses. Adapters must not leak secrets in messages.

## 7. Package layout

```text
src/blackbox/
  core/             # events, items, state, sessions, capabilities, artifacts, approvals, accounting, cache, content, raw, errors
  providers/        # ModelProvider/AgentProvider protocols, registry, request contracts, adapters
    model_adapters/ # OpenAI Responses, Anthropic Messages, Gemini GenerateContent, xAI, Echo
    agent_adapters/ # Local, OpenAI Cloud, Claude Code, Vertex AI Agent Engine
  runtime/          # AgentRuntime, AgentLoop, facades, RuntimeConfig, WorkflowProfile, RiskyActionApprovalPolicy
  planning/         # ResolvedRunSpec, prompt fragments, PromptComposer, parity checks
  tools/            # local registry, ToolRegistry, ToolDefinition, ToolSession; hosted/ provider-native specs
  mcp/              # MCPServerSpec, MCPClient, transports, MCPToolset, trust policies, risk profiles
  workspaces/       # WorkspaceProvider backends (local, git, sandbox, Docker, cloud)
  workspace_agents/ # WorkspaceAgentSpec — packaged/governed agent contracts
  workers/          # inbound work: WorkSource, EnvironmentWorker
  output/           # JSON-Schema conversion + validation helpers
  observability/    # traces, sinks, OpenTelemetry, replay/diff, evals, presets
  realtime/         # RealtimeProvider, RealtimeRuntime, ManagedRealtimeSession
  integrations/     # optional third-party integration builders
  compat/, models/, agents/   # migration shims and compatibility namespaces
```

Every domain package carries a `README.md` with "Belongs Here / Does Not Belong
Here / File Map" — read it before adding code in a package you don't own.

## 8. Hard constraints (architectural)

These have explicit decisions behind them and are easy to violate by accident:

- **No LiteLLM dependency, ever.** Routing is a tiny in-house `ProviderRegistry`.
- **Chat messages are a compatibility export, not canonical state.** No internal
  path may treat alternating user/assistant messages as the source of truth.
- **Raw provider payloads must be preserved** (`raw=<sdk_object>`; `RawEnvelope`
  when sensitivity tagging matters). Production observability *redacts* at the
  trace layer rather than stripping at the source.
- **Hosted/provider tools are not fake local tools.** Local Python, hosted, MCP
  (local-routed and provider-native `RemoteMCP`), cloud-agent, and workspace
  tools are distinct backends.
- **Capabilities tell the truth.** `supports_X=False` ⇒ calling that op raises a
  typed runtime error before SDK dispatch. Contract test:
  `tests/contracts/test_capability_honesty.py`.
- **MCP trust is enforced before tool exposure.** See [06](06-mcp.md).
- **The blackbox loop must keep working.** Any change forcing callers to manually
  parse tool calls or feed results back is a product regression.

## 9. Conventions

- **Routing format:** `provider:model` is canonical (`openai:gpt-5.5`). `/` still
  works but collides with namespaced agent paths
  (`vertex-agent-engine/projects/foo/agent`); use `:` in new examples.
- **Style:** type hints on every public function (`mypy --strict`); dataclasses
  with `slots=True` for value types, frozen for ID/ref/config objects; async
  generators for streaming; stdlib → typing → `blackbox.*` imports; `__all__`
  alphabetically sorted (RUF022).
- **Naming:** `ModelProvider` adapters `<Vendor><Surface>Provider`;
  `AgentProvider` adapters `<Surface>AgentProvider`.

→ Next: [02 — The blackbox loop](02-blackbox-loop.md)
