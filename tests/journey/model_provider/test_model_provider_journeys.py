"""Live, report-oriented journeys for the ModelProvider framework.

These tests are intentionally not assertion-driven. The model responses and
hosted tool behavior are nondeterministic, so each journey prints a compact
JSON report that can be evaluated by an LLM or a human reviewer.
"""
from __future__ import annotations

import json
import os
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

import pytest
from pydantic import BaseModel, Field

from agent_runtime import (
    AgentRuntime,
    ChatMessage,
    CodeInterpreter,
    CompactionControl,
    FileSearch,
    ImageGeneration,
    ModelCacheControl,
    ModelPricing,
    RemoteMCP,
    ToolNamespace,
    ToolSearch,
    ToolSearchControl,
    WebSearch,
)
from agent_runtime.core.errors import OutputValidationError
from agent_runtime.core.events import AgentEvent, EventTypes
from agent_runtime.core.results import AgentResult, OutputSpec
from agent_runtime.core.state import ProviderState
from agent_runtime.providers.model_adapters.anthropic_messages import AnthropicMessagesProvider
from agent_runtime.providers.model_adapters.gemini_generate_content import (
    GeminiGenerateContentProvider,
)
from agent_runtime.providers.model_adapters.openai_responses import OpenAIResponsesProvider
from agent_runtime.providers.model_adapters.xai_responses import XAIResponsesProvider
from agent_runtime.tools import ToolResult

pytestmark = pytest.mark.journey_model_provider


class IncidentBrief(BaseModel):
    severity: str = Field(description="One of low, medium, or high.")
    summary: str
    next_action: str


class MarketNewsBrief(BaseModel):
    topic: str
    summary: str
    risks: list[str]


@dataclass(frozen=True, slots=True)
class LiveProvider:
    key: str
    model: str
    register: Callable[[AgentRuntime], None]
    supports_provider_native_output: bool = False
    supports_web_search: bool = False
    supports_remote_mcp: bool = False
    supports_openai_style_chat_input: bool = False
    supports_compaction: bool = False
    supports_cache_key: bool = False

    @property
    def ref(self) -> str:
        return f"{self.key}:{self.model}"


def _available_providers(
    *,
    provider_native_output: bool | None = None,
    web_search: bool | None = None,
    remote_mcp: bool | None = None,
    openai_style_chat_input: bool | None = None,
) -> list[LiveProvider]:
    providers = _all_configured_providers()
    requested = _requested_provider_keys()
    if requested is not None:
        providers = [provider for provider in providers if provider.key in requested]
    if provider_native_output is not None:
        providers = [
            provider
            for provider in providers
            if provider.supports_provider_native_output is provider_native_output
        ]
    if web_search is not None:
        providers = [provider for provider in providers if provider.supports_web_search is web_search]
    if remote_mcp is not None:
        providers = [provider for provider in providers if provider.supports_remote_mcp is remote_mcp]
    if openai_style_chat_input is not None:
        providers = [
            provider
            for provider in providers
            if provider.supports_openai_style_chat_input is openai_style_chat_input
        ]
    if not providers:
        pytest.skip("No configured live providers match this journey.")
    return providers


def _all_configured_providers() -> list[LiveProvider]:
    providers: list[LiveProvider] = []
    if os.environ.get("OPENAI_API_KEY"):
        providers.append(
            LiveProvider(
                key="openai",
                model=os.environ.get(
                    "OPENAI_JOURNEY_MODEL",
                    os.environ.get("OPENAI_TEST_MODEL", "gpt-4o-mini"),
                ),
                register=lambda runtime: runtime.registry.register_model(
                    OpenAIResponsesProvider(api_key=os.environ["OPENAI_API_KEY"])
                ),
                supports_provider_native_output=True,
                supports_web_search=True,
                supports_remote_mcp=True,
                supports_openai_style_chat_input=True,
                supports_compaction=True,
                supports_cache_key=True,
            )
        )
    if os.environ.get("ANTHROPIC_API_KEY"):
        providers.append(
            LiveProvider(
                key="anthropic",
                model=os.environ.get("ANTHROPIC_JOURNEY_MODEL", "claude-haiku-4-5-20251001"),
                register=lambda runtime: runtime.registry.register_model(
                    AnthropicMessagesProvider(api_key=os.environ["ANTHROPIC_API_KEY"])
                ),
                supports_remote_mcp=True,
            )
        )
    google_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
    if google_key:
        providers.append(
            LiveProvider(
                key="google",
                model=os.environ.get(
                    "GEMINI_JOURNEY_MODEL",
                    os.environ.get("GEMINI_MODEL", "gemini-2.5-flash"),
                ),
                register=lambda runtime: runtime.registry.register_model(
                    GeminiGenerateContentProvider(api_key=google_key)
                ),
                supports_provider_native_output=True,
                supports_web_search=True,
            )
        )
    if os.environ.get("XAI_API_KEY"):
        providers.append(
            LiveProvider(
                key="xai",
                model=os.environ.get("XAI_JOURNEY_MODEL", os.environ.get("XAI_MODEL", "grok-4")),
                register=lambda runtime: runtime.registry.register_model(
                    XAIResponsesProvider(api_key=os.environ["XAI_API_KEY"])
                ),
                supports_openai_style_chat_input=True,
            )
        )
    return providers


