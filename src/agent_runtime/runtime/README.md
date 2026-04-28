# runtime

`runtime` owns orchestration facades: direct model turns, high-level blackbox
agent runs, agent sessions, workspace lifecycle routing, prompt dry-runs, local
tool facade access, and provider cache lifecycle helpers.

## Belongs Here

- `AgentRuntime`, the main public entrypoint.
- `AgentLoop`, the shared autonomous model/tool execution loop.
- Facades that coordinate existing domain packages.
- Runtime-only helper functions for metadata collection, output validation,
  prompt planning events, and workspace/session result assembly.

## Does Not Belong Here

- Provider adapter request/event mapping.
- Local tool implementation details.
- Workspace backend implementation details.
- MCP transport/client internals.
- Domain-specific prompt packs or business workflow rules.

## File Map

- `main.py`: `AgentRuntime` and high-level run/stream/plan orchestration.
- `agent_loop.py`: shared autonomous model/tool loop used by high-level runs
  and local agent sessions.
- `model.py`: direct model-turn facade.
- `agents.py`: provider-managed/local agent-session facade.
- `workspaces.py`: workspace provider registry/lifecycle facade.
- `chat.py`: explicit chat compatibility facade.
- `prompting.py`: prompt dry-run facade.
- `tools.py`: local tool registry facade.
- `caches.py`: provider cache lifecycle facade.
- `output.py`: output spec resolution, finalizer payloads, repair prompts, and
  output validation.
- `run_planning.py`: prompt spec normalization, resolved tool/MCP planning, and
  prompt planning event construction.
- `workspace_results.py`: workspace event enrichment and workspace metadata
  extraction from runtime events.
- `_helpers.py`: private shared runtime helpers that have not yet been split
  by responsibility.
