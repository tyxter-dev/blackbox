from __future__ import annotations

import json
import os
import warnings
from pathlib import Path

import pytest

from benchmarks.harness import load_budgets, run_many, write_json_report
from benchmarks.scenarios import credentialed_network_scenarios, offline_scenarios


@pytest.mark.perf
async def test_offline_performance_benchmarks_emit_json(tmp_path: Path) -> None:
    suite = await run_many(offline_scenarios(), budgets=load_budgets())
    output_path = write_json_report(suite, tmp_path / "perf_results.json")
    payload = json.loads(output_path.read_text(encoding="utf-8"))

    assert payload["schema_version"] == 1
    assert payload["summary"]["scenario_count"] == 10
    assert {result["scenario"] for result in payload["results"]} == {
        "text_only_run",
        "structured_output_run",
        "posthoc_parse_with_retry_run",
        "one_local_tool_call",
        "five_parallel_local_tool_calls",
        "dynamic_toolset_search_load",
        "mcp_tool_discovery_call",
        "workspace_read_write_patch_snapshot",
        "cloud_agent_session_stream_collection",
        "persisted_resume_jsonl_sqlite",
    }
    assert all(result["success"] for result in payload["results"])
    assert all("wall_time_ms" in result["metrics"] for result in payload["results"])
    assert all("memory_growth_kib" in result["metrics"] for result in payload["results"])

    failed_budget_scenarios = [
        result.scenario for result in suite.results if result.failed_budget_checks
    ]
    if failed_budget_scenarios and os.environ.get("AGENT_RUNTIME_PERF_STRICT") == "1":
        pytest.fail(f"performance budgets failed: {failed_budget_scenarios}")
    if failed_budget_scenarios:
        warnings.warn(
            f"performance budgets exceeded fail thresholds: {failed_budget_scenarios}",
            RuntimeWarning,
            stacklevel=2,
        )


@pytest.mark.perf
def test_network_benchmarks_are_credential_gated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    scenarios, skipped = credentialed_network_scenarios()

    assert scenarios == []
    assert skipped == ["openai_text_only_network: missing OPENAI_API_KEY"]
