# 10 — Feature: Structured Output

**Contracts:** `OutputSpec`, `OutputFallback` · **Package:**
`src/blackbox/output/` · **Related facade:** `runtime.prompts`

## Summary

Typed outputs are product surface, not decoration. A high-level `run(...)` can
take an `output_type` (Pydantic model, dataclass, or raw JSON Schema) and return
a validated `result.output` of that type. Validation strategy is selectable per
run via `OutputSpec`.

```python
class TicketDecision(BaseModel):
    should_escalate: bool
    priority: str
    summary: str

result = await runtime.run(input="...", tools=[...], output_type=TicketDecision)
decision: TicketDecision = result.output
```

## The four output strategies

Strategy selection lives on `OutputSpec`:

| Strategy | Behavior |
|---|---|
| `provider_native` | The provider supports strict schema output (e.g. OpenAI Responses `text.format`); the runtime wires the schema down. |
| `finalizer_tool` | The runtime exposes a hidden `submit_final_output` tool whose arguments are validated against the schema; the model finishes by calling it. |
| `posthoc_parse` (default in v0.1) | The runtime parses the final text after generation completes. |
| `posthoc_parse_with_retry` | Same as `posthoc_parse`, but on failure the runtime feeds a repair prompt back up to `max_validation_retries` times. |

`OutputFallback` controls behavior when the chosen strategy is unsupported by the
resolved provider.

## Validation semantics

- **Fail-fast by default.** Validation failures raise `OutputValidationError`,
  carrying the raw text and the underlying validator error so the application
  decides how to recover.
- **Retry is opt-in** via `posthoc_parse_with_retry`.
- **Validators:** Pydantic is the validator of choice; dataclasses and raw JSON
  Schema are also supported. `pydantic` is imported lazily inside
  `_validate_output`, so the runtime works without it unless a Pydantic
  `output_type` is passed (`validate` extra; `dev` extras include it).

## Schema helpers

`src/blackbox/output/` provides JSON-Schema conversion + validation helpers
shared across the strategies — the same machinery the `finalizer_tool` and
`provider_native` paths use to project a Python type into a provider schema.

## Prompt dry-run (related)

`runtime.prompts` composes a `PromptBundle` *without* invoking a model — useful
for inspecting exactly what (instructions, tool schemas, output schema) would be
sent. Prompt composition lives in `src/blackbox/planning/` (`PromptComposer`,
fragments, parity checks) and emits `prompt.bundle.created`.

## Requirements

| ID | Priority | Requirement |
|---|---|---|
| P0-R12 | P0 | `AgentResult[T]` exists; `OutputSpec` exposes all four strategies. |
| P1-R12 | P1 | Runtime validates final output against Pydantic models, dataclasses, JSON-Schema-like schemas, or provider-native mechanisms where available. |

## Hard constraints / settled decisions (don't re-litigate)

- Pydantic is the validator of choice; dataclasses also supported. All four
  strategies are implemented.
- Validation failures are fail-fast (`OutputValidationError`) except under
  `posthoc_parse_with_retry`.
- The runtime must function with zero hard dependencies — Pydantic stays an
  optional, lazily-imported extra.

## Status & references

All four strategies shipped. Example: `examples/run_with_typed_output.py`. Tests:
`tests/unit/output/`, `tests/runtime/`. PRD §10.7 (`OutputSpec`), §12 (P0-R12 /
P1-R12), §26 Q9–Q10 (settled in `ROADMAP.md` decision log).

→ Next: [11 — Observability](11-observability.md)
