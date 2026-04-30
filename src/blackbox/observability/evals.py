from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from inspect import isawaitable
from typing import Any, Protocol
from uuid import uuid4

from blackbox.core.events import AgentEvent, EventTypes
from blackbox.observability.traces import SpanKinds, Trace


@dataclass(slots=True, frozen=True)
class EvaluationResult:
    name: str
    passed: bool
    score: float | None = None
    reason: str | None = None
    labels: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


class Evaluator(Protocol):
    """Protocol for trace evaluators that return an EvaluationResult."""

    name: str

    def evaluate(self, trace: Trace) -> EvaluationResult | Awaitable[EvaluationResult]: ...


EvaluatorHook = Callable[[Trace], EvaluationResult | Awaitable[EvaluationResult]]


@dataclass(slots=True, frozen=True)
class EvaluationReport:
    trace_id: str
    results: list[EvaluationResult]
    events: list[AgentEvent]

    @property
    def passed(self) -> bool:
        return all(result.passed for result in self.results)


async def evaluate_trace(
    trace: Trace,
    evaluators: Iterable[Evaluator | EvaluatorHook],
    *,
    run_id: str | None = None,
) -> EvaluationReport:
    """Run evaluators against a trace and return a report plus lifecycle events.

    Evaluator exceptions are captured as failed results so later evaluators run.
    """
    effective_run_id = run_id or _trace_run_id(trace)
    events: list[AgentEvent] = []
    results: list[EvaluationResult] = []
    parent_span_id = trace.spans[0].span_id if trace.spans else None
    sequence = 0

    for evaluator in evaluators:
        eval_name = _evaluator_name(evaluator)
        eval_id = f"eval_{uuid4().hex}"
        span_id = f"span_{uuid4().hex}"
        events.append(
            AgentEvent(
                type=EventTypes.EVAL_STARTED,
                run_id=effective_run_id,
                sequence=sequence,
                trace_id=trace.id,
                span_id=span_id,
                parent_span_id=parent_span_id,
                span_kind=SpanKinds.EVAL,
                item_id=eval_id,
                data={"eval_id": eval_id, "name": eval_name},
            )
        )
        sequence += 1
        try:
            result = await _call_evaluator(evaluator, trace)
        except Exception as exc:
            result = EvaluationResult(
                name=eval_name,
                passed=False,
                reason=str(exc),
                metadata={"error_type": type(exc).__name__},
            )
            event_type = EventTypes.EVAL_FAILED
        else:
            event_type = EventTypes.EVAL_COMPLETED
        results.append(result)
        events.append(
            AgentEvent(
                type=event_type,
                run_id=effective_run_id,
                sequence=sequence,
                trace_id=trace.id,
                span_id=span_id,
                parent_span_id=parent_span_id,
                span_kind=SpanKinds.EVAL,
                item_id=eval_id,
                data={
                    "eval_id": eval_id,
                    "name": result.name,
                    "passed": result.passed,
                    "score": result.score,
                    "reason": result.reason,
                    "labels": dict(result.labels),
                    "metadata": dict(result.metadata),
                },
            )
        )
        sequence += 1

    return EvaluationReport(trace_id=trace.id, results=results, events=events)


async def _call_evaluator(
    evaluator: Evaluator | EvaluatorHook,
    trace: Trace,
) -> EvaluationResult:
    if hasattr(evaluator, "evaluate"):
        candidate = evaluator.evaluate(trace)
    else:
        candidate = evaluator(trace)
    if isawaitable(candidate):
        return await candidate
    return candidate


def _evaluator_name(evaluator: Evaluator | EvaluatorHook) -> str:
    name = getattr(evaluator, "name", None)
    if isinstance(name, str) and name:
        return name
    callable_name = getattr(evaluator, "__name__", None)
    return callable_name if isinstance(callable_name, str) else "evaluator"


def _trace_run_id(trace: Trace) -> str | None:
    for event in trace.events:
        if event.run_id is not None:
            return event.run_id
    return None
