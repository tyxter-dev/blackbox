from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from agent_runtime import AgentRuntime, EventTypes
from agent_runtime.models.anthropic_messages import _compose_messages
from agent_runtime.models.gemini_generate_content import _compose_contents
from agent_runtime.models.openai_responses import OpenAIResponsesProvider
from agent_runtime.providers.base import TurnRequest
from tests.fixtures.fake_openai_client import FakeOpenAIClient, FakeStream, evt, final_response


class RetryableStatusError(Exception):
    status_code = 503
    headers: ClassVar[dict[str, str]] = {}


class FailingEnterStream(FakeStream):
    async def __aenter__(self) -> FailingEnterStream:
        raise RetryableStatusError("temporary outage")


def test_openai_strips_runtime_private_message_fields() -> None:
    request = TurnRequest(
        model="gpt-test",
        input=[
            {
                "role": "system",
                "content": "cached prompt",
                "_cache_sections": [{"content": "cached prompt", "cacheable": True}],
            }
        ],
    )

    kwargs = OpenAIResponsesProvider._build_request_kwargs(request)

    assert kwargs["input"] == [{"role": "system", "content": "cached prompt"}]


def test_anthropic_strips_runtime_private_message_fields() -> None:
    request = TurnRequest(
        model="claude-test",
        input=[
            {
                "role": "user",
                "content": "hello",
                "_cache_sections": [{"content": "hello", "cacheable": True}],
            }
        ],
    )

    assert _compose_messages(request) == [{"role": "user", "content": "hello"}]


def test_gemini_strips_runtime_private_message_fields() -> None:
    request = TurnRequest(
        model="gemini-test",
        input=[
            {
                "role": "user",
                "parts": [{"text": "hello"}],
                "_cache_sections": [{"content": "hello", "cacheable": True}],
            }
        ],
    )

    assert _compose_contents(request) == [{"role": "user", "parts": [{"text": "hello"}]}]


async def test_openai_retries_pre_output_transient_stream_failure() -> None:
    client = FakeOpenAIClient()
    client.responses.queued_streams.append(FailingEnterStream(events=[]))
    client.queue(
        [evt("response.output_text.delta", delta="pong", item_id="msg_1")],
        final_response=final_response(id_="resp_1", output=[]),
    )
    provider = OpenAIResponsesProvider(
        client=client,
        max_retries=1,
        retry_min_delay=0,
    )

    events = [event async for event in provider.stream_turn(TurnRequest(model="gpt-test", input="hi"))]

    assert [event.data["delta"] for event in events if event.type == EventTypes.MODEL_TEXT_DELTA] == ["pong"]
    assert len(client.responses.seen_kwargs) == 2


@dataclass
class ClosableProvider:
    provider_id = "closable"
    closed: bool = False

    async def close(self) -> None:
        self.closed = True


async def test_runtime_close_closes_registered_providers_once() -> None:
    provider = ClosableProvider()
    runtime = AgentRuntime()
    runtime.registry.register_model(provider, "alias")

    await runtime.close()

    assert provider.closed is True
