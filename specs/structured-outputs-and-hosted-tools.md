# Structured Outputs and Hosted Tools Implementation Spec

Status: draft

Scope: implement the two highest-value next features:

1. Provider-native and finalizer-tool structured output strategies.
2. First-class hosted tool specs for provider-managed tools.

This spec intentionally does not cover managed MCP transports, cloud agents,
handoffs, realtime, or tracing. Those should build on the surfaces defined
here.

References:

- OpenAI Responses `text.format` supports `json_schema` structured output and
  `json_object` JSON mode. The Responses `tools` array supports function,
  hosted, and remote MCP tools.
- Gemini structured output uses `response_mime_type="application/json"` and
  `response_json_schema` in generation config.
- Current repo state: `OutputSpec` defines `provider_native` and
  `finalizer_tool`, but runtime behavior is still post-hoc parsing except for
  retry.

## Goals

### Feature 1: Structured Output Strategies

Make these strategies real:

- `OutputSpec(strategy="provider_native")`
- `OutputSpec(strategy="finalizer_tool")`

Keep these existing strategies working:

- `posthoc_parse`
- `posthoc_parse_with_retry`

The runtime must preserve the current high-level contract:

```python
result = await runtime.run(
    provider="openai:gpt-5.4",
    input="Extract incident details.",
    output_spec=OutputSpec(schema=Incident, strategy="provider_native"),
)
assert isinstance(result.output, Incident)
```

### Feature 2: Hosted Tool Specs

Let applications configure provider-managed tools without raw provider dicts:

```python
result = await runtime.run(
    provider="openai:gpt-5.4",
    input="Research this and write a report.",
    hosted_tools=[
        WebSearch(),
        FileSearch(vector_store_ids=["vs_123"]),
    ],
    output_type=Report,
)
```

Hosted tools must stay distinct from local Python tools. Local tools are
executed by `ToolRuntime`; hosted tools are executed by the provider and only
observed, configured, and normalized by this runtime.

## Non-Goals

- Do not implement managed MCP stdio/HTTP transports in this spec.
- Do not implement a real cloud/coding-agent provider.
- Do not add realtime/audio abstractions.
- Do not make chat messages the internal state model.
- Do not hard-code provider pricing or provider-specific model matrices.

## Current Problems

1. `provider_native` is only a named strategy; it does not affect provider
   request payloads.
2. `finalizer_tool` is only a named strategy; there is no hidden finalization
   tool.
3. Dict JSON Schemas are rejected by `_validate_output(...)` for post-hoc
   parsing, and there is no path where they are useful.
4. Hosted tools can be passed manually as raw provider dicts, but there is no
   public typed API for them.
5. Hosted tool output items are mostly generic fallback events, so apps cannot
   reliably distinguish web search, file search, code execution, and remote MCP
   results.

## Design Principles

1. Provider-native first, but never provider-only.
2. Explicit fallback behavior.
3. Capability flags must stay honest.
4. Typed public specs, raw provider escape hatches preserved.
5. Local tools and hosted tools stay separate in API and event semantics.
6. Output parsing still validates locally before returning `AgentResult[T]`.

## Feature 1 Design: Structured Outputs

### Public API

Existing API stays:

```python
OutputSpec(
    schema=Incident,
    strategy="provider_native",
    max_validation_retries=1,
)
```

Add optional fields to `OutputSpec`:

```python
name: str | None = None
description: str | None = None
strict: bool = True
fallback: Literal["error", "finalizer_tool", "posthoc_parse"] = "error"
```

Rationale:

- `name` maps to OpenAI `text.format.name` and hidden finalizer tool names.
- `description` maps to provider schema/tool descriptions.
- `strict` maps to providers that support strict schema adherence.
- `fallback` controls unsupported provider behavior explicitly.

Default behavior:

- `provider_native` with unsupported provider raises `UnsupportedFeatureError`
  unless `fallback` is set.
- `finalizer_tool` requires function-tool support; otherwise raises
  `UnsupportedFeatureError`.
- `posthoc_parse` remains the default when callers pass `output_type=...`.

### Internal Model

Add a schema helper module:

```text
src/agent_runtime/output/schema.py
```

Responsibilities:

- Convert Pydantic models to JSON Schema with `model_json_schema()`.
- Convert dataclasses to JSON Schema.
- Accept raw dict JSON Schemas directly.
- Produce an `OutputSchema` object:

```python
@dataclass(slots=True, frozen=True)
class OutputSchema:
    name: str
    schema: dict[str, Any]
    description: str | None = None
    strict: bool = True
    target_type: type[Any] | dict[str, Any] | None = None
```

Validation remains local after provider return:

- Pydantic: `model_validate_json`.
- Dataclass: parse JSON object then instantiate.
- Dict schema: validate with `jsonschema` if available later; phase 1 can
  parse and return dict for provider-native/finalizer strategies.

### Provider Request Wiring

Extend `TurnRequest`:

```python
output_schema: OutputSchema | None = None
output_strategy: OutputStrategy | None = None
```

Provider mappings:

#### OpenAI Responses

When `output_strategy == "provider_native"`:

