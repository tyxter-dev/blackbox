# AGENTS.md

Project-specific guidance for coding agents working on `agent_runtime`.

## What this project is

`agent_runtime` is the successor in spirit to `llm_factory_toolkit`, not a mechanical rewrite of it.

The original v1 product promise is still load-bearing:

```text
developer gives input + tools + output schema
runtime runs the model/tool loop internally
runtime executes allowed tools and feeds results back
runtime stops only at final output, failure, cancellation, or approval pause
developer receives a validated AgentResult[T]
```

That blackbox loop is the product soul. Users should not have to parse provider tool calls, dispatch Python functions, append tool results, juggle continuation IDs, or hand-validate JSON just to complete a normal agent task.

The new architecture expands that promise for the current provider landscape. The runtime must support direct model turns, local Python tools, hosted provider tools, MCP, workspaces, approvals, artifacts, provider state, and cloud-managed agent sessions without flattening everything into chat messages.

So the project has two public levels:

- `runtime.run(...)` / `runtime.stream(...)`: the high-level task runner for everyday use, returning `AgentResult[T]`.
- `runtime.models.*` and `runtime.agents.*`: lower-level provider-native supervision APIs for model turns and agent sessions.

The product is not a LiteLLM clone, not a Chat Completions normalizer, and not only a cloud-agent session wrapper. It is event-first, session-first, state-first, provider-native, and still centered on the complete agent loop.

## Source of truth

Read these before making non-trivial changes:

- `PRD.md` — the contract. Sections most worth re-reading: §4 design principles, §7.1 MVP scope, §8.4 AgentLoop, §10.7 AgentResult, §11 event taxonomy, §12 P0/P1/P2, §22 milestones.
- `CHANGELOG.md` — what each slice shipped (one entry per release, semver-ish).
- `tests/VALIDATION.md` — coverage tracker with status per behavior.
- `README.md` — public-facing API surface.

The PRD is updated when product direction shifts; ask before making sweeping PRD edits.

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
```

All three quality gates (pytest / ruff / mypy --strict) must pass before committing. The focused structural gate is `pytest -q tests/unit tests/runtime tests/contracts tests/golden`; the current baseline is 479 passing + 1 skipped. Broader integration suites are network-gated and require provider credentials.

## Architecture in five points

1. **Two protocols, not one.** `ModelProvider` runs a single model turn (`stream_turn`); `AgentProvider` runs sessions (`create_agent`/`start_session`/`stream_events`/...). Cloud agents are NOT model calls with more tools — keep these surfaces separate.

2. **`AgentRuntime` exposes three facades plus the high-level path.**
   - `runtime.run(...)` / `runtime.stream(...)` — the blackbox surface; collects from `stream`.
   - `runtime.tools` — local tool registry facade (`register`, `get`, `all_tools`, `to_provider_tools`, `call(..., mock=...)`).
   - `runtime.models` — direct model turns.
   - `runtime.agents` — session lifecycle (cloud or local).

3. **`AgentLoop` (in `src/agent_runtime/runtime/agent_loop.py`) is the shared execution layer.** Both `LocalAgentProvider` and the high-level `AgentRuntime.run` delegate to it. New tool/approval/policy semantics belong here, not duplicated across the providers. `src/agent_runtime/loop.py` is only a compatibility shim.

4. **Events are the runtime stream.** `AgentEvent` carries `run_id` (UUID per `runtime.run/.stream` invocation) and a monotonic `sequence`. Every event flows through `runtime.event_store` (an `EventStore` Protocol; in-memory by default). Text is one projection; structured `output` is another; the events log is the truth.

5. **`ProviderState` is provider-native, not chat history.** Adapters preserve native continuation IDs (`previous_response_id`, conversation IDs, response-output items) inside `ProviderState`. Never reduce this to a chat-message transcript inside the core runtime.

## Hard constraints

These are easy to violate by accident and have explicit decisions behind them:

- **No LiteLLM dependency**, ever. Provider routing is done with a tiny in-house `ProviderRegistry`. The reasoning is in PRD §3.3.
- **Chat messages are a compatibility export, not the canonical state.** No internal code path should treat alternating user/assistant messages as the source of truth.
- **Raw provider payloads must be preserved.** Adapter events carry `raw=<sdk_object>` whenever possible. Use `RawEnvelope` (`core/raw.py`) when sensitivity tagging matters.
- **Hosted/provider tools are not fake local tools.** Local Python tools, hosted provider tools, MCP tools, cloud-agent tools, and workspace tools are distinct backends. See PRD §14.4.
- **Capabilities tell the truth.** If a provider advertises `supports_X=False`, calling that operation must raise a typed runtime error, not silently no-op. There's a contract test for this in `tests/contracts/test_capability_honesty.py`.
- **The blackbox loop must keep working.** `runtime.run(...)` is the v1 product promise. Any change that requires callers to manually parse tool calls or feed results back is a regression of the product, not an improvement.

## Conventions

### Routing format

`provider:model` is the canonical separator at the high level (`model="openai:gpt-5.4"`). `/` still works for backward compatibility but collides with namespaced agent paths like `vertex-agent-engine/projects/foo/agent`. Use `:` in new examples.

### Event taxonomy

Constants live in `EventTypes` in `core/events.py`. New event types should be added there, not invented per-adapter. The shape is `<domain>.<verb>` (`tool.call.requested`, `model.text.delta`, `approval.requested`, etc.).

### Error hierarchy

All exceptions descend from `AgentRuntimeError` (`core/errors.py`). Use the closest-matching subclass (`ProviderNotFoundError`, `ProviderNotConfiguredError`, `ProviderExecutionError`, `ToolExecutionError`, `ApprovalError`, `SessionError`, `OutputValidationError`, `CapabilityError`, ...). Don't add bare `Exception` subclasses.

### Style

- Type hints required on every public function. `mypy --strict` is enforced.
- Prefer dataclasses with `slots=True` for value types. Frozen dataclasses for ID/ref objects.
- Async generators for streaming methods. Protocol declarations use plain `def` returning `AsyncIterator[...]`, not `async def`, so async-generator implementations type-check.
- Imports: the file pattern is stdlib, typing, `agent_runtime.*`. Ruff handles ordering; don't fight it.
- Use `replace(event, ...)` (from `dataclasses`) to update frozen events; never mutate.

### Naming

- `ModelProvider` adapters: `<Vendor><Surface>Provider` (`OpenAIResponsesProvider`, `AnthropicMessagesProvider`, `GeminiGenerateContentProvider`).
- `AgentProvider` adapters: `<Surface>AgentProvider` (`LocalAgentProvider`, `OpenAICloudAgentProvider`, `ClaudeCodeAgentProvider`, `VertexAIAgentEngineProvider`). The earlier names `AnthropicManagedAgentProvider` and `GoogleAgentPlatformProvider` are deprecated aliases — don't use them in new code.

## Tests

Layout (under `tests/`):

```
unit/                    # source-mirrored unit tests
  core/                  # data models, errors, stores, state, accounting
  providers/             # registry and provider unit tests
    model_adapters/      # model adapter controls/capabilities/hardening
  tools/                 # local and hosted tool units
  mcp/                   # MCP specs, connector, client, toolset
  workspaces/            # workspace provider/runtime units
  workspace_agents/      # governed workspace-agent package units
  output/                # structured-output schema units
  observability/         # sinks, traces, replay/eval workflow units
  realtime/              # realtime contracts and registry units
  compat/                # compatibility facade/import units
  integrations/          # optional integration builder units
