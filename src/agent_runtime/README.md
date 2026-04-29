# agent_runtime Package Map

`agent_runtime` is the public SDK package. Its subpackages are organized around
runtime responsibilities, not around provider brands.

## Coding Agent Quickstart

Start with `src/agent_runtime/__init__.py`. It is the public export surface a
coding agent should inspect first when integrating the library.

Use these minimal offline examples as first references:

- `examples/minimal_model_turn.py`: direct `runtime.models.run(...)`.
- `examples/minimal_runtime_run.py`: high-level `AgentRuntime.run(...)` with
  typed output.
- `examples/minimal_local_agent.py`: `runtime.agents.run(...)` with the local
  agent provider.
- `examples/minimal_workspace.py`: `runtime.workspaces` local file and command
  operations.

Use these package READMEs for deeper subsystem context:

- `runtime/README.md`: orchestration facades and high-level run behavior.
- `providers/README.md`: provider protocols and adapter organization.
- `tools/README.md`: local Python tool registration and execution.
- `tools/hosted/README.md`: provider-hosted tools such as `WebSearch`,
  `FileSearch`, and `RemoteMCP`.
- `mcp/README.md`: MCP server specs, routing, connector, and trust policy.
- `workspaces/README.md`: workspace provider contracts.
- `workspace_agents/README.md`: packaged workspace-agent contracts.

Import guidance:

- Prefer `from agent_runtime import AgentRuntime, WebSearch, FileSearch` for
  public contracts.
- Import concrete providers from `agent_runtime.providers.model_adapters...`
  and `agent_runtime.providers.agent_adapters...`.
- Treat `agent_runtime.models` and `agent_runtime.agents` as compatibility
  namespaces unless you are maintaining old imports.

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
