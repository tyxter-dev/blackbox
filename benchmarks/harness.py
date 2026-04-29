from __future__ import annotations

import asyncio
import json
import platform
import statistics
import sys
import time
import tracemalloc
from collections.abc import Awaitable, Callable, Coroutine, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

BudgetSeverity = Literal["ok", "warn", "fail"]
MetricValue = int | float | None


BENCHMARK_SCHEMA_VERSION = 1


DEFAULT_RESULT_PATH = Path("benchmarks/results/perf_results.json")
DEFAULT_BUDGET_PATH = Path("benchmarks/budgets.json")


@dataclass(slots=True, frozen=True)
class BenchmarkBudget:
    """Warn/fail thresholds for one benchmark scenario."""

    scenario: str
    warn: dict[str, float] = field(default_factory=dict)
    fail: dict[str, float] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, scenario: str, payload: Mapping[str, Any]) -> BenchmarkBudget:
        warn = _float_mapping(payload.get("warn"))
        fail = _float_mapping(payload.get("fail"))
        return cls(scenario=scenario, warn=warn, fail=fail)


@dataclass(slots=True, frozen=True)
class BudgetCheck:
    metric: str
    value: float
    severity: BudgetSeverity
    warn_above: float | None = None
    fail_above: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric": self.metric,
            "value": self.value,
            "severity": self.severity,
            "warn_above": self.warn_above,
            "fail_above": self.fail_above,
        }


@dataclass(slots=True)
class BenchmarkResult:
    scenario: str
    metrics: dict[str, MetricValue]
    mode: Literal["offline", "network"] = "offline"
    success: bool = True
    budget_checks: list[BudgetCheck] = field(default_factory=list)
    started_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    duration_ms: float | None = None
    tags: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario": self.scenario,
            "mode": self.mode,
            "success": self.success,
            "started_at": self.started_at,
            "duration_ms": self.duration_ms,
            "metrics": dict(self.metrics),
            "budget_checks": [check.to_dict() for check in self.budget_checks],
            "tags": list(self.tags),
            "metadata": dict(self.metadata),
            "error": self.error,
        }

    @property
    def failed_budget_checks(self) -> list[BudgetCheck]:
        return [check for check in self.budget_checks if check.severity == "fail"]

    @property
    def warned_budget_checks(self) -> list[BudgetCheck]:
        return [check for check in self.budget_checks if check.severity == "warn"]


@dataclass(slots=True)
class BenchmarkSuiteResult:
    results: list[BenchmarkResult]
    started_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    schema_version: int = BENCHMARK_SCHEMA_VERSION
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "started_at": self.started_at,
            "environment": {
                "python": sys.version.split()[0],
                "platform": platform.platform(),
                "implementation": platform.python_implementation(),
            },
            "metadata": dict(self.metadata),
            "results": [result.to_dict() for result in self.results],
            "summary": self.summary(),
        }

    def summary(self) -> dict[str, Any]:
        failed = [
            result.scenario
            for result in self.results
            if not result.success or result.failed_budget_checks
        ]
        warned = [
            result.scenario
            for result in self.results
            if result.warned_budget_checks and result.scenario not in failed
        ]
        return {
            "scenario_count": len(self.results),
            "failed_count": len(failed),
            "warned_count": len(warned),
            "failed": failed,
            "warned": warned,
        }


class BenchmarkRecorder:
    """Collect common latency and memory metrics for one scenario."""

    def __init__(self, scenario: str) -> None:
        self.scenario = scenario
        self._start = time.perf_counter()
        self._first_event_at: float | None = None
        self._final_output_at: float | None = None
        self._memory_start: int | None = None
        self.metrics: dict[str, MetricValue] = {}
        self.metadata: dict[str, Any] = {}

    def __enter__(self) -> BenchmarkRecorder:
        if not tracemalloc.is_tracing():
            tracemalloc.start()
        self._memory_start = tracemalloc.get_traced_memory()[0]
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.finish()

    def observe_event(self) -> None:
        if self._first_event_at is None:
            self._first_event_at = time.perf_counter()

    def mark_final_output(self) -> None:
        self._final_output_at = time.perf_counter()

    def record(self, metric: str, value: MetricValue) -> None:
        self.metrics[metric] = _round_metric(value)

    def record_count(self, metric: str, values: Iterable[Any]) -> None:
        self.metrics[metric] = len(list(values))

    def finish(self) -> None:
        now = time.perf_counter()
        self.metrics.setdefault("wall_time_ms", _elapsed_ms(self._start, now))
        if self._first_event_at is not None:
            self.metrics.setdefault(
                "time_to_first_event_ms",
                _elapsed_ms(self._start, self._first_event_at),
            )
        if self._final_output_at is not None:
            self.metrics.setdefault(
                "time_to_final_output_ms",
                _elapsed_ms(self._start, self._final_output_at),
            )
        if tracemalloc.is_tracing() and self._memory_start is not None:
            current = tracemalloc.get_traced_memory()[0]
            growth = max(0, current - self._memory_start)
            self.metrics.setdefault("memory_growth_kib", round(growth / 1024, 3))


async def time_async(action: Callable[[], Awaitable[Any]]) -> tuple[Any, float]:
    started = time.perf_counter()
    result = await action()
    return result, _elapsed_ms(started, time.perf_counter())