```python
kwargs["text"] = {
    **existing_text_config,
    "format": {
        "type": "json_schema",
        "name": schema.name,
        "schema": schema.schema,
        "strict": schema.strict,
        # description if present
    },
}
```

If `request.extra["text"]` already exists, merge without losing explicit
caller fields. Explicit caller fields win unless that would silently disable
the requested structured output; in that conflict, raise `ValueError`.

#### Gemini GenerateContent

When `output_strategy == "provider_native"`:

```python
config["response_mime_type"] = "application/json"
config["response_json_schema"] = schema.schema
```

If caller-provided `extra["config"]` conflicts with the structured output
schema, raise `ValueError`.

#### Anthropic Messages

Phase 1: no provider-native text response schema.

Behavior:

- `provider_native` raises `UnsupportedFeatureError` unless fallback is set.
- `finalizer_tool` works through the hidden tool if function tools are
  supported.

#### xAI Responses

Phase 1: keep conservative.

Behavior:

- Do not advertise `supports_structured_output` until a live/golden smoke
  verifies the OpenAI-compatible structured output shape.
- Permit `finalizer_tool` if function tools work.

### Finalizer Tool Strategy

For `OutputSpec(strategy="finalizer_tool")`, inject a hidden tool:

```python
{
    "type": "function",
    "name": "submit_final_output",
    "description": "...",
    "parameters": schema.schema,
    "metadata": {"agent_runtime_hidden": True, "purpose": "final_output"},
}
```

Runtime behavior:

1. Add the finalizer tool to provider-visible tools.
2. The model calls `submit_final_output`.
3. `AgentLoop` recognizes this tool name as terminal.
4. The runtime validates the call arguments as the final output.
5. The runtime does not execute a local Python function for this tool.
6. Emit:
   - `tool.call.requested`
   - `tool.call.completed` with metadata marking final output
   - `model.completed` as provided by adapter if present
   - final `AgentResult.output`

Open question for implementation: whether finalizer completion should stop
immediately before a provider continuation turn, or whether the loop should
feed a synthetic function result back and allow a short final text. Phase 1
should stop immediately after validated finalizer arguments to avoid extra
tokens and ambiguity.

### Capability Updates

Set `supports_structured_output=True` only for providers with native schema
wiring:

- OpenAI Responses: true.
- Gemini: true.
- Anthropic: false in phase 1.
- xAI: false in phase 1.
- Echo/Scripted test providers: false unless a test fixture opts in.

`finalizer_tool` depends on `supports_function_tools`, not
`supports_structured_output`.

### Tests

Unit:

- Pydantic to `OutputSchema`.
- Dataclass to `OutputSchema`.
- Raw dict JSON Schema to `OutputSchema`.
- Schema name normalization and defaulting.

Provider request kwargs:

- OpenAI receives `text.format.type == "json_schema"`.
- OpenAI preserves existing `text` config and rejects conflicting format.
- Gemini receives `config.response_mime_type` and `config.response_json_schema`.
- Unsupported provider with `provider_native` raises `UnsupportedFeatureError`.

Runtime:

- `provider_native` validates returned JSON into Pydantic.
- `provider_native` supports dict schema and returns dict.
- `finalizer_tool` injects hidden tool and terminates with validated output.
- Finalizer tool is not visible in `runtime.tools.all_tools()`.
- Existing `posthoc_parse` and retry tests keep passing.

Docs/catalog:

- `FEATURES.md`: provider-native structured output becomes Supported for
  OpenAI/Gemini, finalizer tool becomes Supported.
- `tests/VALIDATION.md`: add rows for provider-native and finalizer tool.

## Feature 2 Design: Hosted Tool Specs

### Public API

Add:

```python
from agent_runtime.hosted_tools import WebSearch, FileSearch, CodeInterpreter

result = await runtime.run(
    provider="openai:gpt-5.4",
    input="Research this.",
    hosted_tools=[
        WebSearch(),
        FileSearch(vector_store_ids=["vs_123"]),
        CodeInterpreter(container_id="cntr_123"),
    ],
)
```

Also support lower-level model calls:

```python
await runtime.models.run(
    provider="openai:gpt-5.4",
    input="Research this.",
    hosted_tools=[WebSearch()],
)
```

Keep raw provider dicts available through `extra` or as an explicit escape
hatch:

```python
HostedToolRaw(payload={"type": "future_provider_tool", ...})
```

### Data Model

Add:

```text
src/agent_runtime/hosted_tools.py
```

Phase 1 specs:

```python
@dataclass(slots=True, frozen=True)
class WebSearch:
    search_context_size: str | None = None
    user_location: dict[str, Any] | None = None

@dataclass(slots=True, frozen=True)
class FileSearch:
    vector_store_ids: list[str]
    max_num_results: int | None = None
    filters: dict[str, Any] | None = None
    include_results: bool = False

@dataclass(slots=True, frozen=True)
class CodeInterpreter:
    container_id: str | None = None

@dataclass(slots=True, frozen=True)
class HostedToolRaw:
    payload: dict[str, Any]
```

