from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from blackbox.core.approvals import ApprovalDecision
from blackbox.providers.base import AgentSpec, TaskSpec


@dataclass(slots=True)
class FakeCodexAppServerClient:
    """Scriptable app-server fake for offline Codex AgentProvider tests."""

    events: list[dict[str, Any]] = field(default_factory=list)
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    approvals: list[tuple[str, ApprovalDecision]] = field(default_factory=list)
    cancelled: list[str] = field(default_factory=list)
    messages: list[tuple[str, str]] = field(default_factory=list)
    started_tasks: list[TaskSpec] = field(default_factory=list)

    async def create_agent(self, spec: AgentSpec) -> dict[str, Any]:
        return {
            "id": f"codex_agent_{spec.name}",
            "metadata": {"instructions": spec.instructions},
        }

    async def start_session(self, agent: Any, task: TaskSpec) -> dict[str, Any]:
        self.started_tasks.append(task)
        return {
            "id": "thread_1",
            "provider_session_id": "thread_1",
            "status": "running",
            "model": task.model,
            "metadata": {"agent_id": getattr(agent, "id", agent), "task": task.prompt},
        }

    async def stream_events(
        self,
        provider_session_id: str,
        *,
        after_event_id: str | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        start = 0
        if after_event_id is not None:
            for index, event in enumerate(self.events):
                if event.get("id") == after_event_id:
                    start = index + 1
                    break
        for event in self.events[start:]:
            yield event

    async def send_message(self, provider_session_id: str, message: str) -> dict[str, str]:
        self.messages.append((provider_session_id, message))
        return {"id": "turn_followup", "provider_session_id": provider_session_id}

    async def approve(self, approval_id: str, decision: ApprovalDecision) -> None:
        self.approvals.append((approval_id, decision))

    async def cancel(self, provider_session_id: str) -> None:
        self.cancelled.append(provider_session_id)

    async def list_artifacts(
        self,
        provider_session_id: str,
        *,
        type: str | None = None,
        after: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        items = [artifact for artifact in self.artifacts if type is None or artifact.get("type") == type]
        return {"items": items[:limit], "has_more": len(items) > limit}
