# AGENTS.md

Project-specific guidance for coding agents working on `blackbox`.

## What this project is

`blackbox` is the successor in spirit to `llm_factory_toolkit`, not a mechanical rewrite of it.

The original v1 product promise is still load-bearing:

```text
developer gives input + tools + output schema
runtime runs the model/tool loop internally
runtime executes allowed tools and feeds results back
runtime stops only at final output, failure, cancellation, or approval pause
developer receives a validated AgentResult[T]
```

That blackbox loop is the product soul. Users should not have to parse provider tool calls, dispatch Python functions, append tool results, juggle continuation IDs, or hand-validate JSON just to complete a normal agent task.

The runtime now spans direct model turns, local Python tools, hosted provider tools, MCP servers, workspaces, approvals, artifacts, provider-native state, cloud-managed agent sessions, and realtime audio/voice — all without flattening anything into chat messages.

The product has two public levels:

- `runtime.run(...)` / `runtime.stream(...)`: the high-level task runner for everyday use, returning `AgentResult[T]`.
- The facade APIs (`runtime.models`, `runtime.agents`, `runtime.workspaces`, `runtime.realtime`, `runtime.prompts`, `runtime.caches`, `runtime.tools`, `runtime.chat`): lower-level provider-native supervision surfaces.

It is event-first, session-first, state-first, provider-native, and still centered on the complete agent loop.

## Source of truth

Read these before making non-trivial changes:

- `docs/PRD.md` — the contract. Sections most worth re-reading: §4 design principles, §7.1 MVP scope, §8.4 AgentLoop, §10.7 AgentResult, §11 event taxonomy, §12.1–§12.3 P0/P1/P2 requirements, §22 milestones.
- `FEATURES.md` — feature catalog with status per behavior (Supported / Partial / Contract only / Not supported yet). This is the canonical "what works today" inventory.
- `CHANGELOG.md` — slice history (one entry per slice, semver-ish).
- `tests/VALIDATION.md` — coverage tracker mapping behaviors to the tests that prove them.
- `README.md` — public-facing API surface and package layout.
- `src/blackbox/<package>/README.md` — every domain package has a "Belongs Here / Does Not Belong Here / File Map" README. Read the relevant one before adding code in a package you don't already own.
- Architecture notes: `docs/ORGANIZATION_PLAN.md`, `docs/AGENT.md`, `docs/MODEL.md`, `docs/WORKSPACE.md`, `docs/WORKFLOW_PROFILES.md`.

The PRD is updated when product direction shifts; ask before making sweeping PRD edits. `FEATURES.md` should be kept honest as features land.

## Quick commands

```bash
pip install -e .[dev]                  # editable install with pytest, ruff, mypy
pytest -q                              # offline suite (default)
pytest -q tests/unit tests/runtime tests/contracts tests/golden
pytest -m integration_openai           # network-gated; needs OPENAI_API_KEY
ruff check src tests examples
mypy src                               # strict mode is configured in pyproject

python examples/echo_run.py
python examples/local_agent_with_tool.py
python examples/run_with_typed_output.py
python examples/launchmybakery.py      # hosted tools and MCP integrations
```

All three quality gates (pytest / ruff / mypy --strict) must pass before committing. The focused structural gate is `pytest -q tests/unit tests/runtime tests/contracts tests/golden`; current baseline is **479 passing + 1 skipped**. Broader integration suites (`tests/integration/*`) and report-oriented journey tests (`tests/journey/*`) are network-gated and need provider credentials.

## Package layout