def _requested_provider_keys() -> set[str] | None:
    raw = os.environ.get("MODEL_PROVIDER_JOURNEY_PROVIDERS")
    if not raw:
        return None
    return {value.strip() for value in raw.split(",") if value.strip()}


def _runtime_for(provider: LiveProvider) -> AgentRuntime:
    runtime = AgentRuntime()
    provider.register(runtime)
    runtime.model_catalog.register_pricing(
        ModelPricing(
            provider=provider.key,
            model=provider.model,
            input_per_million=1.0,
            output_per_million=2.0,
            cache_read_input_per_million=0.25,
            cache_creation_input_per_million=1.25,
        )
    )
    return runtime


async def test_journey_direct_model_turn_for_available_providers() -> None:
    for provider in _available_providers():
        runtime = _runtime_for(provider)
        prompt = (
            "You power a developer status app. Summarize this update in two short bullets: "
            "API latency improved, retry logs are cleaner, and hosted tool metadata is now "
            "visible in run reports."
        )
        try:
            result = await runtime.models.run(
                provider=provider.key,
                model=provider.model,
                input=prompt,
                max_output_tokens=_output_budget(provider, 320),
            )
            _print_turn_report(
                journey="direct_model_turn",
                goal="A user makes one direct model call and receives text plus provider state.",
                provider=provider,
                prompt=prompt,
                result=result,
            )
        finally:
            await runtime.close()


async def test_journey_streaming_response_for_live_ui() -> None:
    for provider in _available_providers():
        runtime = _runtime_for(provider)
        prompt = (
            "You are streaming into a live UI. Write a concise release note for a library "
            "that added provider-native model events."
        )
        events: list[AgentEvent] = []
        text_parts: list[str] = []
        try:
            async for event in runtime.models.stream(
                provider=provider.key,
                model=provider.model,
                input=prompt,
                max_output_tokens=_output_budget(provider, 420),
            ):
                events.append(event)
                if event.type == EventTypes.MODEL_TEXT_DELTA:
                    delta = event.data.get("delta")
                    if isinstance(delta, str):
                        text_parts.append(delta)
            _print_report(
                journey="streaming_response_for_live_ui",
                goal="A user streams model deltas and can render partial output as it arrives.",
                provider=provider,
                prompt=prompt,
                response_text="".join(text_parts),
                events=events,
                extra={"streamed_delta_count": len(text_parts)},
            )
        finally:
            await runtime.close()


async def test_journey_provider_state_continuation_for_follow_up_context() -> None:
    for provider in _available_providers():
        runtime = _runtime_for(provider)
        first_prompt = (
            "Remember that the project codename is blue-orchid. Reply with one sentence "
            "that says the codename was stored."
        )
        second_prompt = (
            "Using only the prior provider-native context, what project codename did I give you? "
            "Answer in one sentence."
        )
        try:
            first = await runtime.models.run(
                provider=provider.key,
                model=provider.model,
                input=first_prompt,
                max_output_tokens=_output_budget(provider, 420),
            )
            second = await runtime.models.run(
                provider=provider.key,
                model=provider.model,
                input=second_prompt,
                provider_state=first.provider_state,
                max_output_tokens=_output_budget(provider, 420),
            )
            _print_turn_report(
                journey="provider_state_continuation",
                goal="A user saves provider-native state and continues the same task later.",
                provider=provider,
                prompt=f"{first_prompt}\n\nFOLLOW-UP: {second_prompt}",
                result=second,
                extra={
                    "first_response": _clip(first.text),
                    "first_provider_state": _provider_state_summary(first.provider_state),
                },
            )
        finally:
            await runtime.close()


