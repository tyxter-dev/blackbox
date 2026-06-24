# 02 — Feature: The Blackbox Loop (high-level task runner)

**Facade:** `runtime.run(...)` / `runtime.stream(...)` · **Returns:** `AgentResult[T]`
· **Execution layer:** `AgentLoop` (`src/blackbox/runtime/agent_loop.py`)

## Summary

The blackbox loop is the v1 product promise and the default path: give the
runtime a task, registered tools, context, and an optional output type; it runs
the model/tool loop internally and returns a validated result. The user never
parses tool calls, dispatches functions, appends tool results, juggles
continuation IDs, or hand-validates JSON.

```python
result = await runtime.run(
    provider="openai:gpt-5.5",
    input="Review this customer report and create a ticket if needed.",
    tools=["search_customer", "create_ticket"],
    output_type=TicketDecision,
)
decision: TicketDecision = result.output
```

## Why it exists

`llm_factory_toolkit` v1 existed because owning the iterative model/tool exchange
inside a blackbox *was* the product value, not an implementation detail. That
loop is still useful and must remain the easiest path. Everything else in the
runtime — facades, providers, MCP, workspaces — is built so this surface can stay
simple while gaining provider-native power underneath.

## The loop

`AgentLoop` is the execution layer between provider-native model turns and tool
execution. It owns the iterative control flow for local model-backed agents:

```text
provider emits tool call
runtime validates tool name and arguments
runtime checks policy and approval requirements
runtime executes the tool or pauses for approval
runtime captures content, payload, metadata, and errors
runtime sends the result back through provider-native continuation
provider continues generation
```

Responsibilities:

- Start a model turn with task input, tool definitions, context, and provider
  state.
- Detect provider-native function/tool/MCP/hosted-tool requests.
- Dispatch local Python tools through the tool runtime when the runtime owns
  execution; **bridge** hosted tools and remote MCP without pretending they are
  local functions.
- Emit canonical events for every model, tool, approval, artifact, and state
  transition.
- Feed local tool results back using the provider's **native** continuation
  mechanism.
- Continue until a final answer, terminal event, failure, cancellation, or
  approval pause.
- Validate structured output into the requested type and return `AgentResult[T]`.

The loop is a runtime layer, **not** buried inside one provider adapter. Both
`LocalAgentProvider` and `AgentRuntime.run` delegate to the same `AgentLoop`.

## AgentResult[T]

The collected output of a high-level `run(...)` call. Required fields:

- `output` — validated structured output of type `T`, or plain text when no
  `output_type` is requested.
- `text` — final text projection.
- `events` — collected `AgentEvent` stream.
- `items` — durable `RunItem` records.
- `artifacts` — produced artifacts.
- `payloads` — structured tool payloads returned for application use.
- `provider_state` — final provider continuation state when available.
- `metadata` — usage, timing, provider, model, and diagnostic metadata (also
  carries cross-cutting reports like `metadata["fallback"]` and
  `metadata["tool_choice"]["visible_tools"]`).

`OutputSpec` describes how output is produced (four strategies — see
[10](10-structured-output.md)).

## Streaming

`runtime.stream(...)` yields the same `AgentEvent` stream the loop drives; `run`
is `stream` collected. Every event carries a `run_id` (UUID per invocation) and a
monotonic `sequence`; `EventStore.list_events(run_id, after_sequence=...)`
returns the ordered tail.

```python
async for event in runtime.stream(input="Find the failing test and propose a fix.",
                                  tools=["read_file", "run_tests"]):
    print(event.type, event.data)
```

## Requirements

| ID | Priority | Requirement |
|---|---|---|
| P0-R11 | P0 | `run(...)` executes a complete local model/tool loop against a deterministic `ScriptedModelProvider`; tests cover text-only, tool dispatch, multiple tools per turn, context injection, deferred payloads, mock tools, and structured-output validation. |
| P0-R12 | P0 | `AgentResult[T]` exists with typed `output`, `text`, `events`, `items`, `artifacts`, `payloads`, `provider_state`, `metadata`. |
| P0-R14 | P0 | Every event from `run`/`stream` carries `run_id` and monotonic `sequence`; `EventStore.list_events(...)` returns the ordered tail. |
| P1-R11 | P1 | Local autonomous tool loop detects tool calls, executes local tools, feeds results back through provider-native continuation, and stops only on final answer, error, cancellation, or approval pause. |

## Adoption-driven enhancements (shipped)

Surfaced by the first downstream consumer (a multi-tenant WhatsApp agent platform
migrating off `llm_factory_toolkit`):

- **Cross-provider fallback routing** — `run(fallback_providers=[...])` tries
  refs in order on availability/execution errors. Candidates incompatible with a
  present `provider_state` are skipped; attempts reported under
  `result.metadata["fallback"]`.
- **Cross-run dynamic tool-surface persistence** — the final model-visible tool
  surface is emitted as a `TOOL_SET_CHANGED` event and surfaced as
  `result.metadata["tool_choice"]["visible_tools"]`; passing it back as `tools=`
  restores loaded tools without rediscovery.
- **Multi-tenant pattern** — cached runtime-per-tenant factory over shared
  stores; see `docs/MULTITENANCY.md` and `examples/multi_tenant_runtimes.py`.
- **Inbound multimodal input** — `ContentItem` entries in `run(input=[...])` map
  to provider-native multimodal input across OpenAI/xAI, Anthropic, and Gemini;
  unmappable parts raise `UnsupportedFeatureError`.

## Hard constraints

- **The blackbox loop must keep working.** Any change requiring callers to
  manually parse tool calls or feed results back is a product regression, not an
  improvement.
- New tool/approval/policy semantics belong in `AgentLoop`, not duplicated across
  providers. `loop.py` is a compatibility shim — no logic there.

## Status & references

Shipped (M0 complete). Tests: `tests/runtime/test_runtime_run.py`,
`test_runtime_stream.py`, `test_local_agent_provider.py`. Examples:
`examples/echo_run.py`, `examples/local_agent_with_tool.py`,
`examples/run_with_typed_output.py`. PRD §8.4, §10.7, §12.

→ Next: [03 — Model providers](03-model-providers.md)
