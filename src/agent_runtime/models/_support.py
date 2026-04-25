from __future__ import annotations

import asyncio
import random
import re
from collections.abc import Mapping
from typing import Any

_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
_RE_RETRY_MS = re.compile(r"(?:try again in|retry after)\s+(\d+(?:\.\d+)?)\s*ms", re.I)
_RE_RETRY_S = re.compile(r"(?:try again in|retry after)\s+(\d+(?:\.\d+)?)\s*s", re.I)


def strip_private_fields(value: Any) -> Any:
    """Remove runtime-private keys before sending payloads to provider APIs."""
    if isinstance(value, list):
        return [strip_private_fields(item) for item in value]
    if isinstance(value, dict):
        return {
            key: strip_private_fields(item)
            for key, item in value.items()
            if not str(key).startswith("_")
        }
    return value


def is_retryable_status_error(error: Exception) -> bool:
    status = getattr(error, "status_code", None) or getattr(error, "code", None)
    if status is None:
        response = getattr(error, "response", None)
        status = getattr(response, "status_code", None)
    if status is not None:
        try:
            return int(status) in _RETRYABLE_STATUS_CODES
        except (TypeError, ValueError):
            return False
    name = type(error).__name__.lower()
    return any(part in name for part in ("timeout", "connection", "unavailable"))


def retry_after_seconds(error: Exception) -> float | None:
    headers = _headers(error)
    if headers:
        raw = _header_get(headers, "retry-after")
        if raw:
            parsed = _parse_float(raw)
            if parsed is not None:
                return parsed
        raw_ms = _header_get(headers, "retry-after-ms")
        if raw_ms:
            parsed = _parse_float(raw_ms)
            if parsed is not None:
                return parsed / 1000.0

    message = str(error)
    ms_match = _RE_RETRY_MS.search(message)
    if ms_match:
        return float(ms_match.group(1)) / 1000.0
    s_match = _RE_RETRY_S.search(message)
    if s_match:
        return float(s_match.group(1))
    return None


async def sleep_for_retry(
    error: Exception,
    *,
    attempt: int,
    base_delay: float,
    max_delay: float = 30.0,
) -> None:
    retry_after = retry_after_seconds(error)
    jittered = random.uniform(0, min(max_delay, base_delay * (2**attempt)))
    await asyncio.sleep(max(jittered, retry_after or 0.0))


def _headers(error: Exception) -> Any:
    headers = getattr(error, "headers", None)
    if headers is not None:
        return headers
    response = getattr(error, "response", None)
    return getattr(response, "headers", None)


def _header_get(headers: Any, key: str) -> str | None:
    if isinstance(headers, Mapping):
        return headers.get(key) or headers.get(key.title())  # type: ignore[return-value]
    getter = getattr(headers, "get", None)
    if callable(getter):
        value = getter(key)
        return str(value) if value is not None else None
    return None


def _parse_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
