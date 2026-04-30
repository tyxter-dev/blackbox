"""Live, report-oriented journeys for the AgentProvider framework.

These tests are intentionally not assertion-driven. Agent sessions, provider
events, and model responses are nondeterministic, so each journey prints a
compact JSON report that can be evaluated by an LLM or a human reviewer.
"""
from __future__ import annotations

import asyncio
import json
import os
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

import pytest

from blackbox import (
    AgentCapabilities,
    AgentRef,
    AgentRuntime,
    AgentSession,
    AgentSpec,
    ApprovalDecision,
    EventTypes,
    ModelPricing,
    TaskSpec,
)
from blackbox.core.artifacts import ArtifactPage
from blackbox.core.errors import ProviderNotConfiguredError, UnsupportedFeatureError
from blackbox.core.events import AgentEvent
from blackbox.core.sessions import InvocationRef
from blackbox.providers.agent_adapters.claude_code import ClaudeCodeAgentProvider
from blackbox.providers.agent_adapters.local import LocalAgentProvider
from blackbox.providers.agent_adapters.openai_cloud import OpenAICloudAgentProvider
from blackbox.providers.model_adapters.anthropic_messages import AnthropicMessagesProvider
from blackbox.providers.model_adapters.gemini_generate_content import (
    GeminiGenerateContentProvider,
)
from blackbox.providers.model_adapters.openai_responses import OpenAIResponsesProvider
from blackbox.providers.model_adapters.xai_responses import XAIResponsesProvider
from blackbox.tools import ToolResult

pytestmark = pytest.mark.journey_agent_provider


@dataclass(frozen=True, slots=True)
class LiveModelProvider:
    key: str
    model: str
    register: Callable[[AgentRuntime], None]
    supports_function_tools: bool = True

    @property
    def ref(self) -> str:
        return f"{self.key}:{self.model}"


async def test_journey_local_agent_session_lifecycle() -> None:
    for model_provider in _available_model_providers():
        runtime, local = _runtime_with_local_agent(model_provider)
        prompt = (
            "You are running inside an AgentProvider session. Write a concise support "
            "handoff note with one risk and one next action."
        )
        try:
            agent = await runtime.agents.create_agent(
                provider="local",
                spec=AgentSpec(
                    name="support-agent",
                    instructions="Keep session answers compact and operational.",
                    model=model_provider.ref,
                    metadata={"journey": "local_agent_session_lifecycle"},
                ),
            )
            session = await runtime.agents.create_session(
                provider="local",
                agent=agent,
                task=prompt,
                model=model_provider.ref,
            )
            events = [event async for event in runtime.agents.stream(session)]
            artifacts = await runtime.agents.list_artifacts(session)
            _print_report(
                journey="local_agent_session_lifecycle",
                goal=(
                    "A user creates a local model-backed agent, starts a session, streams "
                    "events, and receives a completed session."
                ),
                agent_provider=local.provider_id,
                model_provider=model_provider,
                agent=agent,
                session=session,
                prompt=prompt,
                events=events,
                artifacts=artifacts,
            )
        finally:
            await runtime.close()


async def test_journey_local_agent_follow_up_message() -> None:
    for model_provider in _available_model_providers():
        runtime, local = _runtime_with_local_agent(model_provider)
        try:
            agent = await runtime.agents.create_agent(
                provider="local",
                spec=AgentSpec(
                    name="research-agent",
                    instructions="Treat follow-up messages as part of the same user session.",
                    model=model_provider.ref,
                ),
            )
            session = await runtime.agents.create_session(
                provider="local",
                agent=agent,
                task="Draft a one sentence project status note.",
                model=model_provider.ref,
            )
            initial_events = [event async for event in runtime.agents.stream(session)]
            invocation = await runtime.agents.send_message(
                session,
                "Add that the owner needs a deploy risk callout.",
            )
            follow_up_events = [event async for event in runtime.agents.stream(session)]
            events = [*initial_events, *follow_up_events]
            _print_report(
                journey="local_agent_follow_up_message",
                goal=(
                    "A user receives an initial agent response, sends a follow-up message "
                    "into the same session, and receives a resumed agent response."
                ),
                agent_provider=local.provider_id,
                model_provider=model_provider,
                agent=agent,
                session=session,
                prompt=session.task,
                events=events,
                invocation=invocation,
                extra={
                    "initial_response_text": _clip(_collected_text(initial_events)),
                    "follow_up_response_text": _clip(_collected_text(follow_up_events)),
                    "initial_event_summary": _event_summary(initial_events),
                    "follow_up_event_summary": _event_summary(follow_up_events),
                    "session_task_unchanged": session.task == "Draft a one sentence project status note.",
                },
            )
        finally:
            await runtime.close()


