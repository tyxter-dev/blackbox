# ModelProvider Framework PRD

**Framework:** `ModelProvider`
**Parent PRD:** `PRD.md` (Blackbox)
**Document status:** Framework PRD, implementation-aligned draft
**Last reviewed against code:** 2026-04-26

## 1. Product Purpose

`ModelProvider` is the provider-native framework for running single model turns
inside Blackbox.

It is the primitive that powers direct model calls, the high-level
`runtime.run(...)` loop, and local model-backed agent sessions. It must preserve
provider-native state, request controls, streaming event shapes, hosted tools,
remote MCP declarations, structured-output behavior, usage metadata, and raw
provider payloads without reducing every provider to chat messages.

The product promise is:

```text
Applications can call different model APIs through one runtime contract while
providers keep their native turn state, event semantics, tools, controls, and
escape hatches.
```

## 2. Relationship To The Core PRD

The root PRD defines:

```text
ModelProvider    -> runs one model turn
AgentLoop        -> turns model events into a complete local tool loop
AgentProvider    -> supervises agent sessions
WorkspaceProvider -> gives agents places to work
```

This document expands the `ModelProvider` layer. It should guide provider
adapter work and model-turn capability decisions. It is not the product-level
blackbox loop by itself.

## 3. Framework Boundary

### In Scope

`ModelProvider` owns:

```text
single model turn execution
provider-native request mapping
streaming provider event mapping
canonical model/tool/hosted-tool/MCP events
provider continuation state
model capability reporting
granular capability profiles
structured-output request mapping
provider-native request controls
raw provider payload preservation
usage, cache, and optional cost metadata
```

### Out Of Scope

`ModelProvider` does not own:

```text
multi-turn autonomous control flow
local Python tool execution
workspace file and command operations
approval waiting rooms
agent session lifecycle
cloud-agent creation or cancellation
business workspace packaging, scheduling, RBAC, or billing
```

`AgentLoop` owns iteration. `ToolRuntime` owns local tools. `WorkspaceProvider`
owns workspace operations. `AgentProvider` owns session lifecycle.

## 4. Current Implementation Snapshot

| Area | Current evidence | State |
|---|---|---|
| Core protocol | `src/blackbox/providers/base.py` | Implemented. `ModelProvider` exposes provider id, capabilities, and `stream_turn(...)`. |
| Model runtime facade | `src/blackbox/runtime/model.py` | Implemented. `runtime.models.run/stream` build `TurnRequest`, validate capabilities, stamp events, collect text, provider state, artifacts, usage, cost, cache, MCP, hosted-tool, workspace, and trace metadata. |
| Local loop integration | `src/blackbox/runtime/agent_loop.py` | Implemented. `AgentLoop` drives repeated model turns and dispatches tool results through provider-native continuation. |
| OpenAI Responses | `src/blackbox/providers/model_adapters/openai_responses/provider.py` | Implemented. Maps Responses streaming events, hosted tools, remote MCP, provider state, structured output, controls, usage, and retries. |
| Anthropic Messages | `src/blackbox/providers/model_adapters/anthropic_messages/provider.py` | Implemented. Preserves native Messages content/history, thinking, tools, MCP blocks, cache controls, and usage. |
| Gemini GenerateContent | `src/blackbox/providers/model_adapters/gemini_generate_content/provider.py` | Implemented. Preserves content/part history, function calls, thought signatures, provider-native JSON schema, cache references, provider cache create/delete, and usage. |
| xAI Responses | `src/blackbox/providers/model_adapters/xai_responses/provider.py` | Implemented as an OpenAI-compatible Responses adapter with conservative capabilities. |
| Echo/test providers | `src/blackbox/providers/model_adapters/echo.py`, `tests/fixtures/scripted_model.py` | Implemented. Used for deterministic local loop and contract coverage. |
| Capability profiles | `src/blackbox/core/capabilities.py`, `src/blackbox/providers/model_adapters/capability_validation.py` | Implemented. Profiles cover hosted tools, output strategies, controls, state modes, constraints, and pre-dispatch validation. |
| Output strategies | `src/blackbox/core/results.py`, `src/blackbox/output/schema.py` | Implemented. Supports `provider_native`, `finalizer_tool`, `posthoc_parse`, and `posthoc_parse_with_retry`. |
| Tests | `tests/golden/`, `tests/runtime/`, `tests/unit/providers/model_adapters/test_model_request_controls.py`, `tests/contracts/test_capability_honesty.py` | Current behavior is covered by golden provider mappings, runtime loop tests, capability validation, request controls, accounting, and integration gates. |

## 5. Primary Users

| User | Need | Value |
|---|---|---|
| App developer | Call OpenAI, Anthropic, Gemini, xAI, or future providers | One turn contract without lowest-common-denominator chat state. |
| Agent runtime developer | Feed model turns into a complete tool loop | Shared event stream and provider-state continuation. |
| Evaluation engineer | Compare provider behavior | Canonical events plus raw provider payloads. |
| Tooling developer | Add hosted tools, local tools, and MCP support | Native hosted tools remain distinct from runtime-executed tools. |
| Platform engineer | Persist and resume work | `ProviderState` carries provider-native continuation details. |