async def test_journey_chat_compatibility_for_existing_chat_app() -> None:
    for provider in _available_providers(openai_style_chat_input=True):
        runtime = _runtime_for(provider)
        messages = [
            ChatMessage(
                role="user",
                content=(
                    "You are being called from an existing chat-shaped app. Explain in one "
                    "paragraph why provider-native state still matters."
                ),
            )
        ]
        try:
            result = await runtime.chat.run(
                provider=provider.key,
                model=provider.model,
                messages=messages,
                max_output_tokens=_output_budget(provider, 420),
            )
            _print_turn_report(
                journey="chat_compatibility_projection",
                goal=(
                    "A user with chat-shaped messages can call the runtime without replacing "
                    "the internal provider-native event/state model."
                ),
                provider=provider,
                prompt=json.dumps([message.to_dict() for message in messages], default=str),
                result=result,
            )
        finally:
            await runtime.close()


async def test_journey_high_level_runtime_dispatches_local_tools() -> None:
    provider = _available_providers()[0]
    runtime = _runtime_for(provider)

    def lookup_customer(customer_id: str) -> ToolResult:
        return ToolResult(
            content=json.dumps(
                {
                    "customer_id": customer_id,
                    "plan": "enterprise",
                    "open_incidents": 3,
                    "latest_issue": "checkout latency for market-data dashboard",
                }
            ),
            payload={"customer_id": customer_id, "risk": "high", "owner": "success-team"},
        )

    def create_ticket(customer_id: str, priority: str, summary: str) -> ToolResult:
        return ToolResult(
            content=json.dumps(
                {
                    "ticket_id": "TCK-journey-001",
                    "customer_id": customer_id,
                    "priority": priority,
                    "summary": summary,
                }
            ),
            payload={
                "ticket_id": "TCK-journey-001",
                "customer_id": customer_id,
                "priority": priority,
                "summary": summary,
            },
        )

    runtime.tools.register(
        lookup_customer,
        name="lookup_customer",
        description="Look up customer health and recent issue context.",
        parameters={
            "type": "object",
            "properties": {"customer_id": {"type": "string"}},
            "required": ["customer_id"],
            "additionalProperties": False,
        },
    )
    runtime.tools.register(
        create_ticket,
        name="create_ticket",
        description="Create a support escalation ticket.",
        parameters={
            "type": "object",
            "properties": {
                "customer_id": {"type": "string"},
                "priority": {"type": "string"},
                "summary": {"type": "string"},
            },
            "required": ["customer_id", "priority", "summary"],
            "additionalProperties": False,
        },
    )

    prompt = (
        "You are powering a customer support app. Use lookup_customer for customer C-104, "
        "then create_ticket if the account needs escalation. Finish with a concise status."
    )
    try:
        result = await runtime.run(
            provider=provider.ref,
            input=prompt,
            tools=["lookup_customer", "create_ticket"],
            tool_max_concurrent=2,
            max_output_tokens=_output_budget(provider, 700),
        )
        _print_agent_report(
            journey="high_level_runtime_local_tool_loop",
            goal=(
                "A user exposes local Python tools and lets AgentRuntime dispatch calls, "
                "feed results back, and return the final model answer."
            ),
            provider=provider,
            prompt=prompt,
            result=result,
        )
    finally:
        await runtime.close()


