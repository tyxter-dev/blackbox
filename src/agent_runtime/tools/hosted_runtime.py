from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, cast

from agent_runtime.core.artifacts import Artifact
from agent_runtime.core.errors import ConfigurationError, UnsupportedFeatureError
from agent_runtime.core.items import ItemTypes, RunItem
from agent_runtime.core.policy import PolicyDecision, PolicyRequest
from agent_runtime.hosted_tools import (
    ApplyPatch,
    ComputerUse,
    HostedToolHandlers,
    HostedToolSpec,
    Shell,
)
from agent_runtime.tools.hosted import HostedToolCall, HostedToolContext, HostedToolOutput


@dataclass(slots=True)
class HostedToolRunner:
    async def call(
        self,
        call: HostedToolCall,
        *,
        context: HostedToolContext,
        handlers: HostedToolHandlers,
        spec: HostedToolSpec | None,
    ) -> HostedToolOutput:
        if context.policy is not None:
            decision = await context.policy.check(_policy_request(call, spec=spec))
        else:
            decision = PolicyDecision.allow()

        if decision.verdict == "deny":
            return _denied_output(call, reason=decision.reason)
        if decision.verdict == "require_approval":
            return _denied_output(
                call,
                reason=decision.reason or "Approval is required but no approval channel is active.",
            )

        handler = _handler_for(call.hosted_tool_type, handlers)
        output = cast(HostedToolOutput, await handler.handle(call, context))
        if output.provider_input_item is None:
            return _with_provider_input_item(output)
        return output

    def output_item(self, output: HostedToolOutput) -> RunItem:
        return RunItem(
            type=ItemTypes.HOSTED_TOOL_RESULT,
            provider=output.provider,
            status="failed" if output.status in {"failed", "denied"} else "completed",
            data={
                "hosted_tool_type": output.hosted_tool_type,
                "call_id": output.call_id,
                "content": output.content,
                "provider_input_item": output.provider_input_item,
                "artifact_ids": [artifact.id for artifact in output.artifacts],
                **({"payload": output.payload} if output.payload else {}),
            },
            raw=output.raw,
        )


def _handler_for(hosted_tool_type: str, handlers: HostedToolHandlers) -> Any:
    if hosted_tool_type == "shell" and handlers.shell is not None:
        return handlers.shell
    if hosted_tool_type == "apply_patch" and handlers.apply_patch is not None:
        return handlers.apply_patch
    if hosted_tool_type == "computer" and handlers.computer is not None:
        return handlers.computer
    if hosted_tool_type == "text_editor" and handlers.text_editor is not None:
        return handlers.text_editor
    custom = handlers.custom.get(hosted_tool_type)
    if custom is not None:
        return custom
    raise ConfigurationError(
        f"Hosted tool '{hosted_tool_type}' requires an injected handler."
    )


def _policy_request(call: HostedToolCall, *, spec: HostedToolSpec | None) -> PolicyRequest:
    arguments: dict[str, Any] = {
        "provider_item_type": call.provider_item_type,
        "arguments": dict(call.arguments),
    }
    if isinstance(spec, ApplyPatch):
        arguments["workspace_id"] = spec.workspace_id
        arguments["path_preview"] = call.arguments.get("path") or call.arguments.get("paths")
    elif isinstance(spec, Shell):
        arguments["command_preview"] = call.arguments.get("command") or call.arguments.get("cmd")
    elif isinstance(spec, ComputerUse):
        arguments["action_preview"] = call.arguments.get("action") or call.arguments.get("type")
    return PolicyRequest(
        checkpoint="before_hosted_tool_call",
        action=call.hosted_tool_type,
        arguments=arguments,
        metadata={
            "requires_continuation": True,
            "raw_item_id": call.item_id,
            "provider": call.provider,
        },
    )


def _denied_output(call: HostedToolCall, *, reason: str | None) -> HostedToolOutput:
    content = f"Denied by policy: {reason or 'not allowed'}"
    output = HostedToolOutput(
        provider=call.provider,
        hosted_tool_type=call.hosted_tool_type,
        call_id=call.call_id,
        status="denied",
        content=content,
        payload={"error": content},
        metadata={"denied": True, "reason": reason},
    )
    return _with_provider_input_item(output)


def _with_provider_input_item(output: HostedToolOutput) -> HostedToolOutput:
    item = to_provider_continuation_item(output)
    return HostedToolOutput(
        provider=output.provider,
        hosted_tool_type=output.hosted_tool_type,
        call_id=output.call_id,
        status=output.status,
        content=output.content,
        provider_input_item=item,
        artifacts=list(output.artifacts),
        payload=dict(output.payload),
        raw=output.raw,
        metadata=dict(output.metadata),
    )


def to_provider_continuation_item(output: HostedToolOutput) -> dict[str, Any]:
    if output.provider in {"openai", "scripted"}:
        return _to_openai_continuation_item(output)
    if output.provider == "anthropic":
        return _to_anthropic_continuation_item(output)
    if output.provider == "google":
        return _to_gemini_continuation_item(output)
    raise UnsupportedFeatureError(
        f"Hosted tool continuation is not implemented for provider '{output.provider}'."
    )


def _to_openai_continuation_item(output: HostedToolOutput) -> dict[str, Any]:
    if output.hosted_tool_type == "shell":
        shell_output = output.payload.get("output")
        if shell_output is None:
            shell_output = [
                {
                    "stdout": output.content or "",
                    "stderr": output.payload.get("stderr", ""),
                    "outcome": output.payload.get("outcome", {"type": "exit", "exit_code": 0}),
                }
            ]
        return {
            "type": "shell_call_output",
            "call_id": output.call_id,
            "output": shell_output,
        }
    if output.hosted_tool_type == "apply_patch":
        return {
            "type": "apply_patch_call_output",
            "call_id": output.call_id,
            "status": "failed" if output.status in {"failed", "denied"} else "completed",
            "output": output.content or "",
        }
    if output.hosted_tool_type == "computer":
        computer_output = output.payload.get("computer_screenshot")
        if computer_output is None:
            computer_output = output.payload.get("screenshot") or output.content or ""
        return {
            "type": "computer_call_output",
            "call_id": output.call_id,
            "output": computer_output,
        }
    raise UnsupportedFeatureError(
        f"OpenAI continuation is not implemented for hosted tool '{output.hosted_tool_type}'."
    )


def _to_anthropic_continuation_item(output: HostedToolOutput) -> dict[str, Any]:
    content = output.content if isinstance(output.content, str) else json.dumps(output.payload)
    return {
        "type": "tool_result",
        "tool_use_id": output.call_id,
        "content": content,
        "is_error": output.status in {"failed", "denied"},
    }


def _to_gemini_continuation_item(output: HostedToolOutput) -> dict[str, Any]:
    response = output.payload if output.payload else {"result": output.content or ""}
    return {
        "function_response": {
            "id": output.call_id,
            "name": output.hosted_tool_type,
            "response": response,
        }
    }


def artifact_from_payload(
    *,
    type: str,
    name: str,
    data: Any | None = None,
    uri: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> Artifact:
    return Artifact(type=type, name=name, data=data, uri=uri, metadata=metadata or {})