## 6. Core Workflows

### M1: Run A Direct Model Turn

```python
result = await runtime.models.run(
    provider="openai:gpt-5.4",
    input="Summarize this incident report.",
)
```

The runtime streams events, collects text, preserves provider state, and returns
`TurnResult`.

### M2: Run A Turn Inside The Blackbox Agent Loop

```python
result = await runtime.run(
    provider="anthropic:claude-sonnet",
    input="Create a support ticket if this customer needs escalation.",
    tools=["create_ticket"],
    output_type=TicketDecision,
)
```

`ModelProvider` executes the turn. `AgentLoop` detects tool calls, dispatches
tools, feeds tool results back through provider-native continuation, and
validates the final output.

### M3: Continue Provider-Native State

```python
first = await runtime.models.run(
    provider="openai:gpt-5.4",
    input="Start analysis.",
)

second = await runtime.models.run(
    provider="openai:gpt-5.4",
    input="Continue.",
    provider_state=first.provider_state,
)
```

OpenAI may use `previous_response_id`; Anthropic and Gemini may replay native
history objects. The runtime does not force those mechanisms into one chat
transcript.

### M4: Use Hosted Tools Separately From Local Tools

```python
result = await runtime.run(
    provider="openai:gpt-5.4",
    input="Research recent product issues and produce a report.",
    hosted_tools=[WebSearch(), RemoteMCP(server_label="docs", server_url="https://...")],
)
```

Hosted tools are provider-native capabilities. Local Python tools still flow
through `ToolRuntime`.

### M5: Request Structured Output

```python
result = await runtime.run(
    provider="google:gemini-2.5-pro",
    input="Return the incident classification.",
    output_spec=OutputSpec(
        schema=IncidentClassification,
        strategy="provider_native",
        fallback="finalizer_tool",
    ),
)
```

The runtime resolves the requested output strategy against the provider's
capability profile before dispatch.

## 7. Public Contract

The current protocol is:

```python
@runtime_checkable
class ModelProvider(Protocol):
    @property
    def provider_id(self) -> str:
        ...

    def capabilities(self, model: str | None = None) -> ModelCapabilities:
        ...

    def stream_turn(self, request: TurnRequest) -> AsyncIterator[AgentEvent]:
        ...
```

`TurnRequest` carries:

```text
model
input
provider_state
tools
hosted_tools
controls
output_schema
output_strategy
artifacts
metadata
extra
```

`TurnResult` collects text, events, provider state, artifacts, and metadata.

## 8. Design Rules

1. **One turn only:** A provider adapter must not secretly own the full
   multi-turn agent loop.
2. **Events first:** Text is a projection of `model.text.delta`; it is not the
   only runtime output.
3. **Provider state is native:** `ProviderState` can contain IDs, native history,
   reasoning state, tool state, and continuation payloads.
4. **Capabilities are enforced:** Explicit hosted tools, controls, output
   strategies, and state modes must fail before provider dispatch when the
   provider cannot support them.
5. **Raw payloads survive:** Adapters must keep raw SDK events/responses where
   possible.
6. **Hosted tools are not local tools:** Provider-executed tools, client-executed
   hosted tools, local Python tools, MCP tools, and workspace tools remain
   different concepts.
7. **Chat compatibility is explicit:** `runtime.chat` is a projection over
   model runtime input, not the internal source of truth.

## 9. Event Taxonomy

`ModelProvider` adapters should map common turn concepts to these canonical
events:

```text
model.request.started
model.item.created
model.text.delta
model.reasoning.delta
model.item.completed
model.completed
tool.call.requested
tool_search.requested
tool_search.completed
hosted_tool.call.requested
hosted_tool.call.started
hosted_tool.call.completed
hosted_tool.call.failed
hosted_tool.output.prepared
mcp.list_tools.completed
mcp.approval.required
mcp.call.started
mcp.call.completed
artifact.created
retry.started
retry.completed
retry.failed
```

Unknown provider items should fall back to a generic model item event with the
original item type and raw payload preserved.

## 10. Requirements

### P0 - Required

| ID | Requirement | Current state | Acceptance criteria |
|---|---|---|---|
| M-P0-1 | Stable provider identity | Implemented | Every adapter exposes `provider_id` and can be registered/routed. |
| M-P0-2 | Capability honesty | Implemented | Summary capabilities and granular profiles are conservative and tested. |
| M-P0-3 | Streaming events | Implemented | `stream_turn(...)` yields canonical `AgentEvent`s for request, deltas/items/tool calls, and completion. |
| M-P0-4 | Raw payload preservation | Implemented | SDK events/responses are kept in `raw` or provider-specific data. |
| M-P0-5 | Provider state | Implemented | Providers return and accept `ProviderState` using native continuation mechanisms. |
| M-P0-6 | Tool-call normalization | Implemented | Native function/tool calls map to `tool.call.requested` with name, arguments, and call id. |
| M-P0-7 | Text projection | Implemented | `ModelRuntime.run(...)` collects `model.text.delta` into `TurnResult.text`. |
| M-P0-8 | Typed errors | Implemented | Missing configuration, unsupported features, validation failures, and provider errors raise runtime error types. |
| M-P0-9 | No chat as canonical state | Implemented | Chat compatibility is isolated in `runtime.chat`; provider state remains native. |
| M-P0-10 | Deterministic fixtures | Implemented | Scripted/fake clients cover provider mappings without network calls. |
| M-P0-11 | Runtime loop compatibility | Implemented | `AgentLoop` can run any registered model provider that emits the required events. |

