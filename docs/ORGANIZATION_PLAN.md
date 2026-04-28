# Project Organization Plan

Status: active implementation  
Scope: package, test, and documentation organization only

## Implementation Progress

- Phase 0 is implemented: this plan exists and the package map documents the
  compatibility namespaces.
- Phase 1 is implemented in source-owned imports: tests, examples, and active
  docs now use canonical provider adapter paths except for targeted
  compatibility checks and migration notes.
- Phase 2 is implemented: `AgentLoop` lives in `runtime/agent_loop.py`, with
  `agent_runtime.loop` retained as a compatibility shim.
- Phase 3 is implemented: OpenAI Responses, Anthropic Messages, Gemini
  GenerateContent, and xAI Responses are packages with stable public
  re-exports. Remaining adapter package splits should move one at a time when
  they become large enough to justify it.
- Phase 4 is implemented: output-specific runtime helpers have been split into
  `runtime/output.py`, and workspace event/result helpers have been split into
  `runtime/workspace_results.py`. Run-planning helpers have been split into
  `runtime/run_planning.py`, and event/accounting metadata helpers have been
  split into `runtime/event_metadata.py`. The remaining generic helper module
  has been renamed to `runtime/session_results.py`.
- Phase 5 is implemented: `tests/unit` now mirrors source ownership for the
  package areas with unit coverage, and validation references point at the new
  paths.
- Phase 6 remains follow-up documentation alignment.

## Goal

Make the project tree explain the runtime architecture at a glance:

- `core` owns stable, provider-neutral contracts.
- `runtime` owns orchestration and the high-level blackbox loop.
- `providers` owns provider protocols, registry, and concrete adapters.
- `tools`, `mcp`, `workspaces`, and `workspace_agents` stay separate because
  they represent different execution and governance surfaces.

This plan should preserve existing behavior and public compatibility while
reducing structural ambiguity created by rushed feature work.

## Non-Goals

- Do not redesign `ModelProvider`, `AgentProvider`, or workspace contracts.
- Do not remove compatibility import paths in the same pass that introduces
  canonical paths.
- Do not merge cloud agents into model adapters or hosted tools into local
  tools.
- Do not reorganize by provider brand at the top level.

## Starting Friction

1. `agent_runtime.models` and `agent_runtime.agents` look first-class in the
   tree, but they are compatibility namespaces. Tests and docs still use them
   heavily, which makes the old layout feel canonical.
2. `agent_runtime.loop` owns core orchestration but sits at package root instead
   of under `runtime`.
3. Large modules such as provider adapters and `runtime/main.py` hide useful
   internal boundaries.
4. Before Phase 5, `tests/unit` was flat, so test location did not reinforce
   package ownership.
5. The top-level `agent_runtime.__init__` is broad enough that it can obscure
   the intended package boundaries.

## Target Shape

```text
src/agent_runtime/
  core/                  # stable primitives and shared contracts
  runtime/               # AgentRuntime, AgentLoop, facades, orchestration
  providers/
    base.py              # provider protocols and request/result contracts
    registry.py          # provider routing and registry
    model_adapters/      # direct ModelProvider implementations
      openai_responses/
      anthropic_messages/
      gemini_generate_content/
      xai_responses/
      echo.py
    agent_adapters/      # AgentProvider session implementations
      local/
      openai_cloud/
      claude_code/
      vertex_agent_engine/
  tools/                 # local tools, hosted-tool specs, tool sessions
  mcp/                   # MCP specs, clients, toolsets, transports
  workspaces/            # workspace providers and execution backends
  workspace_agents/      # governed workspace-agent package contracts
  planning/              # prompt/run planning before execution
  output/                # structured-output schemas and validation
  realtime/              # realtime runtime and realtime providers
  observability/         # traces, replay, evals, sinks
  integrations/          # optional third-party integration builders
  compat/                # explicit migration and compatibility helpers
  models/                # deprecated import shims only
  agents/                # deprecated import shims only
```

## Compatibility Policy

- Canonical new imports should use `agent_runtime.providers.model_adapters.*`
  and `agent_runtime.providers.agent_adapters.*`.
- `agent_runtime.models.*` and `agent_runtime.agents.*` remain as import shims
  for at least one release after the project stops using them internally.
- Root-level compatibility shims should contain re-exports only. They should
  not gain new implementation logic.
- Tests may keep a small compatibility test for old imports, but ordinary
  feature tests should use canonical paths.

## Phase 0: Guardrails

Purpose: document the intended structure before moving code.

Tasks:

- Add this organization plan.
- Keep `src/agent_runtime/README.md` as the package map and update it whenever a
  phase lands.
- Record canonical import paths in README examples and new docs.

Done when:

- The plan exists.
- The current package map identifies compatibility namespaces clearly.

## Phase 1: Canonicalize Imports

Purpose: make the current implementation read as if `providers` is the owner of
provider adapters.

Tasks:

- Update project-owned tests, examples, and docs from:
  - `agent_runtime.models.*` to `agent_runtime.providers.model_adapters.*`
  - `agent_runtime.agents.*` to `agent_runtime.providers.agent_adapters.*`
