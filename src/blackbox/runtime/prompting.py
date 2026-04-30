from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

from blackbox.core.errors import ConfigurationError
from blackbox.planning.prompts import PromptBundle


@dataclass(slots=True)
class PromptRuntimeFacade:
    """Dry-run prompt composition facade over ``AgentRuntime.plan_run``."""

    runtime: Any

    async def build(self, **kwargs: Any) -> PromptBundle:
        plan = await self.runtime.plan_run(**kwargs)
        if plan.prompt is None:
            raise ConfigurationError("Prompt plan did not produce a prompt bundle.")
        return cast(PromptBundle, plan.prompt)
