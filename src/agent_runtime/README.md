# agent_runtime Package Map

`agent_runtime` is the public SDK package. Its subpackages are organized around
runtime responsibilities, not around provider brands.

## Current Layout

- `core/`: stable primitive contracts shared across the runtime.
- `providers/`: provider protocols, provider registry, and provider adapter packages.
- `runtime/`: high-level orchestration facades, `AgentLoop`, and private runtime helpers.
- `planning/`: resolved run specs, prompt composition, and prompt/tool parity.
- `models/`: compatibility import namespace for model adapters.
- `agents/`: compatibility import namespace for agent adapters.
- `tools/`: local Python tools, hosted-tool specs/calls, catalogs, and tool sessions.
- `mcp/`: MCP server specs, auth, toolset routing, connector, cache, and transports.
- `workspaces/`: workspace provider contracts and local/sandbox/docker/cloud backends.
- `workspace_agents/`: governed workspace-agent package specs, permissions, schedules, registry, and serialization.
- `realtime/`: realtime session contracts, runtime, fake provider, and realtime provider adapters.
- `output/`: structured-output schema conversion and validation helpers.
- `observability/`: traces, replay, evals, and event sinks.
- `compat/`: migration and compatibility helpers.
- `integrations/`: optional third-party integration builders.

## Top-Level Modules

- `loop.py`: compatibility shim for `runtime/agent_loop.py`.
- `hosted_tools.py`: compatibility shim for hosted-tool specs now owned by
  `tools/hosted/specs.py`.

## Boundary Status

The current tree reflects the organization plan:

1. Large provider adapters are packages with stable public re-exports.
2. Runtime helper modules have responsibility names, including
   `output.py`, `run_planning.py`, `event_metadata.py`,
   `workspace_results.py`, and `session_results.py`.
3. Unit tests mirror source package ownership under `tests/unit/`.
