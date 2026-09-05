"""Verified adaptive controls and prefix-bound replay for current Claude models."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from blackbox.core.errors import UnsupportedFeatureError
from blackbox.core.state import ProviderState
from blackbox.providers.base import TurnRequest

ADAPTIVE_MODELS = frozenset(
    {"claude-fable-5-1", "claude-fable-5", "claude-opus-5", "claude-sonnet-5", "claude-opus-4-8"}
)
EFFORTS = ("low", "medium", "high", "xhigh", "max")
_REPLAY_KEY = "fable_5_1_prefix"


def _effective(kwargs: dict[str, Any]) -> dict[str, Any]:
    return {**kwargs, **(kwargs.get("extra_body") or {})}


def apply_current_controls(request: TurnRequest, kwargs: dict[str, Any]) -> None:
    """Validate final native overrides and map typed adaptive effort without losing format."""
    effective = _effective(kwargs)
    model = effective.get("model")
    if model in ADAPTIVE_MODELS:
        output = effective.get("output_config", {})
        if not isinstance(output, dict):
            raise UnsupportedFeatureError("output_config must be a mapping.")
        if request.output_schema is not None and request.output_strategy == "provider_native":
            response_format = {"type": "json_schema", "schema": request.output_schema.schema}
            if output.get("format", response_format) != response_format:
                raise UnsupportedFeatureError(
                    "Native output format conflicts with the declared schema."
                )
            output = {**output, "format": response_format}
            kwargs["output_config"] = output
            if "output_config" in (kwargs.get("extra_body") or {}):
                kwargs["extra_body"] = {**kwargs["extra_body"], "output_config": output}
        effort = request.controls.reasoning_effort
        if effort is not None:
            if output.get("effort", effort) != effort:
                raise UnsupportedFeatureError("Native and typed reasoning efforts conflict.")
            output = {**output, "effort": effort}
            kwargs["output_config"] = output
            if "output_config" in (kwargs.get("extra_body") or {}):
                kwargs["extra_body"] = {**kwargs["extra_body"], "output_config": output}
        effort = output.get("effort", "high")
        if effort not in EFFORTS:
            raise UnsupportedFeatureError(f"Unsupported adaptive reasoning effort {effort!r}.")
        for key in ("temperature", "top_p", "top_k"):
            if key in effective and (key == "top_k" or effective[key] != 1):
                raise UnsupportedFeatureError(f"{model} does not support nondefault {key}.")
        thinking = effective.get("thinking")
        if thinking is not None:
            if (
                not isinstance(thinking, dict)
                or thinking.get("type") not in {"adaptive", "disabled"}
                or "budget_tokens" in thinking
            ):
                raise UnsupportedFeatureError(
                    "This model requires adaptive thinking, without a token budget."
                )
            if thinking["type"] == "disabled":
                if model in {"claude-fable-5-1", "claude-fable-5"} or (
                    model == "claude-opus-5" and effort in {"xhigh", "max"}
                ):
                    raise UnsupportedFeatureError(
                        "Thinking cannot be disabled for this model and effort."
                    )
        elif request.controls.reasoning_effort is not None:
            kwargs["thinking"] = {"type": "adaptive"}
        choice = effective.get("tool_choice")
        choice_type = choice.get("type") if isinstance(choice, dict) else choice
        if model == "claude-fable-5-1" and (
            choice_type not in {None, "auto", "none"} or request.output_strategy == "finalizer_tool"
        ):
            raise UnsupportedFeatureError(
                "Fable 5.1 supports only auto/none tool choice; finalizer_tool is unavailable."
            )
        if model == "claude-opus-5" and any(
            isinstance(tool, dict) and str(tool.get("type", "")).startswith("web_fetch")
            for tool in effective.get("tools", [])
        ):
            raise UnsupportedFeatureError("Claude Opus 5 does not support WebFetch.")
    _validate_replay(request.provider_state, _effective(kwargs))


def _fingerprint(value: Any) -> str:
    try:
        serialized = json.dumps(value, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise UnsupportedFeatureError("Fable 5.1 replay requires JSON-native prefix data.") from exc
    return hashlib.sha256(serialized.encode()).hexdigest()


def _prefix(kwargs: dict[str, Any], messages: Any) -> list[Any]:
    return [kwargs.get("system"), kwargs.get("tools"), messages]


def _validate_replay(state: ProviderState | None, kwargs: dict[str, Any]) -> None:
    if state is None:
        return
    guard = state.tool_state.get(_REPLAY_KEY)
    if guard is not None:
        if not isinstance(guard, dict) or kwargs.get("model") != "claude-fable-5-1":
            raise UnsupportedFeatureError(
                "Fable 5.1 thinking state cannot be replayed to another model."
            )
        count = guard.get("message_count")
        messages = kwargs.get("messages")
        if (
            not isinstance(count, int)
            or not isinstance(messages, list)
            or len(messages) < count
            or _fingerprint(_prefix(kwargs, messages[:count])) != guard.get("digest")
        ):
            raise UnsupportedFeatureError(
                "Fable 5.1 replay requires unchanged system, tools, and prior messages."
            )
    elif kwargs.get("model") == "claude-fable-5-1" and any(
        isinstance(message, dict)
        and isinstance(message.get("content"), list)
        and any(
            isinstance(block, dict) and block.get("type") in {"thinking", "redacted_thinking"}
            for block in message["content"]
        )
        for message in state.native_history
    ):
        raise UnsupportedFeatureError(
            "Imported thinking history lacks Fable 5.1 prefix provenance."
        )


def record_replay_prefix(state: ProviderState, kwargs: dict[str, Any]) -> None:
    """Retain provenance alongside untouched native history for subsequent replay."""
    effective = _effective(kwargs)
    if effective.get("model") == "claude-fable-5-1":
        state.tool_state[_REPLAY_KEY] = {
            "model": "claude-fable-5-1",
            "message_count": len(state.native_history),
            "digest": _fingerprint(_prefix(effective, state.native_history)),
        }