runtime/                 # runtime.run/.stream, AgentLoop, LocalAgentProvider
contracts/               # provider contracts (capability honesty, etc.)
golden/<vendor>/         # adapter event-mapping tests with offline fixtures
integration/<vendor>/    # network-gated smoke tests
e2e/                     # reserved for full coding-agent workflows
fixtures/                # ScriptedModelProvider, FakeOpenAIClient (shared)
```

**Writing new tests:**

- Behavior of `runtime.run`/`runtime.stream` -> `runtime/test_runtime_run.py` or `test_runtime_stream.py`.
- Local provider session lifecycle -> `runtime/test_local_agent_provider.py`.
- New adapter event mapping -> `golden/<vendor>/` with replayable fixtures (no network); add a corresponding network-gated smoke test under `integration/<vendor>/`.
- Capability flags -> `contracts/test_capability_honesty.py` (positive *and* negative assertions).
- Pure data classes -> `unit/core/`.
- Provider registry or adapter unit behavior -> `unit/providers/` or
  `unit/providers/model_adapters/`.

Use `ScriptedModelProvider` (`tests/fixtures/scripted_model.py`) to drive the loop without network. Update `tests/VALIDATION.md` when adding tests for behaviors listed there.

## How slices are shaped

Work has been delivered as numbered slices, each one commit, each one self-contained:

- One topic per slice (e.g. "OpenAI Responses adapter", "test reorganization").
- Slice commits run all three quality gates green before commit.
- The commit message lists what changed, why, and any deferred items.
- Big-picture decisions are recorded in `CHANGELOG.md`.

When starting new work, prefer the same shape: small slice, green gate, descriptive commit, log the deferred items.

## Deferred decisions (don't re-litigate)

These were decided after deliberation; raising them again wastes time:

- Pydantic is the validator of choice for `output_type`; dataclasses are also supported. v0.1 uses `posthoc_parse` strategy. `provider_native` and `posthoc_parse_with_retry` are reserved on `OutputSpec` for later.
- Validation failures are fail-fast (`OutputValidationError`); the application decides whether to retry. No automatic retry in v0.1.
- Tools are referenced by name (`tools=["create_ticket"]`), not by `ToolRef`. Namespaced `ToolRef` lands with M3 (MCP).
- Persistence interfaces (`EventStore`, `RunStore`) are P0; JSONL/SQLite implementations are P1.
- Workspace data contracts and behavior land together in M2.
- The first real `AgentProvider` is `OpenAICloudAgentProvider` (M4 priority order: OpenAI -> Claude Code -> Vertex Agent Engine).

## Things to ask before doing

- Renaming or repurposing `AgentEvent`, `AgentResult`, `ProviderState`, or the `ModelProvider` / `AgentProvider` Protocols — these are the load-bearing contracts.
- Adding a new top-level facade on `AgentRuntime`.
- Introducing a new dependency. The runtime core has zero hard dependencies. Optional extras: `openai`, `anthropic`, `google` (genai), `mcp`, `validate` (pydantic). `dev` extras include `pydantic` so tests/examples that exercise structured output run out of the box. `pydantic` is imported lazily inside `_validate_output`, so the runtime works without it as long as you don't pass a Pydantic `output_type`.
- Sweeping PRD redlines.
