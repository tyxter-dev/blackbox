from __future__ import annotations

from collections.abc import Iterable
from types import SimpleNamespace
from typing import Any


class FakeGenerateContentStream:
    def __init__(self, chunks: Iterable[Any]) -> None:
        self._chunks = list(chunks)
        self.kwargs: dict[str, Any] | None = None
        self._index = 0

    def __aiter__(self) -> FakeGenerateContentStream:
        self._index = 0
        return self

    async def __anext__(self) -> Any:
        if self._index >= len(self._chunks):
            raise StopAsyncIteration
        chunk = self._chunks[self._index]
        self._index += 1
        return chunk


class FakeAsyncModels:
    def __init__(self, client: FakeGeminiClient) -> None:
        self._client = client

    def generate_content_stream(self, **kwargs: Any) -> FakeGenerateContentStream:
        stream = self._client._streams.pop(0)
        stream.kwargs = kwargs
        self._client.calls.append(kwargs)
        return stream


class FakeAio:
    def __init__(self, client: FakeGeminiClient) -> None:
        self.models = FakeAsyncModels(client)


class FakeGeminiClient:
    def __init__(self) -> None:
        self._streams: list[FakeGenerateContentStream] = []
        self.calls: list[dict[str, Any]] = []
        self.aio = FakeAio(self)

    def queue(self, chunks: Iterable[Any]) -> FakeGenerateContentStream:
        stream = FakeGenerateContentStream(chunks)
        self._streams.append(stream)
        return stream


def chunk(
    *,
    response_id: str | None = None,
    parts: list[Any] | None = None,
    **fields: Any,
) -> SimpleNamespace:
    return SimpleNamespace(
        response_id=response_id,
        candidates=[SimpleNamespace(content=SimpleNamespace(parts=parts or []))],
        **fields,
    )


def text_part(text: str) -> SimpleNamespace:
    return SimpleNamespace(text=text)


def thought_part(text: str, *, thought_signature: str = "sig") -> SimpleNamespace:
    return SimpleNamespace(text=text, thought=True, thought_signature=thought_signature)


def function_call_part(
    *, id_: str, name: str, args: dict[str, Any]
) -> SimpleNamespace:
    return SimpleNamespace(
        function_call=SimpleNamespace(id=id_, name=name, args=args)
    )
