from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    _ROOT = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(_ROOT))
    sys.path.insert(0, str(_ROOT / "src"))

from benchmarks.harness import (
    DEFAULT_BUDGET_PATH,
    DEFAULT_RESULT_PATH,
    BenchmarkSuiteResult,
    load_budgets,
    run,
    run_many,
    write_json_report,
)
from benchmarks.scenarios import (
    ScenarioEntry,
    credentialed_network_scenarios,
    offline_scenarios,
)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    budgets = load_budgets(args.budgets)
    scenarios = _select_scenarios(offline_scenarios(), args.scenario)
    metadata: dict[str, Any] = {"network_requested": args.include_network}
    network_skipped: list[str] = []
    if args.include_network:
        network_scenarios, network_skipped = credentialed_network_scenarios()
        scenarios.extend(_select_scenarios(network_scenarios, args.scenario))
        metadata["network_skipped"] = network_skipped
    suite = run(
        run_many(
            scenarios,
            budgets=budgets,
            tags=["perf", "offline"],
            metadata=metadata,
        )
    )
    if network_skipped:
        suite.metadata["network_skipped"] = network_skipped
    output = write_json_report(suite, args.output)
    _print_summary(suite, output=output, strict=_strict_enabled(args))
    return _exit_code(suite, strict=_strict_enabled(args))


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run agent-runtime performance benchmarks and emit JSON.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_RESULT_PATH,
        help="JSON report path for trend tracking.",
    )
    parser.add_argument(
        "--budgets",
        type=Path,
        default=DEFAULT_BUDGET_PATH,
        help="Latency budget JSON file.",
    )
    parser.add_argument(
        "--scenario",
        action="append",
        default=[],
        help="Scenario name to run. May be repeated. Defaults to all offline scenarios.",
    )
    parser.add_argument(
        "--include-network",
        action="store_true",
        help="Include credential-gated network provider scenarios.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero when a budget check reaches fail severity.",
    )
    return parser.parse_args(argv)


def _select_scenarios(
    scenarios: list[ScenarioEntry],
    selected: list[str],
) -> list[ScenarioEntry]:
    if not selected:
        return list(scenarios)
    wanted = set(selected)
    return [entry for entry in scenarios if entry[0] in wanted]


def _strict_enabled(args: argparse.Namespace) -> bool:
    return bool(args.strict or os.environ.get("AGENT_RUNTIME_PERF_STRICT") == "1")


def _exit_code(suite: BenchmarkSuiteResult, *, strict: bool) -> int:
    if any(not result.success for result in suite.results):
        return 1
    if strict and any(result.failed_budget_checks for result in suite.results):
        return 1
    return 0


def _print_summary(
    suite: BenchmarkSuiteResult,
    *,
    output: Path,
    strict: bool,
) -> None:
    summary = suite.summary()
    print(f"wrote {output}")
    print(
        "scenarios={scenario_count} failed={failed_count} warned={warned_count}".format(
            **summary
        )
    )
    for result in suite.results:
        status = "ok"
        if not result.success:
            status = "error"
        elif result.failed_budget_checks:
            status = "fail" if strict else "warn"
        elif result.warned_budget_checks:
            status = "warn"
        wall = result.metrics.get("wall_time_ms")
        print(f"{status:>5} {result.scenario} wall_time_ms={wall}")
        if result.error:
            print(f"      {result.error}")
        for check in [*result.failed_budget_checks, *result.warned_budget_checks]:
            print(
                f"      {check.severity}: {check.metric}={check.value} "
                f"(warn>{check.warn_above}, fail>{check.fail_above})"
            )
    skipped = suite.metadata.get("network_skipped")
    if isinstance(skipped, list) and skipped:
        for item in skipped:
            print(f"skip network {item}")


if __name__ == "__main__":
    raise SystemExit(main())
