from __future__ import annotations

import asyncio
import inspect
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from agent_runtime.tools.registry import ToolDefinition, ToolRegistry
from agent_runtime.tools.results import ToolResult


@dataclass(slots=True)
class ToolRuntime:
    registry: ToolRegistry
    context: Mapping[str, Any] = field(default_factory=dict)
    max_concurrent: int | None = None
    timeout: float | None = None

    async def call(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
        *,
        mock: bool = False,
    ) -> ToolResult:
        definition = self.registry.get(name)
        if mock:
            return ToolResult(
                content=f"Mocked execution for tool '{name}'.",
                metadata={"mock": True, "tool_name": name},
            )
        return await self._execute(definition, arguments or {})

    async def call_many(
        self,
        calls: list[tuple[str, dict[str, Any]]],
        *,
        mock: bool = False,
    ) -> list[ToolResult | BaseException]:
        if self.max_concurrent is None:
            return await asyncio.gather(
                *(self.call(name, args, mock=mock) for name, args in calls),
                return_exceptions=True,
            )

        semaphore = asyncio.Semaphore(self.max_concurrent)

        async def guarded(name: str, args: dict[str, Any]) -> ToolResult:
            async with semaphore:
                return await self.call(name, args, mock=mock)

        return await asyncio.gather(
            *(guarded(name, args) for name, args in calls),
            return_exceptions=True,
        )

    async def _execute(self, definition: ToolDefinition, arguments: dict[str, Any]) -> ToolResult:
        kwargs = self._inject_context(definition, arguments)
        coro = self._call_function(definition, kwargs)
        if self.timeout is not None:
            return await asyncio.wait_for(coro, timeout=self.timeout)
        return await coro

    async def _call_function(self, definition: ToolDefinition, kwargs: dict[str, Any]) -> ToolResult:
        if definition.blocking:
            raw = await asyncio.to_thread(definition.function, **kwargs)
        else:
            raw = definition.function(**kwargs)
            if inspect.isawaitable(raw):
                raw = await raw
        return self._coerce_result(raw)

    def _inject_context(self, definition: ToolDefinition, arguments: dict[str, Any]) -> dict[str, Any]:
        signature = inspect.signature(definition.function)
        kwargs = dict(arguments)
        for name in signature.parameters:
            if name not in kwargs and name in self.context:
                kwargs[name] = self.context[name]
        return kwargs

    @staticmethod
    def _coerce_result(raw: Any) -> ToolResult:
        if isinstance(raw, ToolResult):
            return raw
        if isinstance(raw, str):
            return ToolResult(content=raw)
        return ToolResult(content=str(raw), payload=raw)