async def test_journey_dynamic_tool_session_for_one_off_app_tool() -> None:
    provider = _available_providers()[0]
    runtime = _runtime_for(provider)
    tool_session = runtime.tools.session()

    def lookup_release(version: str) -> ToolResult:
        return ToolResult(
            content=json.dumps(
                {
                    "version": version,
                    "status": "ready",
                    "notable_change": "ModelProvider journeys now produce reviewable reports.",
                }
            ),
            payload={"version": version, "ready": True},
        )

    tool_session.register(
        lookup_release,
        name="lookup_release",
        description="Look up release readiness for one run only.",
        parameters={
            "type": "object",
            "properties": {"version": {"type": "string"}},
            "required": ["version"],
            "additionalProperties": False,
        },
    )

    prompt = (
        "You are powering a release dashboard. Call lookup_release for version 0.1.0 "
        "and summarize whether the release can proceed."
    )
    try:
        result = await runtime.run(
            provider=provider.ref,
            input=prompt,
            tools=["lookup_release"],
            tool_session=tool_session,
            max_output_tokens=_output_budget(provider, 600),
        )
        _print_agent_report(
            journey="dynamic_tool_session",
            goal=(
                "A user creates an isolated one-off tool session without mutating the global "
                "tool registry."
            ),
            provider=provider,
            prompt=prompt,
            result=result,
            extra={"global_tool_names_after_run": [tool.name for tool in runtime.tools.all_tools()]},
        )
    finally:
        await runtime.close()


async def test_journey_provider_native_structured_output_for_supported_providers() -> None:
    for provider in _available_providers(provider_native_output=True):
        runtime = _runtime_for(provider)
        prompt = (
            "Classify this incident for an operations app: a paid customer cannot access "
            "billing exports for two hours, but no data is lost. Return the structured object."
        )
        try:
            result: AgentResult[IncidentBrief] = await runtime.run(
                provider=provider.ref,
                input=prompt,
                output_spec=OutputSpec(
                    schema=IncidentBrief,
                    strategy="provider_native",
                    name="incident_brief",
                ),
                max_output_tokens=_output_budget(provider, 520),
            )
            _print_agent_report(
                journey="provider_native_structured_output",
                goal=(
                    "A user asks a provider that supports native schemas to return typed data "
                    "through the runtime."
                ),
                provider=provider,
                prompt=prompt,
                result=result,
                extra={"validated_output": _model_output(result.output)},
            )
        finally:
            await runtime.close()


async def test_journey_posthoc_structured_output_with_retry() -> None:
    for provider in _available_providers():
        runtime = _runtime_for(provider)
        prompt = (
            "Return only JSON for this product planning note. Topic: ModelProvider journeys. "
            "Summary: live calls produce reports for LLM review. Risks: API quota and "
            "provider-specific hosted tool availability."
        )
        try:
            try:
                result: AgentResult[MarketNewsBrief] = await runtime.run(
                    provider=provider.ref,
                    input=prompt,
                    output_spec=OutputSpec(
                        schema=MarketNewsBrief,
                        strategy="posthoc_parse_with_retry",
                        max_validation_retries=2,
                    ),
                    max_output_tokens=_output_budget(provider, 1400),
                )
            except OutputValidationError as exc:
                _print_report(
                    journey="posthoc_structured_output_with_retry",
                    goal=(
                        "A user asks the runtime to parse model text into a schema and repair "
                        "invalid JSON when needed."
                    ),
                    provider=provider,
                    prompt=prompt,
                    response_text=exc.raw_text or "",
                    events=[],
                    extra={"status": "validation_failed", "error": str(exc)},
                )
                continue
            _print_agent_report(
                journey="posthoc_structured_output_with_retry",
                goal=(
                    "A user asks the runtime to parse model text into a schema and repair "
                    "invalid JSON when needed."
                ),
                provider=provider,
                prompt=prompt,
                result=result,
                extra={"validated_output": _model_output(result.output)},
            )
        finally:
            await runtime.close()


async def test_journey_request_controls_usage_and_cost_metadata() -> None:
    for provider in _available_providers():
        runtime = _runtime_for(provider)
        prompt = (
            "Write a concise implementation note for an app that records model usage and "
            "estimated cost after each provider turn."
        )
        kwargs: dict[str, Any] = {"max_output_tokens": _output_budget(provider, 1200)}
        if provider.key != "openai":
            kwargs["temperature"] = 0.2
        if provider.key != "xai":
            kwargs["instructions"] = "Use compact engineering language."
        if provider.supports_compaction:
            kwargs["compaction"] = CompactionControl(strategy="auto")
        if provider.supports_cache_key:
            kwargs["cache"] = ModelCacheControl(
                key="agent-runtime-journey-cache",
                ttl="24h",
            )
        try:
            result = await runtime.models.run(
                provider=provider.key,
                model=provider.model,
                input=prompt,
                **kwargs,
            )
            _print_turn_report(
                journey="request_controls_usage_and_cost_metadata",
                goal=(
                    "A user tunes provider-native request controls and receives normalized "
                    "usage plus app-supplied cost estimates."
                ),
                provider=provider,
                prompt=prompt,
                result=result,
                extra={"requested_controls": sorted(kwargs)},
            )
        finally:
            await runtime.close()


