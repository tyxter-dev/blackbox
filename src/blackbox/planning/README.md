# planning

`planning` owns pre-execution run planning: resolving the effective tool set,
selecting tool/runtime-aware prompt fragments, building prompt bundles, and
checking prompt/tool parity before a provider call is made.

## Belongs Here

- `ResolvedRunSpec`, the single source of truth for a planned run.
- Prompt fragments, selectors, requirements, bundles, and cache sections.
- Prompt composition and parity validation.
- Runtime planning metadata used by traces, tests, and dry-run APIs.

## Does Not Belong Here

- Domain-specific prompt pack content.
- Provider adapter request/event mapping.
- Local tool execution.
- Workspace backend implementation.

## Compatibility

The old `blackbox.core.prompts` and `blackbox.core.run_plan` modules
remain as re-export shims during the transition.