async def test_journey_local_agent_tool_dispatch() -> None:
    for model_provider in _available_model_providers(function_tools=True):
        runtime, local = _runtime_with_local_agent(model_provider)
        _register_support_tools(runtime)
        prompt = (
            "Use the lookup_customer tool for customer C-104. If the customer risk is high, "
            "use create_ticket with urgent priority. Then summarize the outcome for an "
            "operations dashboard."
        )
        try:
            agent = await runtime.agents.create_agent(
                provider="local",
                spec=AgentSpec(
                    name="support-tool-agent",
                    instructions=(
                        "Use provided local tools when the task asks for customer or ticket "
                        "data. Return a short final operational answer."
                    ),
                    model=model_provider.ref,
                    tools=["lookup_customer", "create_ticket"],
                ),
            )
            session = await runtime.agents.create_session(
                provider="local",
                agent=agent,
                task=prompt,
                model=model_provider.ref,
            )
            events = [event async for event in runtime.agents.stream(session)]
            _print_report(
                journey="local_agent_tool_dispatch",
                goal=(
                    "A user gives a local agent tools and expects the AgentProvider session "
                    "to route tool calls through AgentRuntime."
                ),
                agent_provider=local.provider_id,
                model_provider=model_provider,
                agent=agent,
                session=session,
                prompt=prompt,
                events=events,
                extra={"tool_results": _tool_results(events)},
            )
        finally:
            await runtime.close()


async def test_journey_local_agent_approval_resume() -> None:
    for model_provider in _available_model_providers(function_tools=True):
        runtime, local = _runtime_with_local_agent(
            model_provider,
            approval_policy=lambda name, args: name == "prepare_release",
        )
        _register_release_tool(runtime)
        prompt = (
            "Use prepare_release for service market-data-api and change_id CHG-7. "
            "After approval, summarize the prepared release gate."
        )
        try:
            agent = await runtime.agents.create_agent(
                provider="local",
                spec=AgentSpec(
                    name="release-agent",
                    instructions=(
                        "When asked to prepare a release, call the prepare_release tool. "
                        "Continue after approval with a compact status update."
                    ),
                    model=model_provider.ref,
                    tools=["prepare_release"],
                ),
            )
            session = await runtime.agents.create_session(
                provider="local",
                agent=agent,
                task=prompt,
                model=model_provider.ref,
            )
            events, approval_metadata = await _collect_with_optional_approval(
                runtime,
                local,
                session,
            )
            _print_report(
                journey="local_agent_approval_resume",
                goal=(
                    "A user requires approval before a sensitive local tool call and resumes "
                    "the AgentProvider session after approval."
                ),
                agent_provider=local.provider_id,
                model_provider=model_provider,
                agent=agent,
                session=session,
                prompt=prompt,
                events=events,
                extra=approval_metadata | {"tool_results": _tool_results(events)},
            )
        finally:
            await runtime.close()


async def test_journey_local_agent_cancel_after_tool_output() -> None:
    for model_provider in _available_model_providers(function_tools=True):
        runtime, local = _runtime_with_local_agent(model_provider)
        _register_support_tools(runtime)
        prompt = (
            "Use lookup_customer for customer C-104, then explain what you found. The "
            "application may cancel the session after it observes the tool result."
        )
        try:
            agent = await runtime.agents.create_agent(
                provider="local",
                spec=AgentSpec(
                    name="cancellable-agent",
                    instructions="Use lookup_customer when asked for customer details.",
                    model=model_provider.ref,
                    tools=["lookup_customer"],
                ),
            )
            session = await runtime.agents.create_session(
                provider="local",
                agent=agent,
                task=prompt,
                model=model_provider.ref,
            )
            events: list[AgentEvent] = []
            cancellation_requested = False
            async for event in runtime.agents.stream(session):
                events.append(event)
                if event.type == EventTypes.TOOL_CALL_COMPLETED and not cancellation_requested:
                    cancellation_requested = True
                    await runtime.agents.cancel(session)
            _print_report(
                journey="local_agent_cancel_after_tool_output",
                goal=(
                    "A user cancels an active local AgentProvider session after enough work "
                    "has been observed."
                ),
                agent_provider=local.provider_id,
                model_provider=model_provider,
                agent=agent,
                session=session,
                prompt=prompt,
                events=events,
                extra={
                    "cancellation_requested": cancellation_requested,
                    "tool_results": _tool_results(events),
                },
            )
        finally:
            await runtime.close()