Remote MCP should not be part of this phase unless needed for type layout.
Leave `RemoteMCP` for the next spec/commit.

### TurnRequest Changes

Extend `TurnRequest`:

```python
hosted_tools: list[HostedToolSpec] = field(default_factory=list)
```

`ModelRuntime.stream/run` and `AgentRuntime.stream/run` accept
`hosted_tools=...` and pass them through.

### Provider Mapping

Add provider conversion helpers:

```text
src/agent_runtime/hosted_tools.py
to_openai_tool(spec) -> dict[str, Any]
to_gemini_tool(spec) -> dict[str, Any]
```

#### OpenAI Responses

Map:

- `WebSearch()` -> `{"type": "web_search"}`
- `FileSearch(vector_store_ids=[...])` -> `{"type": "file_search", ...}`
- `CodeInterpreter(container_id=...)` -> `{"type": "code_interpreter", ...}`
- `HostedToolRaw(payload)` -> payload

If `FileSearch.include_results=True`, add request include:

```python
kwargs["include"] = [..., "file_search_call.results"]
```

OpenAI local function tools and hosted tools both go into `kwargs["tools"]`,
but originate from distinct runtime API arguments.

#### Gemini

Phase 1:

- `WebSearch` maps to Google search tool if the adapter supports it cleanly.
- `FileSearch` and `CodeInterpreter` raise `UnsupportedFeatureError` unless
  raw provider payload is used.

Keep Gemini hosted-tool support conservative because its tool vocabulary and
model support vary.

#### Anthropic and xAI

Phase 1:

- Accept `HostedToolRaw` only.
- Raise `UnsupportedFeatureError` for typed hosted specs unless mapped.

### Event and Item Mapping

Extend `ItemTypes` if needed:

- `HOSTED_TOOL_CALL`
- `HOSTED_TOOL_RESULT`

Or use existing item values if already present. The normalized data should
include:

```python
{
    "item_type": provider_item_type,
    "hosted_tool_type": "web_search" | "file_search" | "code_interpreter" | ...,
    "status": ...,
    "query": ...,
    "results": ...,
}
```

Rules:

- Preserve raw provider payload on `event.raw`.
- For known hosted tool item types, emit typed run items.
- Unknown hosted tools still fall back to generic item events with raw payload.
- File/code artifacts should become `Artifact` objects only when the provider
  payload has a stable file/container reference. Otherwise preserve raw refs in
  event data and defer artifact normalization.

### Capability Updates

Capabilities should stay provider honest:

- `supports_hosted_tools=True` only means the provider can receive hosted tool
  configs.
- More granular support lives in a new helper:

```python
provider.supported_hosted_tools(model) -> set[str]
```

Phase 1 can implement this as optional duck-typed method, not a protocol
requirement.

### Tests

Unit:

- Hosted spec serialization for OpenAI.
- File search include behavior.
- Unsupported provider/tool combinations raise `UnsupportedFeatureError`.

Runtime/provider request:

- `runtime.models.run(..., hosted_tools=[WebSearch()])` sends OpenAI hosted
  tool in `kwargs["tools"]`.
- Local tools and hosted tools both appear in provider payload but stay
  separately configured in the public API.
- High-level `runtime.run(..., tools=[...], hosted_tools=[...])` forwards both.

Golden event mapping:

- Web search call maps to hosted tool run item.
- File search call/result maps to hosted tool run item and preserves raw data.
- Unknown hosted item still falls back to generic event.

Docs/catalog:

- `README.md`: add hosted tool example.
- `FEATURES.md`: hosted tool specs become Supported for OpenAI phase 1.
- `tests/VALIDATION.md`: add hosted tool spec and event mapping rows.

## Implementation Sequence

Commit 1: schema conversion groundwork

- Add `OutputSchema` and conversion helpers.
- Add tests.

Commit 2: provider-native structured output wiring

- Extend `TurnRequest`.
- Wire OpenAI and Gemini request payloads.
- Add capability updates and tests.

Commit 3: finalizer tool strategy

- Add hidden finalizer tool injection.
- Teach `AgentLoop` to terminate on finalizer tool call.
- Add runtime tests.

Commit 4: hosted tool specs and OpenAI mapping

- Add hosted tool dataclasses.
- Extend runtime/model APIs with `hosted_tools`.
- Map specs into OpenAI Responses kwargs.
- Add request tests.

Commit 5: hosted tool event normalization

- Map known hosted output item types into run items.
- Preserve fallback for unknown hosted tools.
- Add golden tests.

Each commit must pass:

```bash
pytest -q
ruff check .
mypy src tests
```

## Open Questions

1. Should xAI inherit OpenAI structured output once request-shape tests pass, or
   stay conservative until live smoke coverage exists?
2. Should `jsonschema` become an optional dependency for validating raw dict
   schemas locally?
3. Should finalizer-tool output be represented as a `ToolPayload`, a `RunItem`,
   or only `AgentResult.output`? Phase 1 should emit a `RunItem` and final
   output, but no app payload.
4. Should `CodeInterpreter` be in phase 1 or deferred until artifacts/files are
   normalized?
