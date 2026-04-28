# agent_runtime Package Map

`agent_runtime` is the public SDK package. Its subpackages are organized around
runtime responsibilities, not around provider brands.

## Current Layout

- `core/`: stable primitive contracts shared across the runtime.
- `providers/`: provider protocols, provider registry, and provider adapter packages.
- `runtime/`: high-level orchestration facades and private runtime helpers.
- `planning/`: resolved run specs, prompt composition, and prompt/tool parity.
- `models/`: compatibility import namespace for model adapters.
- `agents/`: agent-provider adapters such as local agents and cloud coding agents.
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

- `loop.py`: local model/tool loop used by high-level runs and local agent sessions.
- `hosted_tools.py`: compatibility shim for hosted-tool specs now owned by
  `tools/hosted/specs.py`.

## Planned Boundary Cleanup

The next structural refactors should be mechanical and compatibility-preserving:

1. Move agent-provider adapters under `providers/agent_adapters/` while keeping
   compatibility imports under `agent_runtime.agents`.