async def test_journey_local_agent_artifact_listing() -> None:
    for model_provider in _available_model_providers():
        runtime, local = _runtime_with_local_agent(model_provider)
        prompt = "Produce a short status note, then the application will list session artifacts."
        try:
            agent = await runtime.agents.create_agent(
                provider="local",
                spec=AgentSpec(name="artifact-agent", model=model_provider.ref),
            )
            session = await runtime.agents.create_session(
                provider="local",
                agent=agent,
                task=prompt,
                model=model_provider.ref,
            )
            events = [event async for event in runtime.agents.stream(session)]
            artifacts = await runtime.agents.list_artifacts(session)
            _print_report(
                journey="local_agent_artifact_listing",
                goal=(
                    "A user lists artifacts through the AgentProvider contract, even when "
                    "the local provider returns an empty artifact page."
                ),
                agent_provider=local.provider_id,
                model_provider=model_provider,
                agent=agent,
                session=session,
                prompt=prompt,
                events=events,
                artifacts=artifacts,
            )
        finally:
            await runtime.close()


async def test_journey_agent_provider_capability_report() -> None:
    runtime = AgentRuntime()
    local = LocalAgentProvider(runtime.models)
    providers: dict[str, AgentCapabilities] = {"local": local.capabilities()}
    providers["openai-agent"] = OpenAICloudAgentProvider().capabilities()
    providers["claude-code"] = ClaudeCodeAgentProvider().capabilities()
    _print_report(
        journey="agent_provider_capability_report",
        goal="A user inspects capability flags before choosing an AgentProvider.",
        agent_provider="mixed",
        prompt="Capability discovery only.",
        events=[],
        extra={
            "capabilities": {
                provider: _capability_summary(caps)
                for provider, caps in providers.items()
            }
        },
    )


async def test_journey_openai_agents_sdk_session_supervision() -> None:
    if os.environ.get("RUN_OPENAI_AGENT_PROVIDER_JOURNEY") != "1":
        pytest.skip("Set RUN_OPENAI_AGENT_PROVIDER_JOURNEY=1 to run this journey.")
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        pytest.skip("OPENAI_API_KEY is required for this journey.")

    runtime = AgentRuntime()
    provider = OpenAICloudAgentProvider(api_key=api_key)
    runtime.registry.register_agent(provider)
    prompt = "Return a two sentence provider-managed agent status note."
    try:
        agent = await runtime.agents.create_agent(
            provider="openai-agent",
            spec=AgentSpec(
                name="openai-managed-agent",
                instructions="Do not use external tools. Return concise text.",
                model=os.environ.get("OPENAI_AGENT_PROVIDER_MODEL", "gpt-4o-mini"),
            ),
        )
        session = await runtime.agents.create_session(
            provider="openai-agent",
            agent=agent,
            task=TaskSpec(prompt=prompt),
        )
        events = [event async for event in runtime.agents.stream(session)]
        artifacts = await runtime.agents.list_artifacts(session)
    except (ProviderNotConfiguredError, UnsupportedFeatureError) as exc:
        pytest.skip(str(exc))
    finally:
        await runtime.close()

    _print_report(
        journey="openai_agents_sdk_session_supervision",
        goal=(
            "A user supervises an OpenAI Agents SDK-backed session through the "
            "AgentProvider contract and lists artifacts."
        ),
        agent_provider=provider.provider_id,
        agent=agent,
        session=session,
        prompt=prompt,
        events=events,
        artifacts=artifacts,
    )


