# WorkspaceProvider Journeys

These tests exercise the `WorkspaceProvider` framework as user-facing
journeys. They intentionally avoid deterministic assertions against model text.
Runtime-integrated journeys use real configured model providers, while direct
provider journeys exercise the lower-level workspace orchestration contract and
print structured reports for LLM review.

Run with:

```bash
pytest -m journey_workspace_provider -s tests/journey/WorkspaceProvider
```

## Covered Journeys

The suite covers these user-facing goals:

- `direct_local_workspace_operations`: a user opens a local workspace directly, lists and reads files, writes and deletes files, applies a patch, runs a command, snapshots the result, exports artifacts, and closes the workspace.
- `local_workspace_state_attach_and_restore`: a user serializes local workspace state, attaches from it in a later process, snapshots the workspace, and restores into a new local root.
- `direct_policy_gated_workspace_write`: a user protects workspace writes with policy, observes an approval-required operation, approves it, and retries the write successfully.
- `runtime_local_workspace_tool_loop`: a user passes a local workspace to `runtime.run`, lets a real model inspect/edit/run/snapshot through automatically registered workspace tools, and receives a final answer plus events.
- `runtime_workspace_write_approval_resume`: a user runs a real model with workspace write policy, approves the pending workspace operation, and lets the model continue.
- `runtime_sandbox_workspace_tool_loop`: a user passes a sandbox workspace provider to `runtime.run`, lets a real model inspect/edit/run/snapshot through the sandbox-backed tool contract, and receives a final answer.
- `direct_sandbox_workspace_ports_and_state`: a user uses a sandbox provider directly to open inputs, run commands, expose a preview port, snapshot, attach, restore, list artifacts, and close sessions.
- `workspace_provider_capability_report`: a user inspects capability flags before choosing local or sandbox workspace providers.
- `workspace_path_safety_report`: a user attempts path-escape operations and receives workspace errors instead of touching files outside the workspace.

Provider credentials for runtime-integrated journeys are read from the standard
model environment variables: `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`,
`GOOGLE_API_KEY` or `GEMINI_API_KEY`, and `XAI_API_KEY`.

Use `WORKSPACE_PROVIDER_JOURNEY_MODEL_PROVIDERS=openai,google` to limit the
model provider set. `MODEL_PROVIDER_JOURNEY_PROVIDERS` is also honored as a
fallback.
