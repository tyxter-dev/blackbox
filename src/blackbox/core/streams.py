from __future__ import annotations

import asyncio
from typing import TypeVar

T = TypeVar("T")

DEFAULT_STREAM_QUEUE_MAXSIZE = 64


def bounded_queue(maxsize: int = DEFAULT_STREAM_QUEUE_MAXSIZE) -> asyncio.Queue[T]:
    """Create a bounded async queue for provider stream pumps."""

    if maxsize <= 0:
        raise ValueError("stream queue maxsize must be positive")
    return asyncio.Queue(maxsize=maxsize)