async def test_journey_claude_code_session_supervision() -> None:
    if os.environ.get("RUN_CLAUDE_CODE_AGENT_PROVIDER_JOURNEY") != "1":
        pytest.skip("Set RUN_CLAUDE_CODE_AGENT_PROVIDER_JOURNEY=1 to run this journey.")
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        pytest.skip("ANTHROPIC_API_KEY is required for this journey.")

    runtime = AgentRuntime()
    provider = ClaudeCodeAgentProvider(api_key=api_key)
    runtime.registry.register_agent(provider)
    prompt = "Return a two sentence provider-managed coding-agent status note. Do not edit files."
    try:
        agent = await runtime.agents.create_agent(
            provider="claude-code",
            spec=AgentSpec(
                name="claude-code-journey-agent",
                instructions="Do not edit files or run shell commands. Return concise text.",
                model=os.environ.get("CLAUDE_CODE_AGENT_PROVIDER_MODEL"),
                permissions={"permission_mode": "plan"},
            ),
        )
        session = await runtime.agents.create_session(
            provider="claude-code",
            agent=agent,
            task=TaskSpec(prompt=prompt),
        )
        events = [event async for event in runtime.agents.stream(session)]
        artifacts = await runtime.agents.list_artifacts(session)
    except (ProviderNotConfiguredError, UnsupportedFeatureError) as exc:
        pytest.skip(str(exc))
    finally:
        await runtime.close()

    _print_report(
        journey="claude_code_session_supervision",
        goal=(
            "A user supervises a Claude Code / Agent SDK-backed session through the "
            "AgentProvider contract and lists artifacts."
        ),
        agent_provider=provider.provider_id,
        agent=agent,
        session=session,
        prompt=prompt,
        events=events,
        artifacts=artifacts,
    )


def _available_model_providers(
    *, function_tools: bool | None = None
) -> list[LiveModelProvider]:
    providers = _all_configured_model_providers()
    requested = _requested_model_provider_keys()
    if requested is not None:
        providers = [provider for provider in providers if provider.key in requested]
    if function_tools is not None:
        providers = [
            provider
            for provider in providers
            if provider.supports_function_tools is function_tools
        ]
    if not providers:
        pytest.skip("No configured live model providers match this AgentProvider journey.")
    return providers


def _all_configured_model_providers() -> list[LiveModelProvider]:
    providers: list[LiveModelProvider] = []
    if os.environ.get("OPENAI_API_KEY"):
        providers.append(
            LiveModelProvider(
                key="openai",
                model=os.environ.get(
                    "OPENAI_AGENT_JOURNEY_MODEL",
                    os.environ.get("OPENAI_JOURNEY_MODEL", "gpt-4o-mini"),
                ),
                register=lambda runtime: runtime.registry.register_model(
                    OpenAIResponsesProvider(api_key=os.environ["OPENAI_API_KEY"])
                ),
            )
        )
    if os.environ.get("ANTHROPIC_API_KEY"):
        providers.append(
            LiveModelProvider(
                key="anthropic",
                model=os.environ.get("ANTHROPIC_AGENT_JOURNEY_MODEL", "claude-haiku-4-5-20251001"),
                register=lambda runtime: runtime.registry.register_model(
                    AnthropicMessagesProvider(api_key=os.environ["ANTHROPIC_API_KEY"])
                ),
            )
        )
    google_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
    if google_key:
        providers.append(
            LiveModelProvider(
                key="google",
                model=os.environ.get("GEMINI_AGENT_JOURNEY_MODEL", "gemini-2.5-flash"),
                register=lambda runtime: runtime.registry.register_model(
                    GeminiGenerateContentProvider(api_key=google_key)
                ),
            )
        )
    if os.environ.get("XAI_API_KEY"):
        providers.append(
            LiveModelProvider(
                key="xai",
                model=os.environ.get("XAI_AGENT_JOURNEY_MODEL", os.environ.get("XAI_MODEL", "grok-4")),
                register=lambda runtime: runtime.registry.register_model(
                    XAIResponsesProvider(api_key=os.environ["XAI_API_KEY"])
                ),
            )
        )
    return providers


def _requested_model_provider_keys() -> set[str] | None:
    raw = os.environ.get("AGENT_PROVIDER_JOURNEY_MODEL_PROVIDERS")
    if not raw:
        raw = os.environ.get("MODEL_PROVIDER_JOURNEY_PROVIDERS")
    if not raw:
        return None
    return {value.strip() for value in raw.split(",") if value.strip()}


