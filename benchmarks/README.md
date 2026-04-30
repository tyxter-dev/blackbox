# Performance Benchmarks

This suite tracks latency and regression risk for the runtime surfaces most
likely to affect interactive agent applications. The default suite is fully
offline and uses fake model, agent, MCP, and workspace providers.

## Run

```bash
python benchmarks/run_perf.py --output benchmarks/results/perf_results.json
python benchmarks/run_perf.py --repeat 5 --output benchmarks/results/perf_results.json
python -m pytest tests/perf -q
```

Use strict mode when CI should fail on budget fail-threshold breaches:

```bash
AGENT_RUNTIME_PERF_STRICT=1 python -m pytest tests/perf -q
python benchmarks/run_perf.py --strict
```

Network provider probes are opt-in and credential-gated:

```bash
OPENAI_API_KEY=... python benchmarks/run_perf.py --include-network
```

Without `--include-network`, no benchmark attempts network access. With
`--include-network`, scenarios without required credentials are skipped and
listed in the JSON report metadata.

## Scenarios

The offline CI suite covers:

1. `text_only_run`
2. `structured_output_run`
3. `posthoc_parse_with_retry_run`
4. `one_local_tool_call`
5. `five_parallel_local_tool_calls`
6. `dynamic_toolset_search_load`
7. `mcp_tool_discovery_call`
8. `workspace_read_write_patch_snapshot`
9. `cloud_agent_session_stream_collection`
10. `persisted_resume_jsonl_sqlite`

## Metrics

Every scenario emits JSON with:

- `wall_time_ms`: total scenario duration.
- `event_count`: normalized runtime events produced by the scenario.
- `event_store_write_ms`: append overhead for the emitted event set.
- `memory_growth_kib`: traced Python heap growth during the scenario.
- `time_to_first_event_ms`: elapsed time before the first observed event.
- `time_to_final_output_ms`: elapsed time before final output collection.
- `trace_export_ms`: trace projection/export preparation overhead.

Scenario-specific metrics include:

- `tool_dispatch_max_concurrency`
- `tool_search_ms`
- `tool_load_ms`
- `mcp_discovery_ms`
- `mcp_cache_hit_ms`
- `mcp_call_ms`
- `output_validation_ms`
- `artifact_serialization_ms`
- `jsonl_resume_ms`
- `sqlite_resume_ms`

## Budgets

Latency budgets live in `benchmarks/budgets.json`. Each scenario can define:

- `warn`: threshold that creates a warning in reports/tests.
- `fail`: threshold that marks a budget check as failed.

The pytest guard always runs offline and emits a warning for fail-threshold
breaches by default. Set `AGENT_RUNTIME_PERF_STRICT=1` to fail CI on those
breaches. The CLI uses the same budgets and fails on scenario errors; pass
`--strict` or set `AGENT_RUNTIME_PERF_STRICT=1` to also fail on budget checks.

## Trend JSON

The report shape is stable for trend tracking:

```json
{
  "schema_version": 1,
  "environment": {"python": "3.12.0", "platform": "..."},
  "results": [
    {
      "scenario": "text_only_run",
      "mode": "offline",
      "success": true,
      "metrics": {"wall_time_ms": 10.0},
      "budget_checks": []
    }
  ],
  "trends": {
    "text_only_run": {
      "wall_time_ms": {"samples": 5, "p50": 10.0, "p95": 12.0}
    }
  },
  "summary": {"scenario_count": 10, "failed_count": 0}
}
```

Trend values are calculated per scenario from the JSON results in the current
report. A normal single-run CI report has one sample per scenario; local
`--repeat N` runs add enough samples for p50/p95 movement before changing
strict budgets.

## Optimization Watchlist

Use these benchmarks to evaluate the current P0 optimization targets:

- Cache generated tool schemas and output schemas.
- Cache MCP discovery by server identity, protocol, auth shape, and filters.
- Avoid copying large raw provider payloads unless retention is enabled.
- Batch event-store writes for high-volume streams.
- Add bounded queues/backpressure for streaming consumers.
- Precompute capability/profile validation where possible.
- Lazily materialize large workspace artifacts.

The strict CI gate must keep explicit fail budgets for `text_only_run`,
`structured_output_run`, `five_parallel_local_tool_calls`,
`dynamic_toolset_search_load`, and `mcp_tool_discovery_call`, including
memory-growth budgets for each and first-event budgets for tool-heavy streams.
