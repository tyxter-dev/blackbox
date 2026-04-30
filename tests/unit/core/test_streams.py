from __future__ import annotations

import asyncio

import pytest

from agent_runtime.core.streams import DEFAULT_STREAM_QUEUE_MAXSIZE, bounded_queue


async def test_bounded_queue_enforces_backpressure() -> None:
    queue: asyncio.Queue[int] = bounded_queue(maxsize=1)

    await queue.put(1)

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(queue.put(2), timeout=0.01)
    assert queue.maxsize == 1


def test_default_stream_queue_is_bounded() -> None:
    queue: asyncio.Queue[str] = bounded_queue()

    assert queue.maxsize == DEFAULT_STREAM_QUEUE_MAXSIZE
