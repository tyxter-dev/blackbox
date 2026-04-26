from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any


@dataclass
class FakeStream:
    """Mimics openai.responses.stream(...) async context manager."""

    events: list[Any]
    final_response: Any = None
    final_error: Exception | None = None
    captured_kwargs: dict[str, Any] = field(default_factory=dict)

    async def __aenter__(self) -> FakeStream:
        return self

    async def __aexit__(self, *exc: Any) -> bool:
        return False

    def __aiter__(self) -> FakeStream:
        self._iter = iter(self.events)
        return self

    async def __anext__(self) -> Any:
        try:
            return next(self._iter)
        except StopIteration as exc:
            raise StopAsyncIteration from exc

    async def get_final_response(self) -> Any:
        if self.final_error is not None:
            raise self.final_error
        return self.final_response


@dataclass
class FakeResponses:
    queued_streams: list[FakeStream] = field(default_factory=list)
    seen_kwargs: list[dict[str, Any]] = field(default_factory=list)

    def stream(self, **kwargs: Any) -> FakeStream:
        self.seen_kwargs.append(kwargs)
        if not self.queued_streams:
            raise AssertionError("FakeResponses received a call but has no streams queued")
        next_stream = self.queued_streams.pop(0)
        next_stream.captured_kwargs = kwargs
        return next_stream


@dataclass
class FakeOpenAIClient:
    responses: FakeResponses = field(default_factory=FakeResponses)

    def queue(
        self,
        events: Iterable[Any],
        final_response: Any = None,
        final_error: Exception | None = None,
    ) -> FakeStream:
        stream = FakeStream(
            events=list(events),
            final_response=final_response,
            final_error=final_error,
        )
        self.responses.queued_streams.append(stream)
        return stream


def evt(type_: str, **fields: Any) -> SimpleNamespace:
    return SimpleNamespace(type=type_, **fields)


def item(type_: str, *, id_: str | None = None, **fields: Any) -> SimpleNamespace:
    return SimpleNamespace(type=type_, id=id_, **fields)


def final_response(
    *, id_: str, output: list[Any] | None = None, **fields: Any
) -> SimpleNamespace:
    return SimpleNamespace(id=id_, output=output or [], **fields)