```
src/blackbox/
  core/             # events, items, state, sessions, capabilities, artifacts, approvals, accounting, cache, content
  providers/        # ModelProvider/AgentProvider protocols, registry, request contracts, and adapters
    model_adapters/ # OpenAI Responses, Anthropic Messages, Gemini GenerateContent, xAI, Echo
    agent_adapters/ # Local, OpenAI Cloud, Claude Code, Vertex AI Agent Engine
  runtime/          # AgentRuntime, AgentLoop, facades, RuntimeConfig, WorkflowProfile, RiskyActionApprovalPolicy
  planning/         # ResolvedRunSpec, prompt fragments, PromptComposer, parity checks
  tools/            # local registry, ToolRegistry, ToolDefinition, ToolSession
    hosted/         # provider-native hosted-tool specs (WebSearch, FileSearch, Memory, RemoteMCP, ...)
  mcp/              # MCPServerSpec, MCPClient, transports, MCPToolset, trust policies, risk profiles
  workspaces/       # WorkspaceProvider backends (local, git, sandbox, Docker, cloud)
  workspace_agents/ # WorkspaceAgentSpec — packaged/governed agent contracts (connectors, schedules, permissions)
  output/           # JSON-Schema conversion + validation helpers for structured output strategies
  observability/    # traces, sinks, OpenTelemetry, replay/diff, evals, ObservabilityPreset
  realtime/         # RealtimeProvider, RealtimeRuntime, ManagedRealtimeSession (voice/audio sessions)
  integrations/     # optional third-party integration builders (OpenAI vector stores, etc.)
  compat/           # migration shims (ChatMessage, default-provider registration)
  models/, agents/  # compatibility namespaces for older imports
```

## Architecture in five points

1. **Two protocols, not one.** `ModelProvider` runs a single model turn (`stream_turn`); `AgentProvider` runs sessions (`create_agent`/`start_session`/`stream_events`/...). Cloud agents are NOT model calls with more tools — keep these surfaces separate.