def time_sync(action: Callable[[], Any]) -> tuple[Any, float]:
    started = time.perf_counter()
    result = action()
    return result, _elapsed_ms(started, time.perf_counter())


async def run_benchmark(
    scenario: str,
    action: Callable[[BenchmarkRecorder], Awaitable[dict[str, MetricValue] | None]],
    *,
    mode: Literal["offline", "network"] = "offline",
    budgets: Mapping[str, BenchmarkBudget] | None = None,
    tags: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> BenchmarkResult:
    started = time.perf_counter()
    recorder = BenchmarkRecorder(scenario)
    success = True
    error: str | None = None
    try:
        with recorder:
            extra_metrics = await action(recorder)
            if extra_metrics:
                for name, value in extra_metrics.items():
                    recorder.record(name, value)
    except Exception as exc:
        recorder.finish()
        success = False
        error = f"{type(exc).__name__}: {exc}"
    result = BenchmarkResult(
        scenario=scenario,
        mode=mode,
        success=success,
        metrics=dict(recorder.metrics),
        duration_ms=_elapsed_ms(started, time.perf_counter()),
        tags=list(tags or []),
        metadata={**recorder.metadata, **dict(metadata or {})},
        error=error,
    )
    if budgets is not None:
        result.budget_checks = evaluate_budget(result, budgets.get(scenario))
    return result


async def run_many(
    scenarios: Iterable[tuple[str, Callable[[BenchmarkRecorder], Awaitable[dict[str, MetricValue] | None]]]],
    *,
    budgets: Mapping[str, BenchmarkBudget] | None = None,
    mode: Literal["offline", "network"] = "offline",
    tags: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> BenchmarkSuiteResult:
    results: list[BenchmarkResult] = []
    for name, action in scenarios:
        results.append(
            await run_benchmark(
                name,
                action,
                mode=mode,
                budgets=budgets,
                tags=tags,
                metadata=metadata,
            )
        )
    return BenchmarkSuiteResult(results=results, metadata=dict(metadata or {}))


def load_budgets(path: str | Path = DEFAULT_BUDGET_PATH) -> dict[str, BenchmarkBudget]:
    budget_path = Path(path)
    if not budget_path.exists():
        return {}
    payload = json.loads(budget_path.read_text(encoding="utf-8"))
    raw_scenarios = payload.get("scenarios", payload)
    if not isinstance(raw_scenarios, dict):
        raise ValueError("benchmark budget file must contain a mapping of scenarios")
    return {
        str(name): BenchmarkBudget.from_mapping(str(name), scenario_payload)
        for name, scenario_payload in raw_scenarios.items()
        if isinstance(scenario_payload, Mapping)
    }


def evaluate_budget(
    result: BenchmarkResult,
    budget: BenchmarkBudget | None,
) -> list[BudgetCheck]:
    if budget is None:
        return []
    checks: list[BudgetCheck] = []
    metric_names = sorted(set(budget.warn) | set(budget.fail))
    for metric in metric_names:
        raw_value = result.metrics.get(metric)
        if raw_value is None:
            continue
        value = float(raw_value)
        fail_above = budget.fail.get(metric)
        warn_above = budget.warn.get(metric)
        severity: BudgetSeverity = "ok"
        if fail_above is not None and value > fail_above:
            severity = "fail"
        elif warn_above is not None and value > warn_above:
            severity = "warn"
        checks.append(
            BudgetCheck(
                metric=metric,
                value=value,
                severity=severity,
                warn_above=warn_above,
                fail_above=fail_above,
            )
        )
    return checks


def write_json_report(
    suite: BenchmarkSuiteResult,
    path: str | Path = DEFAULT_RESULT_PATH,
) -> Path:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(suite.to_dict(), indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return output_path


def percentile(values: Iterable[float], pct: float) -> float | None:
    samples = sorted(values)
    if not samples:
        return None
    if len(samples) == 1:
        return round(samples[0], 3)
    index = (len(samples) - 1) * pct
    lower = int(index)
    upper = min(lower + 1, len(samples) - 1)
    weight = index - lower
    return round(samples[lower] * (1 - weight) + samples[upper] * weight, 3)


def aggregate_metric(results: Iterable[BenchmarkResult], metric: str) -> dict[str, float | None]:
    samples = [
        float(value)
        for result in results
        if (value := result.metrics.get(metric)) is not None
    ]
    if not samples:
        return {"min": None, "median": None, "p95": None, "max": None}
    return {
        "min": round(min(samples), 3),
        "median": round(statistics.median(samples), 3),
        "p95": percentile(samples, 0.95),
        "max": round(max(samples), 3),
    }


def run(coro: Coroutine[Any, Any, Any]) -> Any:
    return asyncio.run(coro)


def _elapsed_ms(started: float, ended: float) -> float:
    return round((ended - started) * 1000, 3)


def _round_metric(value: MetricValue) -> MetricValue:
    if isinstance(value, float):
        return round(value, 3)
    return value


def _float_mapping(value: Any) -> dict[str, float]:
    if not isinstance(value, Mapping):
        return {}
    return {
        str(key): float(raw)
        for key, raw in value.items()
        if isinstance(raw, int | float)
    }