async def test_journey_hosted_web_search_recent_stock_news() -> None:
    for provider in _available_providers(web_search=True):
        runtime = _runtime_for(provider)
        prompt = (
            "You are powering an app that pulls recent market news from the web. Use one "
            "provider web search query for recent NVIDIA and Microsoft stock news, then "
            "answer in no more than 140 words. Start with the search date context."
        )
        try:
            result: AgentResult[str] = await runtime.run(
                provider=provider.ref,
                input=prompt,
                instructions=(
                    "Use the web search tool briefly. Do not continue researching after you "
                    "have enough evidence for a short app-facing update."
                ),
                hosted_tools=[WebSearch(search_context_size="low")],
                max_output_tokens=_output_budget(provider, 7000),
            )
            _print_agent_report(
                journey="hosted_web_search_recent_stock_news",
                goal=(
                    "A user builds a news app and relies on provider-integrated web search "
                    "for current stock-related updates."
                ),
                provider=provider,
                prompt=prompt,
                result=result,
            )
        finally:
            await runtime.close()


async def test_journey_openai_file_search_private_knowledge_base() -> None:
    vector_store_id = os.environ.get("OPENAI_VECTOR_STORE_ID")
    if not vector_store_id:
        pytest.skip("OPENAI_VECTOR_STORE_ID is required for the file-search journey.")
    provider = _provider_by_key("openai")
    runtime = _runtime_for(provider)
    prompt = (
        "Search the attached vector store for policy or product facts relevant to onboarding. "
        "Return a concise answer with any source context the provider makes available."
    )
    try:
        result: AgentResult[str] = await runtime.run(
            provider=provider.ref,
            input=prompt,
            hosted_tools=[FileSearch(vector_store_ids=[vector_store_id], include_results=True)],
            max_output_tokens=_output_budget(provider, 1200),
        )
        _print_agent_report(
            journey="openai_file_search_private_knowledge_base",
            goal=(
                "A user connects private indexed files and asks the provider-hosted file "
                "search tool to ground the answer."
            ),
            provider=provider,
            prompt=prompt,
            result=result,
        )
    finally:
        await runtime.close()


async def test_journey_provider_native_remote_mcp_server() -> None:
    server_url = os.environ.get("JOURNEY_MCP_SERVER_URL")
    if not server_url:
        pytest.skip("JOURNEY_MCP_SERVER_URL is required for the remote-MCP journey.")
    for provider in _available_providers(remote_mcp=True):
        runtime = _runtime_for(provider)
        prompt = (
            "Use the remote MCP server if it has relevant tools. Summarize what the server "
            "made available and provide a short answer for an app developer."
        )
        try:
            result: AgentResult[str] = await runtime.run(
                provider=provider.ref,
                input=prompt,
                hosted_tools=[
                    RemoteMCP(
                        server_label="journey",
                        server_url=server_url,
                        require_approval="never",
                    )
                ],
                max_output_tokens=_output_budget(provider, 1200),
            )
            _print_agent_report(
                journey="provider_native_remote_mcp_server",
                goal=(
                    "A user connects a provider-native remote MCP server and expects MCP "
                    "events plus result metadata."
                ),
                provider=provider,
                prompt=prompt,
                result=result,
            )
        finally:
            await runtime.close()


