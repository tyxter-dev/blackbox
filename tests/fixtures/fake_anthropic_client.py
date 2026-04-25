"""In-memory fakes mimicking the Anthropic Messages streaming SDK.

The shape mirrors ``anthropic.AsyncAnthropic().messages.stream(**kwargs)``:
an async context manager that is also async-iterable over event objects, and
exposes ``await stream.get_final_message()`` once iteration completes.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any


@dataclass
class FakeStream:
    events: list[Any]
    final_message: Any = None
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

    async def get_final_message(self) -> Any:
        return self.final_message


@dataclass
class FakeMessages:
    queued_streams: list[FakeStream] = field(default_factory=list)
    seen_kwargs: list[dict[str, Any]] = field(default_factory=list)

    def stream(self, **kwargs: Any) -> FakeStream:
        self.seen_kwargs.append(kwargs)
        if not self.queued_streams:
            raise AssertionError("FakeMessages received a call but has no streams queued")
        next_stream = self.queued_streams.pop(0)
        next_stream.captured_kwargs = kwargs
        return next_stream


@dataclass
class FakeAnthropicClient:
    messages: FakeMessages = field(default_factory=FakeMessages)

    def queue(self, events: Iterable[Any], final_message: Any = None) -> FakeStream:
        stream = FakeStream(events=list(events), final_message=final_message)
        self.messages.queued_streams.append(stream)
        return stream


def evt(type_: str, **fields: Any) -> SimpleNamespace:
    return SimpleNamespace(type=type_, **fields)


def block_start(index: int, content_block: Any) -> SimpleNamespace:
    return evt("content_block_start", index=index, content_block=content_block)


def block_delta(index: int, delta: Any) -> SimpleNamespace:
    return evt("content_block_delta", index=index, delta=delta)


def block_stop(index: int) -> SimpleNamespace:
    return evt("content_block_stop", index=index)


def text_block(*, id_: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(type="text", id=id_)


def thinking_block(*, id_: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(type="thinking", id=id_)


def tool_use_block(*, id_: str, name: str) -> SimpleNamespace:
    return SimpleNamespace(type="tool_use", id=id_, name=name)


def hosted_block(type_: str, *, id_: str | None = None, **fields: Any) -> SimpleNamespace:
    return SimpleNamespace(type=type_, id=id_, **fields)


def text_delta(text: str) -> SimpleNamespace:
    return SimpleNamespace(type="text_delta", text=text)


def thinking_delta(text: str) -> SimpleNamespace:
    return SimpleNamespace(type="thinking_delta", thinking=text)


def input_json_delta(partial_json: str) -> SimpleNamespace:
    return SimpleNamespace(type="input_json_delta", partial_json=partial_json)


def final_message(
    *, id_: str, content: list[Any] | None = None, **fields: Any
) -> SimpleNamespace:
    return SimpleNamespace(id=id_, content=content or [], role="assistant", **fields)
