"""Environment workers: the inbound half of the connector.

Lab control planes enqueue agent sessions as work items; this package claims
and executes them inside the customer boundary under customer-owned policy.
See ``docs/ENVIRONMENT_WORKERS.md`` for the design analysis and
``src/blackbox/workers/README.md`` for usage.
"""
from blackbox.workers.anthropic import (
    AnthropicEnvironmentWorkSource,
    anthropic_sdk_session_handler,
)
from blackbox.workers.source import (
    WORKERS_POLLING_WINDOW,
    InMemoryWorkSource,
    WorkerCredentials,
    WorkItem,
    WorkItemStatus,
    WorkQueueStats,
    WorkResult,
    WorkResultStatus,
    WorkSource,
)
from blackbox.workers.worker import (
    EnvironmentWorker,
    WorkerState,
    WorkerStatus,
    WorkHandler,
)

__all__ = [
    "WORKERS_POLLING_WINDOW",
    "AnthropicEnvironmentWorkSource",
    "EnvironmentWorker",
    "InMemoryWorkSource",
    "WorkHandler",
    "WorkItem",
    "WorkItemStatus",
    "WorkQueueStats",
    "WorkResult",
    "WorkResultStatus",
    "WorkSource",
    "WorkerCredentials",
    "WorkerState",
    "WorkerStatus",
    "anthropic_sdk_session_handler",
]