- Keep targeted compatibility tests for `agent_runtime.models.*` and
  `agent_runtime.agents.*`.
- Update PRD examples that still show old adapter imports.

Done when:

- `rg "agent_runtime\\.(models|agents)" tests examples docs README.md` only
  finds compatibility-specific references or migration documentation.
- Offline tests still pass.

## Phase 2: Move AgentLoop Under Runtime

Purpose: make the high-level execution loop visible under the orchestration
package.

Tasks:

- Move implementation from `src/agent_runtime/loop.py` to
  `src/agent_runtime/runtime/agent_loop.py`.
- Replace `src/agent_runtime/loop.py` with a compatibility re-export shim.
- Update source imports to use `agent_runtime.runtime.agent_loop`.
- Keep any old-path assertion in compatibility tests only.

Done when:

- `AgentLoop` implementation is under `runtime`.
- `agent_runtime.loop` contains no implementation logic.
- Runtime and local-agent tests still pass.

## Phase 3: Split Large Provider Adapters

Purpose: make provider adapter internals navigable without changing public
imports.

Recommended order:

1. OpenAI Responses, because it is the richest adapter and can become the
   template.
2. Anthropic Messages.
3. Gemini GenerateContent.
4. xAI Responses, if it grows beyond a thin OpenAI-compatible adapter.

Target pattern:

```text
providers/model_adapters/openai_responses/
  __init__.py          # public exports
  provider.py          # OpenAIResponsesProvider
  capabilities.py      # profile construction and feature gates
  requests.py          # request mapping
  events.py            # streaming event mapping
  tools.py             # hosted/local/MCP tool mapping helpers
  state.py             # provider state extraction and continuation
```

Rules:

- Keep the public import path stable:
  `from agent_runtime.providers.model_adapters.openai_responses import OpenAIResponsesProvider`.
- Move one adapter at a time.
- Prefer pure mechanical moves first, then small cleanup commits after tests
  prove parity.

Done when:

- Provider package imports remain stable.
- Golden and capability tests pass for the moved adapter.
- No moved adapter submodule depends on runtime orchestration internals.

## Phase 4: Slim Runtime Main and Helpers

Purpose: keep `AgentRuntime` readable as the public entrypoint.

Tasks:

- Keep `runtime/main.py` focused on `AgentRuntime` construction and public
  methods.
- Split private helper clusters by responsibility, for example:
  - `runtime/output.py` for output validation/finalization helpers.
  - `runtime/events.py` for event collection and persistence helpers.
  - `runtime/tool_loop.py` or `runtime/tooling.py` for local tool dispatch
    preparation.
  - `runtime/workspace_results.py` for workspace result assembly.
- Keep facades in their current files unless they grow enough to need packages.

Done when:

- `runtime/main.py` can be read as the public API flow.
- Private helper files have responsibility names instead of a generic
  `_helpers.py` catch-all.
- Runtime tests pass.

## Phase 5: Mirror Source Structure In Tests

Purpose: make tests reinforce ownership.

Target pattern:

```text
tests/unit/
  core/
  runtime/
  providers/
    model_adapters/
    agent_adapters/
  tools/
  mcp/
  workspaces/
  workspace_agents/
  output/
  planning/
  observability/
  realtime/
```

Tasks:

- Move unit tests by source package.
- Keep existing high-level directories for behavior categories:
  `tests/runtime`, `tests/contracts`, `tests/golden`, `tests/integration`, and
  `tests/journey`.
- Update any README or validation references that point at moved files.

Done when:

- A source package owner can find its unit tests by path.
- `tests/VALIDATION.md` references remain accurate.
- Pytest discovery still works without custom path hacks.

## Phase 6: Documentation Cleanup

Purpose: align the PRD, README, and package maps after code movement.

Tasks:

- Update `README.md` package layout after each structural phase.
- Update `src/agent_runtime/README.md` as the authoritative package map.
- Update `docs/PRD.md` section 8.2 after the target shape is true.
- Keep `CLAUDE.md` focused on behavioral constraints and canonical paths.
- Move historical layout notes into changelog entries rather than keeping stale
  architecture diagrams in active docs.

Done when:

- README, package map, and PRD describe the same layout.
- New contributors can identify where a model adapter, agent adapter, runtime
  loop change, workspace backend, or tool change belongs from the tree alone.

## Suggested First Implementation Slice

The first slice should be small and low risk:

1. Canonicalize imports in tests and examples.
2. Update docs that still show `agent_runtime.models.*` or
   `agent_runtime.agents.*` as preferred imports.
3. Keep compatibility shims untouched.
4. Run `pytest -q`.
5. Run `ruff check src tests examples`.

This slice improves readability immediately and creates a clean baseline before
moving implementation files.

## Review Checklist For Each Phase

- Does this move make the package tree closer to the PRD surfaces?
- Are old public imports preserved or intentionally scheduled for later removal?
- Did implementation logic move into a compatibility namespace by accident?
- Did tests move with the behavior they cover?
- Do README, package map, and PRD still agree?
