from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent_runtime.core.errors import ConfigurationError
from agent_runtime.planning.prompts import PromptBundle


@dataclass(slots=True)
class PromptRuntimeFacade:
    """Dry-run prompt composition facade over ``AgentRuntime.plan_run``."""

    runtime: Any

    async def build(self, **kwargs: Any) -> PromptBundle:
        plan = await self.runtime.plan_run(**kwargs)
        if plan.prompt is None:
            raise ConfigurationError("Prompt plan did not produce a prompt bundle.")
        return plan.prompt