def _runtime_with_local_agent(
    model_provider: LiveModelProvider,
    *,
    approval_policy: Callable[[str, dict[str, Any]], bool] | None = None,
    max_iterations: int = 8,
) -> tuple[AgentRuntime, LocalAgentProvider]:
    runtime = AgentRuntime()
    model_provider.register(runtime)
    runtime.model_catalog.register_pricing(
        ModelPricing(
            provider=model_provider.key,
            model=model_provider.model,
            input_per_million=1.0,
            output_per_million=2.0,
            cache_read_input_per_million=0.25,
            cache_creation_input_per_million=1.25,
        )
    )
    local = LocalAgentProvider(
        runtime.models,
        tools=runtime.tools.default_runtime,
        approval_policy=approval_policy,
        max_iterations=max_iterations,
    )
    runtime.registry.register_agent(local)
    return runtime, local


def _register_support_tools(runtime: AgentRuntime) -> None:
    def lookup_customer(customer_id: str) -> ToolResult:
        return ToolResult(
            content=(
                f"Customer {customer_id}: enterprise plan, risk high, three open "
                "incidents, latest issue is checkout latency on market-data dashboard."
            ),
            payload={
                "customer_id": customer_id,
                "plan": "enterprise",
                "risk": "high",
                "open_incidents": 3,
            },
        )

    def create_ticket(customer_id: str, priority: str, summary: str) -> ToolResult:
        return ToolResult(
            content=f"Created ticket TCK-agent-001 for {customer_id} with {priority} priority.",
            payload={
                "ticket_id": "TCK-agent-001",
                "customer_id": customer_id,
                "priority": priority,
                "summary": summary,
            },
        )

    runtime.tools.register(
        lookup_customer,
        name="lookup_customer",
        description="Look up account health and recent incidents for a customer.",
        parameters={
            "type": "object",
            "properties": {"customer_id": {"type": "string"}},
            "required": ["customer_id"],
        },
    )
    runtime.tools.register(
        create_ticket,
        name="create_ticket",
        description="Create an operations escalation ticket.",
        parameters={
            "type": "object",
            "properties": {
                "customer_id": {"type": "string"},
                "priority": {"type": "string"},
                "summary": {"type": "string"},
            },
            "required": ["customer_id", "priority", "summary"],
        },
    )


def _register_release_tool(runtime: AgentRuntime) -> None:
    def prepare_release(service: str, change_id: str) -> ToolResult:
        return ToolResult(
            content=f"Prepared release gate for {service} under {change_id}.",
            payload={
                "service": service,
                "change_id": change_id,
                "gate": "prepared",
                "requires_human_approval": True,
            },
        )

    runtime.tools.register(
        prepare_release,
        name="prepare_release",
        description="Prepare a release gate for a service change.",
        parameters={
            "type": "object",
            "properties": {
                "service": {"type": "string"},
                "change_id": {"type": "string"},
            },
            "required": ["service", "change_id"],
        },
    )


async def _collect_with_optional_approval(
    runtime: AgentRuntime,
    local: LocalAgentProvider,
    session: AgentSession,
) -> tuple[list[AgentEvent], dict[str, Any]]:
    events: list[AgentEvent] = []

    async def collect() -> None:
        async for event in runtime.agents.stream(session):
            events.append(event)

    task = asyncio.create_task(collect())
    approval_id: str | None = None
    for _ in range(300):
        if local._approvals:
            approval_id = next(iter(local._approvals))
            await runtime.agents.approve(
                provider=local.provider_id,
                approval_id=approval_id,
                decision=ApprovalDecision.approve("Approved by AgentProvider journey."),
            )
            break
        if task.done():
            break
        await asyncio.sleep(0.1)

    timed_out = False
    try:
        await asyncio.wait_for(task, timeout=120)
    except TimeoutError:
        timed_out = True
        await runtime.agents.cancel(session)
        await asyncio.wait_for(task, timeout=15)

    return events, {
        "approval_id": approval_id,
        "approval_observed": approval_id is not None,
        "collector_timed_out": timed_out,
    }


