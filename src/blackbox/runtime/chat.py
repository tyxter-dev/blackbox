from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from blackbox.compat.chat import ChatMessage, messages_to_input
from blackbox.core.events import AgentEvent
from blackbox.providers.base import TurnResult
from blackbox.runtime.model import ModelRuntime


@dataclass(slots=True)
class ChatRuntimeFacade:
    """Explicit chat compatibility projection over the model runtime."""

    models: ModelRuntime

    async def stream(
        self,
        *,
        provider: str,
        messages: list[ChatMessage | dict[str, Any]],
        model: str | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[AgentEvent]:
        async for event in self.models.stream(
            provider=provider,
            model=model,
            input=messages_to_input(messages),
            **kwargs,
        ):
            yield event

    async def run(
        self,
        *,
        provider: str,
        messages: list[ChatMessage | dict[str, Any]],
        model: str | None = None,
        **kwargs: Any,
    ) -> TurnResult:
        return await self.models.run(
            provider=provider,
            model=model,
            input=messages_to_input(messages),
            **kwargs,
        )