2. **`AgentRuntime` exposes eight facades plus the high-level path.**
   - `runtime.run(...)` / `runtime.stream(...)` — the blackbox surface; collects from `stream`.
   - `runtime.tools` — local tool registry facade (`register`, `get`, `all_tools`, `to_provider_tools`, `call(..., mock=...)`, `session()`).
   - `runtime.models` — direct model turns.
   - `runtime.agents` — agent session lifecycle (cloud or local).
   - `runtime.workspaces` — workspace provider registry/lifecycle (local, git, sandbox, Docker, cloud).
   - `runtime.realtime` — realtime/voice session connect, mutate, and close.
   - `runtime.prompts` — prompt dry-run composition (returns a `PromptBundle` without invoking a model).
   - `runtime.caches` — provider cache lifecycle helpers.
   - `runtime.chat` — explicit chat-shaped compatibility facade (export only; never the runtime's truth).

3. **`AgentLoop` (in `src/blackbox/runtime/agent_loop.py`) is the shared execution layer.** Both `LocalAgentProvider` and the high-level `AgentRuntime.run` delegate to it. New tool/approval/policy semantics belong here, not duplicated across providers. `src/blackbox/loop.py` and `src/blackbox/hosted_tools.py` are compatibility shims — don't add logic there.

4. **Events are the runtime stream.** `AgentEvent` carries `run_id` (UUID per `runtime.run/.stream` invocation) and a monotonic `sequence`. There are now ~148 event-type constants in `core/events.py` covering: run/session lifecycle, model turns, tool calls (local, hosted, agent, MCP), tool routing/search/choice, prompt planning, MCP server lifecycle and trust, workspace operations, approvals, handoffs, guardrails, retries, evals, cloud-agent status, and realtime audio/turn events. Every event flows through `runtime.event_store` (an `EventStore` Protocol; in-memory by default). Text is one projection; structured `output` is another; the events log is the truth.

5. **`ProviderState` is provider-native, not chat history.** Adapters preserve native continuation IDs (`previous_response_id`, conversation IDs, response-output items, Anthropic tool/MCP state, Gemini grounding/file metadata) inside `ProviderState.tool_state`. Never reduce this to a chat-message transcript inside the core runtime.

## Configuration via WorkflowProfile and RuntimeConfig

`RuntimeConfig` is a frozen value object — **not a new facade** — that bundles a `profile_name` plus override kwargs and expands into the same keyword arguments the runtime methods already accept. It can be loaded from a mapping, environment variables, or a JSON/TOML/YAML file.

```python
from blackbox import RuntimeConfig, WorkspaceSpec

config = RuntimeConfig.profile("coding_agent").with_overrides(
    provider="openai:gpt-5.5",
    workspace=WorkspaceSpec.git(url=repo_url, ref="main"),
)
result = await runtime.run(input="Refactor module X", config=config)
```

Built-in profiles (see `docs/WORKFLOW_PROFILES.md` for tradeoffs and required values):

| Profile | What it tunes |
| --- | --- |
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

Explicit method arguments override config values. Profile defaults apply first, file/env values next, then `with_overrides(...)` on top.

## Hard constraints

These are easy to violate by accident and have explicit decisions behind them:

- **No LiteLLM dependency**, ever. Provider routing is a tiny in-house `ProviderRegistry`. Reasoning is in PRD §3.3.
- **Chat messages are a compatibility export, not the canonical state.** No internal code path should treat alternating user/assistant messages as the source of truth.
- **Raw provider payloads must be preserved.** Adapter events carry `raw=<sdk_object>` whenever possible. Use `RawEnvelope` (`core/raw.py`) when sensitivity tagging matters. Production observability presets redact raw payloads at the trace layer instead of stripping them at the source.
- **Hosted/provider tools are not fake local tools.** Local Python tools, hosted provider tools, MCP tools (local-routed and provider-native `RemoteMCP`), cloud-agent tools, and workspace tools are distinct backends. See PRD §14.4.
- **Capabilities tell the truth.** If a provider advertises `supports_X=False`, calling that operation must raise a typed runtime error before SDK dispatch — not silently no-op. There's a contract test in `tests/contracts/test_capability_honesty.py`.
- **MCP trust is enforced before tool exposure.** Servers carry an `MCPServerTrustPolicy` and a computed `MCPServerRiskProfile`; `BLOCKED` servers refuse to start, untrusted servers route through approval, and outputs honor `MCPTaint` redaction. Don't bypass `MCPTrustEvaluator`.
- **The blackbox loop must keep working.** `runtime.run(...)` is the v1 product promise. Any change that requires callers to manually parse tool calls or feed results back is a regression of the product, not an improvement.

## Conventions

### Routing format

`provider:model` is the canonical separator at the high level (`model="openai:gpt-5.5"`). `/` still works for backward compatibility but collides with namespaced agent paths like `vertex-agent-engine/projects/foo/agent`. Use `:` in new examples.

### Event taxonomy

Constants live in `EventTypes` in `core/events.py`. New event types go there, not invented per-adapter. The shape is `<domain>.<verb>` (`tool.call.requested`, `model.text.delta`, `mcp.trust.evaluated`, `realtime.turn.completed`, `prompt.bundle.created`, etc.).

### Error hierarchy

All exceptions descend from `AgentRuntimeError` (`core/errors.py`). Use the closest-matching subclass (`ProviderNotFoundError`, `ProviderNotConfiguredError`, `ProviderExecutionError`, `ToolExecutionError`, `ApprovalError`, `SessionError`, `OutputValidationError`, `CapabilityError`, `UnsupportedFeatureError`, ...). Don't add bare `Exception` subclasses.

### Style

- Type hints required on every public function. `mypy --strict` is enforced.
- Prefer dataclasses with `slots=True` for value types. Frozen dataclasses for ID/ref objects and config objects.
- Async generators for streaming methods. Protocol declarations use plain `def` returning `AsyncIterator[...]`, not `async def`, so async-generator implementations type-check.
- Imports ordered stdlib → typing → `blackbox.*`. Ruff handles ordering; don't fight it.
- `__all__` lists must be alphabetically sorted (ruff `RUF022` is enforced).
- Use `replace(event, ...)` (from `dataclasses`) to update frozen events; never mutate.

### Naming

- `ModelProvider` adapters: `<Vendor><Surface>Provider` (`OpenAIResponsesProvider`, `AnthropicMessagesProvider`, `GeminiGenerateContentProvider`, `XAIResponsesProvider`).
- `AgentProvider` adapters: `<Surface>AgentProvider` (`LocalAgentProvider`, `OpenAICloudAgentProvider`, `ClaudeCodeAgentProvider`, `VertexAIAgentEngineProvider`). The earlier names `AnthropicManagedAgentProvider` and `GoogleAgentPlatformProvider` are still exported as deprecated aliases — don't use them in new code.

## Tools, toolsets, and routing

Three layers, increasing in scope:

- **`ToolRegistry` / `ToolDefinition`** (`tools/registry.py`) — flat callable registration with metadata (category, tags, risk, scopes, side effects, examples). Use `runtime.tools.register(...)` for normal cases.
- **`Toolset`** (`tools/toolsets.py`) — a named, composable group of tools that can be loaded as a unit. Useful for large catalogs that don't all need to be visible on every turn.
- **`ToolRoutingSpec`** (`tools/routing.py`) — runtime-side routing logic: modes are `explicit` / `auto` / `hybrid` / `model_discovery` / `disabled`, with a `ToolBudget` capping max visible tools, schema tokens, MCP/agent/handoff counts. The runtime uses this to decide which tools the model sees on each turn.

Hosted-tool specs (`WebSearch`, `WebFetch`, `FileSearch`, `RemoteMCP`, `Memory`, `TextEditor`, `CodeInterpreter`, `Shell`, `ApplyPatch`, `ImageGeneration`, `ComputerUse`, `URLContext`, `ToolSearch`) live in `tools/hosted/specs.py`. They describe capabilities executed on the provider's servers, not local Python callables.

`runtime.tools.session()` returns a per-run isolated registry seeded from globals — register run-scoped tools without leaking them into later runs.

## MCP guardrails

MCP integration has explicit security boundaries (added in `e6a04c3 feat: add MCP trust and security guardrails`):

- `MCPTrustLevel`: `BLOCKED` / `UNTRUSTED` / `REVIEWED` / `TRUSTED` / `FIRST_PARTY`.
- `MCPCapabilityRisk`: eleven flags (`RUNS_COMMANDS`, `NETWORK_EGRESS`, `SECRET_ACCESS`, `SERVER_INITIATED_SAMPLING`, ...).
- `MCPServerTrustPolicy` / `MCPToolTrustPolicy`: gate which servers/tools may run, route mode (local vs `RemoteMCP`), approval requirement, output redaction.
- `MCPServerRiskProfile`: computed at connection time by `build_server_risk_profile()` — read-only summary the trust evaluator consults.
- `DefaultMCPTrustEvaluator`: conservative default — blocks `BLOCKED`, requires approval for untrusted.
- `MCPToolset` + `runtime.run(..., toolsets=[...])`: routes to local MCP dispatch or provider-native `RemoteMCP` based on resolved policy.

## Realtime sessions

`RealtimeRuntime` (under `runtime.realtime`) manages low-latency audio/voice sessions. The protocol surface is deliberately separate from `ModelProvider` because realtime transports have distinct lifecycle semantics (mutable session config, server-pushed turn detection, audio deltas, interruptions, transcripts). Built-in providers: OpenAI Realtime, Gemini Live, plus a `FakeRealtimeProvider` for tests. ~50 of the event-type constants belong to the realtime family.

## Observability

`ObservabilityPreset.production(...)` wires runtime event logging, trace export, and metric export from a single `AgentRuntime(observability=...)` argument. Production presets redact raw provider payloads, attach provider request/session IDs to spans, and emit standardized operation names: `agent.run`, `model.turn`, `tool.call`, `mcp.list_tools`, `workspace.command`, `approval.wait`, `cache.lookup`, `artifact.write`. Replay/diff helpers live alongside.

Runtime modules emit canonical events and metadata. Observability modules consume those records — they don't own behavior that produces them.

## Tests

Layout (under `tests/`):

```
unit/                    # source-mirrored unit tests
  core/                  # data models, errors, stores, state, accounting, capabilities
  providers/             # registry and provider unit tests
    model_adapters/      # model adapter controls/capabilities/hardening
  tools/                 # local and hosted tool units
  mcp/                   # MCP specs, connector, client, toolset, trust
  workspaces/            # workspace provider units
  workspace_agents/      # governed workspace-agent package units
  output/                # structured-output schema units
  observability/         # sinks, traces, replay/eval, presets
  realtime/              # realtime contracts, registry, provider units
  integrations/          # optional integration builder units
  runtime/               # runtime-internal helpers (output, planning, results)
  compat/                # compatibility facade/import units
runtime/                 # runtime.run/.stream, AgentLoop, LocalAgentProvider
contracts/               # provider contracts (capability honesty)
golden/<vendor>/         # adapter event-mapping tests with offline fixtures
integration/<vendor>/    # network-gated smoke tests
journey/                 # report-oriented end-to-end runs (model_provider, agent_provider, workspace_provider, business_cases)
e2e/                     # reserved for full coding-agent workflows (currently empty)
fixtures/                # ScriptedModelProvider, FakeOpenAIClient, fakes (shared)
```

**Writing new tests:**

- Behavior of `runtime.run`/`runtime.stream` → `runtime/test_runtime_run.py` or `test_runtime_stream.py`.
- Local provider session lifecycle → `runtime/test_local_agent_provider.py`.
- New adapter event mapping → `golden/<vendor>/` with replayable fixtures (no network); add a corresponding network-gated smoke test under `integration/<vendor>/`.
- Capability flags → `contracts/test_capability_honesty.py` (positive *and* negative assertions).
- Pure data classes → `unit/core/`.
- Provider registry or adapter unit behavior → `unit/providers/` or `unit/providers/model_adapters/`.
- End-to-end demos / live-API smoke runs that emit reviewable JSON reports rather than hard assertions → `journey/<surface>/`.

Use `ScriptedModelProvider` (`tests/fixtures/scripted_model.py`) to drive the loop without network. Update `tests/VALIDATION.md` when adding tests for behaviors listed there. Update `FEATURES.md` when a feature changes status.

## How slices are shaped

Work has been delivered as numbered slices, each one commit, each one self-contained:

- One topic per slice (e.g. "OpenAI Responses adapter", "MCP integration hardening", "production observability preset").
- Slice commits run all three quality gates green before commit.
- The commit message lists what changed, why, and any deferred items.
- Big-picture decisions are recorded in `CHANGELOG.md`.

When starting new work, prefer the same shape: small slice, green gate, descriptive commit, log the deferred items.

## Deferred decisions (don't re-litigate)

These were decided after deliberation; raising them again wastes time:

- Pydantic is the validator of choice for `output_type`; dataclasses are also supported. All four output strategies are now implemented: `provider_native`, `finalizer_tool`, `posthoc_parse`, `posthoc_parse_with_retry`. Strategy selection lives on `OutputSpec`; `OutputFallback` controls behavior when the chosen strategy is unsupported.
- Validation failures are fail-fast (`OutputValidationError`); the application decides whether to retry — except when `posthoc_parse_with_retry` is selected, where the runtime feeds a repair prompt back up to `max_validation_retries` times.
- Tools may be referenced by bare name (`tools=["create_ticket"]`) or by namespaced refs (MCP, hosted, agent), and `MCPToolset` lets callers pass server specs via `toolsets=[...]`.
- Persistence interfaces (`EventStore`, `RunStore`, `SessionStore`, `ProviderCacheStore`) all have JSONL/SQLite implementations alongside the in-memory defaults.
- Workspace data contracts and behavior shipped together. `WorkspaceProvider` is a first-class facade with local/git/sandbox/Docker/cloud backends.
- Cloud agent providers shipped in this priority order: `OpenAICloudAgentProvider` → `ClaudeCodeAgentProvider` → `VertexAIAgentEngineProvider`.

## Things to ask before doing

- Renaming or repurposing `AgentEvent`, `AgentResult`, `ProviderState`, `AgentSession`, or the `ModelProvider` / `AgentProvider` Protocols — these are the load-bearing contracts.
- Adding a new top-level facade on `AgentRuntime`. Prefer extending an existing facade or adding a `RuntimeConfig`-backed surface.
- Adding a new dependency. The runtime core has zero hard dependencies. Optional extras: `openai`, `anthropic`, `google` (genai), `mcp`, `validate` (pydantic). `dev` extras include `pydantic` so tests/examples that exercise structured output run out of the box. `pydantic` is imported lazily inside `_validate_output`, so the runtime works without it as long as you don't pass a Pydantic `output_type`.
- Adding a new MCP risk capability or trust level — these are part of the security boundary contract.
- Sweeping PRD or `FEATURES.md` redlines.