def _print_report(
    *,
    journey: str,
    goal: str,
    agent_provider: str,
    prompt: str,
    events: Sequence[AgentEvent],
    model_provider: LiveModelProvider | None = None,
    agent: AgentRef | None = None,
    session: AgentSession | None = None,
    invocation: InvocationRef | None = None,
    artifacts: ArtifactPage | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    report = {
        "journey": journey,
        "goal": goal,
        "agent_provider": agent_provider,
        "model_provider": (
            {"provider": model_provider.key, "model": model_provider.model}
            if model_provider is not None
            else None
        ),
        "prompt": prompt,
        "response_text": _clip(_collected_text(events)),
        "agent": _agent_summary(agent),
        "session": _session_summary(session),
        "invocation": _invocation_summary(invocation),
        "event_summary": _event_summary(events),
        "artifacts": _artifact_page_summary(artifacts),
        "extra": _jsonable(extra or {}),
    }
    print("\n=== AGENT_PROVIDER_JOURNEY_REPORT ===")
    print(json.dumps(report, indent=2, sort_keys=True, default=str))
    print("=== END_AGENT_PROVIDER_JOURNEY_REPORT ===\n")


def _event_summary(events: Sequence[AgentEvent]) -> dict[str, Any]:
    counts = Counter(event.type for event in events)
    return {
        "total_events": len(events),
        "counts": dict(sorted(counts.items())),
        "samples": [_event_sample(event) for event in events[:14]],
    }


def _event_sample(event: AgentEvent) -> dict[str, Any]:
    item = event.data.get("item")
    return {
        "type": event.type,
        "provider": event.provider,
        "session_id": event.session_id,
        "item_id": event.item_id,
        "sequence": event.sequence,
        "data_keys": sorted(event.data),
        "tool_name": event.data.get("name") or event.data.get("tool"),
        "approval_id": event.data.get("approval_id"),
        "run_item_type": getattr(item, "type", None),
    }


def _collected_text(events: Sequence[AgentEvent]) -> str:
    chunks: list[str] = []
    for event in events:
        if event.type == EventTypes.MODEL_TEXT_DELTA:
            value = event.data.get("delta") or event.data.get("message")
            if value is not None:
                chunks.append(str(value))
        elif event.type in {EventTypes.CLOUD_AGENT_LOG, EventTypes.SESSION_COMPLETED}:
            value = event.data.get("message")
            if value is not None:
                chunks.append(str(value))
    return "".join(chunks)


def _agent_summary(agent: AgentRef | None) -> dict[str, Any] | None:
    if agent is None:
        return None
    return {
        "provider": agent.provider,
        "id": agent.id,
        "metadata_keys": sorted(agent.metadata),
    }


def _session_summary(session: AgentSession | None) -> dict[str, Any] | None:
    if session is None:
        return None
    return {
        "provider": session.provider,
        "id": session.id,
        "agent_id": session.agent_id,
        "model": session.model,
        "status": session.status,
        "metadata_keys": sorted(session.metadata),
    }


def _invocation_summary(invocation: InvocationRef | None) -> dict[str, Any] | None:
    if invocation is None:
        return None
    return {
        "provider": invocation.provider,
        "session_id": invocation.session_id,
        "id": invocation.id,
        "metadata_keys": sorted(invocation.metadata),
    }


def _artifact_page_summary(page: ArtifactPage | None) -> dict[str, Any] | None:
    if page is None:
        return None
    return {
        "count": len(page.items),
        "has_more": page.has_more,
        "next_cursor": page.next_cursor,
        "items": [
            {
                "id": artifact.id,
                "type": artifact.type,
                "name": artifact.name,
                "uri": artifact.uri,
                "data_present": artifact.data is not None,
                "metadata_keys": sorted(artifact.metadata),
            }
            for artifact in page.items
        ],
    }


def _tool_results(events: Sequence[AgentEvent]) -> list[dict[str, Any]]:
    return [
        {
            "call_id": event.data.get("call_id"),
            "name": event.data.get("name"),
            "content": _clip(str(event.data.get("content") or ""), limit=800),
            "payload": _jsonable(event.data.get("payload")),
        }
        for event in events
        if event.type == EventTypes.TOOL_CALL_COMPLETED
    ]


def _capability_summary(caps: AgentCapabilities) -> dict[str, bool]:
    return {
        "sessions": caps.supports_sessions,
        "streaming_events": caps.supports_streaming_events,
        "artifacts": caps.supports_artifacts,
        "workspace": caps.supports_workspace,
        "approvals": caps.supports_approvals,
        "mcp": caps.supports_mcp,
        "deployments": caps.supports_deployments,
        "evals": caps.supports_evals,
        "cancellation": caps.supports_cancellation,
        "resume": caps.supports_resume,
    }


def _jsonable(value: Any) -> Any:
    try:
        json.dumps(value, default=str)
    except TypeError:
        return str(value)
    return value


def _clip(text: str | None, limit: int = 4000) -> str:
    if text is None:
        return ""
    if len(text) <= limit:
        return text
    return text[:limit] + "\n...[truncated]"
