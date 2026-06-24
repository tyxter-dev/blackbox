# 03 — Feature: Model Providers

**Protocol:** `ModelProvider` · **Facade:** `runtime.models` · **Adapters:**
`src/blackbox/providers/model_adapters/`

## Summary

A `ModelProvider` runs a single model turn and streams normalized `AgentEvent`s
while preserving provider-native semantics. It is one of the two core protocols
(the other is `AgentProvider`, [04](04-agent-providers.md)) and never the same
thing as a session.

```python
result = await runtime.models.run(provider="openai", model="gpt-5.5",
                                  input="Explain the failing test in this traceback.")
print(result.text)

async for event in runtime.models.stream(provider="openai:gpt-5.5", input="Review this patch."):
    if event.type == "model.text.delta":
        print(event.data["delta"], end="")
```

## The protocol

```python
class ModelProvider(Protocol):
    @property
    def provider_id(self) -> str: ...
    def capabilities(self, model: str | None = None) -> ModelCapabilities: ...
    async def stream_turn(self, request: TurnRequest) -> AsyncIterator[AgentEvent]: ...
```

Required behavior:

- Accept a `TurnRequest`; emit `AgentEvent` instances.
- Preserve raw provider data in event `raw`/`data` fields.
- Emit canonical text deltas via `model.text.delta`.
- Emit tool, hosted-tool, MCP, reasoning, and state events where supported.
- Return provider state through completion metadata or the collected result.

`stream_turn` is declared as a plain `def` returning `AsyncIterator[...]` (not
`async def`) so async-generator implementations type-check.

## Adapters

| Adapter | Surface | Notes |
|---|---|---|
| `OpenAIResponsesProvider` | OpenAI Responses API | First real adapter. Native Responses output items → events/items; response IDs + continuation in `ProviderState` via `previous_response_id`. **Never** converts to Chat Completions. |
| `AnthropicMessagesProvider` | Anthropic Messages | Native content blocks; `tool_use`/`tool_result` → typed events/items; server tools as hosted-tool events; thinking/reasoning metadata preserved. No alternating chat-message normalization in core. |
| `GeminiGenerateContentProvider` | Gemini GenerateContent | Native Content/Part structure; function-call IDs, thought signatures, part ordering preserved; synthetic IDs only as a legacy fallback. |
| `XAIResponsesProvider` | xAI | Responses-style; shares multimodal input mapping with OpenAI. |
| `EchoModelProvider` | test/dev | Deterministic streaming for tests and examples. |

`ScriptedModelProvider` (`tests/fixtures/scripted_model.py`) drives the loop
deterministically without network and is the fixture for all loop tests.

### OpenAI Responses initial event mappings

| Provider concept | Runtime event/item |
|---|---|
| Text delta | `model.text.delta` |
| Reasoning item/delta | `model.reasoning.delta` or reasoning `RunItem` |
| Function call | `tool.call.requested` / function-call `RunItem` |
| Function output | `tool.call.completed` / function-result `RunItem` |
| Hosted web/file/code/shell/computer tool | hosted tool event/item |
| Tool search call/output | `tool_search.requested` / `tool_search.completed` |
| Remote MCP list/call | `mcp.list_tools.completed`, `mcp.call.started`, `mcp.call.completed` |

## Capabilities

Each provider advertises a `ModelCapabilities` object. **Capability honesty is a
hard contract:** if a provider advertises `supports_X=False`, calling that
operation raises a typed runtime error *before* SDK dispatch — never a silent
no-op. Covered (positive and negative) by
`tests/contracts/test_capability_honesty.py`.

## Provider state

Adapters preserve native continuation IDs inside `ProviderState.tool_state`
(`previous_response_id`, conversation IDs, response-output items, Anthropic
tool/MCP state, Gemini grounding/file metadata). The core runtime must never
reduce this to a chat transcript. State round-trips across turns and across
`run`/`stream` invocations.

## Requirements

| ID | Priority | Requirement |
|---|---|---|
| P0-R1 | P0 | Provider registry registers/resolves `ModelProvider` and `AgentProvider` by key. |
| P0-R2 | P0 | Echo provider + at least one real provider implement `stream_turn`. |
| P0-R6 | P0 | Model runs return and accept provider continuation state. |
| P0-R8 | P0 | Model runtime collects streamed text into a `TurnResult`. |
| P0-R9 | P0 | Providers expose explicit capability objects. |
| P0-R17 | P0 | Every `False` capability flag is tested to raise a typed error, not no-op. |
| P1-R1 | P1 | OpenAI Responses adapter maps output items to events without flattening to chat. |
| P1-R2 | P1 | Anthropic Messages adapter maps text/tool_use/tool_result/server tools/reasoning. |
| P1-R3 | P1 | Gemini adapter preserves parts, function-call IDs, thought signatures, state. |

## Hard constraints

- **No LiteLLM.** Routing is the in-house `ProviderRegistry`.
- **No internal Chat Completions conversion.** Adapters carry `raw=<sdk_object>`.
- **Capabilities tell the truth.**
- Renaming `ModelProvider` or `ProviderState` is an "ask first" change.

## Status & references

OpenAI Responses, Anthropic Messages, Gemini, xAI, Echo shipped (M1 complete).
Model adapters are a commodity maintenance treadmill — keep healthy, don't chase
breadth (`ROADMAP.md` strategic thesis). Bundled model/pricing catalogs are
snapshots needing periodic refresh. Tests: `tests/golden/<vendor>/` (offline
fixtures) + `tests/integration/<vendor>/` (network-gated). Docs: `docs/MODEL.md`,
`docs/MODEL_PROVIDER_WORKFLOW_VALIDATION.md`. PRD §9.1, §13.1/13.3/13.5.

→ Next: [04 — Agent providers](04-agent-providers.md)