### P1 - Important

| ID | Requirement | Current state | Acceptance criteria |
|---|---|---|---|
| M-P1-1 | Provider-native structured output | Implemented for OpenAI and Gemini, unsupported where not mapped | Strategy support is capability-gated and fallbacks are resolved before dispatch. |
| M-P1-2 | Hosted tool mapping | Implemented, provider-dependent | Typed hosted tools map to native provider payloads or fail before dispatch. |
| M-P1-3 | Remote MCP mapping | Implemented for OpenAI and Anthropic | `RemoteMCP` maps to provider-native remote MCP configuration where supported. |
| M-P1-4 | Usage, cache, and cost metadata | Implemented | Usage normalizes into result metadata with cache token splits and tool-call counts; optional catalog pricing estimates cost; cache-hit metrics are exposed when provider usage includes cached tokens. |
| M-P1-5 | Model-specific capability profiles | Implemented, mostly static | Profiles can vary by model/API version/provider once adapters expose dynamic data. |
| M-P1-6 | Request controls | Implemented, provider-dependent | Instructions, temperature, max tokens, tool choice, cache, reasoning, verbosity, tool search, compaction, store/background, include, and extra controls are mapped or rejected. |
| M-P1-7 | Artifact refs | Partial | `TurnRequest.artifacts` exists; adapters should define provider-visible file/artifact input mapping. |
| M-P1-8 | Golden event tests | Implemented | Provider event mappings have offline golden coverage. |
| M-P1-9 | Hosted client-executed tool continuation | Implemented for supported local handlers | Shell, patch, and computer-style hosted calls can be handled by runtime callbacks and fed back to the model. |

### P2 - Later

| ID | Requirement | Acceptance criteria |
|---|---|---|
| M-P2-1 | Typed multimodal input parts | Image/audio/video/file content parts are first-class, not ad hoc dicts. |
| M-P2-2 | Provider-side cache lifecycle | Partially implemented | `runtime.caches` persists provider cache records, supports invalidation/expiry eviction, and creates/deletes Gemini context caches where APIs support it. |
| M-P2-3 | Dynamic provider discovery | Capability profiles can be populated from provider metadata or model lists. |
| M-P2-4 | Realtime turns | Realtime/audio session surfaces live beside normal model turns, not inside this protocol unchanged. |
| M-P2-5 | Provider eval metadata | Adapters can emit eval-related events or metadata where the provider exposes them. |

## 11. Non-Goals

`ModelProvider` must not:

```text
execute local Python tools
run shell commands
own file-system state
own approval waiting rooms
represent cloud agents as model calls
manage long-lived agent sessions
hide provider-native state behind chat messages
become a generic provider-breadth wrapper at the cost of native semantics
```

## 12. Gaps And Decisions

1. **Static capability profiles:** Profiles are now explicit and conservative,
   but model-specific differences are mostly hand-authored. Add provider/model
   discovery only when it improves correctness.
2. **Artifact input mapping:** `TurnRequest.artifacts` exists, but provider
   adapters need clear rules for file refs, images, generated artifacts, and
   provider-hosted file IDs.
3. **Typed multimodal input:** Current adapters accept strings and provider-like
   structures. Define typed content parts before broad multimodal support grows
   organically.
4. **Provider-native output parity:** OpenAI and Gemini have native schema
   mapping. Anthropic and xAI should remain unsupported until the adapter can
   enforce the contract honestly.
5. **Hosted tool status vocabulary:** Keep the distinction between provider
   server execution, runtime client execution, and unsupported typed mapping
   visible in capability profiles.
6. **OpenAI-compatible providers:** xAI currently subclasses the OpenAI
   Responses path with stripped/limited controls. Future compatible providers
   should follow that pattern only when their capability profile is honest.

## 13. Definition Of Done

The framework is successful when:

```text
1. OpenAI, Anthropic, Gemini, xAI, Echo, and test providers implement one turn
   protocol without flattening native state into chat.
2. Provider-specific event shapes normalize into canonical model/tool/MCP/
   hosted-tool events and preserve raw payloads.
3. ProviderState round-trips across turns.
4. Unsupported hosted tools, controls, output strategies, and state modes fail
   before provider calls.
5. Hosted tools stay distinct from local tools.
6. Usage, cost, MCP, hosted-tool, workspace, and trace metadata can be collected
   from streamed events.
7. Golden tests prove event mappings and contract tests prove capability
   honesty.
```
