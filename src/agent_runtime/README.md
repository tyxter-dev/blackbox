# agent_runtime Package Map

`agent_runtime` is the public SDK package. Its subpackages are organized around
runtime responsibilities, not around provider brands.

## Current Layout

- `core/`: stable primitive contracts shared across the runtime.
- `providers/`: provider protocols and provider registry.
- `models/`: model-provider adapters such as OpenAI Responses and Anthropic Messages.
- `agents/`: agent-provider adapters such as local agents and cloud coding agents.
- `tools/`: local Python tools, tool catalogs, tool sessions, and client-executed hosted tool handling.
- `mcp/`: MCP server specs, auth, toolset routing, connector, cache, and transports.
- `workspaces/`: workspace provider contracts and local/sandbox/docker/cloud backends.
- `workspace_agents/`: governed workspace-agent package specs, permissions, schedules, registry, and serialization.
- `realtime/`: realtime session contracts, runtime, fake provider, and realtime provider adapters.
- `output/`: structured-output schema conversion and validation helpers.
- `observability/`: traces, replay, evals, and event sinks.
- `compat/`: migration and compatibility helpers.
- `integrations/`: optional third-party integration builders.

## Top-Level Modules

- `runtime.py`: high-level runtime facade and subfacades. This is intentionally
  still a single module until the runtime package split happens.
- `loop.py`: local model/tool loop used by high-level runs and local agent sessions.
- `hosted_tools.py`: typed hosted-tool specs. This should move under
  `tools/hosted/` once compatibility re-exports are in place.

## Planned Boundary Cleanup

The next structural refactors should be mechanical and compatibility-preserving:

1. Split `runtime.py` into a `runtime/` package.
2. Move prompt/run planning from `core/` into a `planning/` package.
3. Move hosted-tool specs from `hosted_tools.py` into `tools/hosted/specs.py`.
4. Rename adapter folders so provider contracts and provider implementations
   are easier to distinguish.
