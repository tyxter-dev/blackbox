---
status: active
owner: blackbox-src
since: 2026-06-27
adr:
  - docs/adr/0002-chat-messages-are-a-projection.md
  - docs/adr/0003-separate-model-and-agent-protocols.md
  - docs/adr/0007-four-output-strategies-fail-fast.md
  - docs/adr/0008-colon-routing-separator.md
  - docs/adr/0010-workspaces-and-persistence-in-core.md
prd:
  - docs/prd/01-architecture.md
  - docs/prd/02-blackbox-loop.md
  - docs/prd/09-approvals-and-policy.md
  - docs/prd/10-structured-output.md
  - docs/prd/13-configuration.md
---

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
- `session_results.py`: agent task specs, event text/message/artifact extraction,
  session result status, and session provider-state reconstruction.
- `tools.py`: local tool registry facade.
- `caches.py`: provider cache lifecycle facade.
- `event_metadata.py`: MCP, hosted-tool, prompt, cache, tool-usage, and
  accounting metadata extraction from runtime events.
- `output.py`: output spec resolution, finalizer payloads, repair prompts, and
  output validation.
- `run_planning.py`: prompt spec normalization, resolved tool/MCP planning, and
  prompt planning event construction.
- `workspace_results.py`: workspace event enrichment and workspace metadata
  extraction from runtime events.