async def test_journey_openai_tool_search_catalog() -> None:
    if os.environ.get("RUN_OPENAI_TOOL_SEARCH_JOURNEY") != "1":
        pytest.skip("Set RUN_OPENAI_TOOL_SEARCH_JOURNEY=1 to run the OpenAI tool-search journey.")
    provider = _provider_by_key("openai")
    runtime = _runtime_for(provider)
    prompt = (
        "Use provider tool search to choose the best catalog tool for checking deployment "
        "status. Then explain which tool you would call and why."
    )
    tools = [
        {
            "type": "function",
            "name": "deployment_status",
            "description": "Check deployment status by service name.",
            "parameters": {
                "type": "object",
                "properties": {"service": {"type": "string"}},
                "required": ["service"],
            },
        },
        {
            "type": "function",
            "name": "billing_invoice_lookup",
            "description": "Look up billing invoices.",
            "parameters": {
                "type": "object",
                "properties": {"invoice_id": {"type": "string"}},
                "required": ["invoice_id"],
            },
        },
    ]
    try:
        result: AgentResult[str] = await runtime.run(
            provider=provider.ref,
            input=prompt,
            hosted_tools=[
                ToolSearch(
                    namespaces=[
                        ToolNamespace(
                            name="ops_catalog",
                            description="Operational app tools",
                            tools=tools,
                        )
                    ],
                    max_results=3,
                )
            ],
            tool_search=ToolSearchControl(enabled=True),
            max_output_tokens=_output_budget(provider, 800),
        )
        _print_agent_report(
            journey="openai_tool_search_catalog",
            goal=(
                "A user exposes a searchable tool catalog and lets the provider choose relevant "
                "tools before loading or calling them."
            ),
            provider=provider,
            prompt=prompt,
            result=result,
        )
    finally:
        await runtime.close()


async def test_journey_openai_code_interpreter_analysis() -> None:
    if os.environ.get("RUN_OPENAI_CODE_INTERPRETER_JOURNEY") != "1":
        pytest.skip(
            "Set RUN_OPENAI_CODE_INTERPRETER_JOURNEY=1 to run the OpenAI code-interpreter journey."
        )
    provider = _provider_by_key("openai")
    runtime = _runtime_for(provider)
    prompt = (
        "Use code interpreter to compute the average daily return for these percentages: "
        "1.2, -0.4, 0.8, 2.1, -1.0. Explain the calculation briefly."
    )
    try:
        result: AgentResult[str] = await runtime.run(
            provider=provider.ref,
            input=prompt,
            hosted_tools=[CodeInterpreter()],
            max_output_tokens=_output_budget(provider, 1000),
        )
        _print_agent_report(
            journey="openai_code_interpreter_analysis",
            goal="A user delegates computation to a provider-hosted code interpreter.",
            provider=provider,
            prompt=prompt,
            result=result,
        )
    finally:
        await runtime.close()


async def test_journey_openai_image_generation_artifact() -> None:
    if os.environ.get("RUN_OPENAI_IMAGE_GENERATION_JOURNEY") != "1":
        pytest.skip(
            "Set RUN_OPENAI_IMAGE_GENERATION_JOURNEY=1 to run the OpenAI image-generation journey."
        )
    provider = _provider_by_key("openai")
    runtime = _runtime_for(provider)
    prompt = (
        "Generate a simple product-style bitmap image of a small dashboard card showing "
        "provider events, then describe what was generated."
    )
    try:
        result: AgentResult[str] = await runtime.run(
            provider=provider.ref,
            input=prompt,
            hosted_tools=[ImageGeneration(size="1024x1024", quality="low")],
            max_output_tokens=_output_budget(provider, 900),
        )
        _print_agent_report(
            journey="openai_image_generation_artifact",
            goal=(
                "A user asks a provider-hosted image generation tool to produce an artifact "
                "and expects artifact metadata in the runtime result."
            ),
            provider=provider,
            prompt=prompt,
            result=result,
        )
    finally:
        await runtime.close()


def _provider_by_key(key: str) -> LiveProvider:
    for provider in _available_providers():
        if provider.key == key:
            return provider
    pytest.skip(f"{key} provider is not configured for this journey.")


def _output_budget(provider: LiveProvider, requested: int) -> int:
    reasoning_heavy_prefixes = ("gpt-5", "o1", "o3", "o4")
    if provider.key == "openai" and provider.model.startswith(reasoning_heavy_prefixes):
        return max(requested, 2048)
    return requested


def _print_turn_report(
    *,
    journey: str,
    goal: str,
    provider: LiveProvider,
    prompt: str,
    result: Any,
    extra: dict[str, Any] | None = None,
) -> None:
    _print_report(
        journey=journey,
        goal=goal,
        provider=provider,
        prompt=prompt,
        response_text=result.text,
        events=result.events,
        provider_state=result.provider_state,
        metadata=result.metadata,
        extra=extra,
    )


