from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from agent_runtime.core.approvals import ApprovalDecision
from agent_runtime.providers.base import AgentSpec, TaskSpec


@dataclass(slots=True)
class FakeClaudeCodeClient:
    events: list[dict[str, Any]] = field(default_factory=list)
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    approvals: list[tuple[str, ApprovalDecision]] = field(default_factory=list)
    cancelled: list[str] = field(default_factory=list)
    messages: list[tuple[str, str]] = field(default_factory=list)

    async def create_agent(self, spec: AgentSpec) -> dict[str, Any]:
        return {
            "id": f"agent_{spec.name}",
            "metadata": {"instructions": spec.instructions},
        }

    async def start_session(self, agent: Any, task: TaskSpec) -> dict[str, Any]:
        agent_id = getattr(agent, "id", agent)
        return {
            "id": "provider_sess_1",
            "runtime_session_id": "sess_runtime_1",
            "provider_invocation_id": "inv_initial",
            "status": "running",
            "workspace": {"kind": "local", "root": "."},
            "metadata": {"agent_id": agent_id, "task": task.prompt},
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
            yield event | {"provider_session_id": provider_session_id}

    async def send_message(self, provider_session_id: str, message: str) -> dict[str, Any]:
        self.messages.append((provider_session_id, message))
        return {"id": "inv_followup", "provider_session_id": provider_session_id}

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
        items = [
            artifact | {"provider_session_id": provider_session_id}
            for artifact in self.artifacts
            if type is None or artifact.get("type") == type
        ]
        return {"items": items[:limit], "has_more": len(items) > limit}

