# observability

`observability` owns runtime inspection: traces, replay, diffs, event sinks, and
evaluation helpers.

## Belongs Here

- Trace/span records and trace construction helpers.
- Event sinks and OpenTelemetry integration.
- Production observability presets for trace, metric, and redacted log backends.
- Replay and diff helpers for run traces.
- Lightweight evaluation records and reports.

## Production Preset

`ObservabilityPreset.production(...)` wires runtime event logging, trace export,
and metric export from one `AgentRuntime(observability=...)` switch. Production
presets redact raw provider payloads by default, attach provider request and
session IDs to spans, and emit standardized operation names such as
`agent.run`, `model.turn`, `tool.call`, `mcp.list_tools`,
`workspace.command`, `approval.wait`, `cache.lookup`, and `artifact.write`.

## Does Not Belong Here

- Provider adapter event mapping.
- Business-domain benchmark cases.
- Runtime control flow decisions.

## Boundary Note

Runtime modules should emit canonical events and metadata. Observability modules
should consume those records, not own the behavior that produces them.
