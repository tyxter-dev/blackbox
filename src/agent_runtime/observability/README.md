# observability

`observability` owns runtime inspection: traces, replay, diffs, event sinks, and
evaluation helpers.

## Belongs Here

- Trace/span records and trace construction helpers.
- Event sinks and OpenTelemetry integration.
- Replay and diff helpers for run traces.
- Lightweight evaluation records and reports.

## Does Not Belong Here

- Provider adapter event mapping.
- Business-domain benchmark cases.
- Runtime control flow decisions.

## Boundary Note

Runtime modules should emit canonical events and metadata. Observability modules
should consume those records, not own the behavior that produces them.