def _print_agent_report(
    *,
    journey: str,
    goal: str,
    provider: LiveProvider,
    prompt: str,
    result: AgentResult[Any],
    extra: dict[str, Any] | None = None,
) -> None:
    merged_extra = {
        "output": _model_output(result.output),
        "items": [_run_item_summary(item) for item in result.items],
        "payloads": [_payload_summary(payload) for payload in result.payloads],
        **(extra or {}),
    }
    _print_report(
        journey=journey,
        goal=goal,
        provider=provider,
        prompt=prompt,
        response_text=result.text,
        events=result.events,
        provider_state=result.provider_state,
        metadata=result.metadata,
        artifacts=[_artifact_summary(artifact) for artifact in result.artifacts],
        extra=merged_extra,
    )


def _print_report(
    *,
    journey: str,
    goal: str,
    provider: LiveProvider,
    prompt: str,
    response_text: str,
    events: Sequence[AgentEvent],
    provider_state: ProviderState | None = None,
    metadata: dict[str, Any] | None = None,
    artifacts: list[dict[str, Any]] | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    report = {
        "journey": journey,
        "goal": goal,
        "provider": provider.key,
        "model": provider.model,
        "prompt": prompt,
        "response_text": _clip(response_text),
        "event_summary": _event_summary(events),
        "provider_state": _provider_state_summary(provider_state),
        "metadata": _jsonable(metadata or {}),
        "artifacts": artifacts or [],
        "extra": _jsonable(extra or {}),
    }
    print("\n=== MODEL_PROVIDER_JOURNEY_REPORT ===")
    print(json.dumps(report, indent=2, sort_keys=True, default=str))
    print("=== END_MODEL_PROVIDER_JOURNEY_REPORT ===\n")


def _event_summary(events: Sequence[AgentEvent]) -> dict[str, Any]:
    counts = Counter(event.type for event in events)
    samples: list[dict[str, Any]] = []
    for event in events[:12]:
        item = event.data.get("item")
        samples.append(
            {
                "type": event.type,
                "provider": event.provider,
                "item_id": event.item_id,
                "data_keys": sorted(event.data),
                "item_type": event.data.get("item_type"),
                "hosted_tool_type": event.data.get("hosted_tool_type"),
                "tool_name": event.data.get("name"),
                "run_item_type": getattr(item, "type", None),
            }
        )
    return {
        "total_events": len(events),
        "counts": dict(sorted(counts.items())),
        "samples": samples,
    }


def _provider_state_summary(state: ProviderState | None) -> dict[str, Any] | None:
    if state is None:
        return None
    return {
        "provider": state.provider,
        "has_previous_response_id": bool(state.previous_response_id),
        "has_conversation_id": bool(state.conversation_id),
        "native_history_count": (
            len(state.native_history) if isinstance(state.native_history, list) else None
        ),
        "continuation_keys": sorted(state.continuation),
        "reasoning_state_keys": sorted(state.reasoning_state),
        "tool_state_keys": sorted(state.tool_state),
    }


def _run_item_summary(item: Any) -> dict[str, Any]:
    return {
        "id": getattr(item, "id", None),
        "type": getattr(item, "type", None),
        "status": getattr(item, "status", None),
        "data": _jsonable(getattr(item, "data", {})),
    }


def _payload_summary(payload: Any) -> dict[str, Any]:
    return {
        "tool_name": getattr(payload, "tool_name", None),
        "call_id": getattr(payload, "call_id", None),
        "payload": _jsonable(getattr(payload, "payload", None)),
    }


def _artifact_summary(artifact: Any) -> dict[str, Any]:
    data = getattr(artifact, "data", None)
    return {
        "id": getattr(artifact, "id", None),
        "type": getattr(artifact, "type", None),
        "name": getattr(artifact, "name", None),
        "uri": getattr(artifact, "uri", None),
        "data_present": data is not None,
        "metadata": _jsonable(getattr(artifact, "metadata", {})),
    }


def _model_output(value: Any) -> Any:
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        return dump()
    return _jsonable(value)


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
