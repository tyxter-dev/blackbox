"""Persistent conversation resumed in a fresh process from disk.

Mirrors the production pattern from ``docs/USE_CASE_VALIDATION.md`` (all 610
agents persist full conversation memory across sessions): provider-native
continuation state - not a normalized chat transcript - is checkpointed to a
``SQLiteRunStore`` and reloaded by a completely fresh ``AgentRuntime``,
simulating a process restart between two customer messages.

The scripted provider keeps its history in ``ProviderState.native_history``.
"Process B" answers a question whose answer only exists in the state loaded
from disk, proving the continuation actually round-tripped.

Run::

    python examples/conversation_resume.py
"""
from __future__ import annotations

# ruff: noqa: E402
import asyncio
import re
from collections.abc import AsyncIterator
from pathlib import Path

from _bootstrap import bootstrap

bootstrap()

from blackbox import AgentRuntime
from blackbox.core.capabilities import ModelCapabilities
from blackbox.core.events import AgentEvent, EventTypes
from blackbox.core.state import ProviderState, RunState
from blackbox.core.stores import SQLiteRunStore
from blackbox.providers.base import TurnRequest

DB_PATH = Path(__file__).resolve().parent / "state" / "conversation.sqlite"


class MemoryfulModel:
    """Offline model whose only memory is the ProviderState it returns.

    Each turn appends to ``native_history`` and answers from it, the way a
    real adapter preserves provider-native history for continuation.
    """

    provider_id = "memoryful"

    def capabilities(self, model: str | None = None) -> ModelCapabilities:
        return ModelCapabilities(supports_streaming_events=True, supports_provider_state=True)

    async def stream_turn(self, request: TurnRequest) -> AsyncIterator[AgentEvent]:
        text = request.input if isinstance(request.input, str) else str(request.input)
        history = list(request.provider_state.native_history) if request.provider_state else []
        history.append({"role": "user", "text": text})

        reply = self._reply(text, history)
        history.append({"role": "assistant", "text": reply})

        yield AgentEvent(type=EventTypes.MODEL_REQUEST_STARTED, provider=self.provider_id)
        yield AgentEvent(
            type=EventTypes.MODEL_TEXT_DELTA, provider=self.provider_id, data={"delta": reply}
        )
        yield AgentEvent(
            type=EventTypes.MODEL_COMPLETED,
            provider=self.provider_id,
            data={
                "provider_state": ProviderState(
                    provider=self.provider_id,
                    previous_response_id=f"resp_{len(history)}",
                    native_history=history,
                )
            },
        )

    @staticmethod
    def _reply(text: str, history: list[dict]) -> str:
        if "name" in text.lower() and "?" in text:
            for message in history:
                match = re.search(r"my name is (\w+)", message["text"], re.IGNORECASE)
                if match:
                    return (
                        f"You're {match.group(1)} - we spoke about a kitchen "
                        "renovation quote. Picking up right where we left off."
                    )
            return "I don't have your name on file yet."
        return "Nice to meet you! I noted the kitchen renovation request; a quote follows shortly."


def _fresh_runtime() -> AgentRuntime:
    runtime = AgentRuntime()
    runtime.registry.register_model(MemoryfulModel())
    return runtime


async def process_a() -> str:
    """First process: handle the opening message and checkpoint to disk."""

    runtime = _fresh_runtime()
    result = await runtime.run(
        provider="memoryful:demo",
        input="Hi! My name is Helena, I'd like a quote for a kitchen renovation.",
    )
    print("process A  customer: Hi! My name is Helena, I'd like a quote ...")
    print(f"process A  agent:    {result.text}")

    assert result.provider_state is not None
    checkpoint = RunState(
        provider="memoryful",
        model="demo",
        provider_state=result.provider_state,
        metadata={"channel": "whatsapp", "phase": "awaiting_quote"},
    )
    store = SQLiteRunStore(DB_PATH)
    await store.save(checkpoint)
    await store.close()
    print(f"process A  saved session {checkpoint.session_id} "
          f"({len(result.provider_state.native_history)} history entries) to {DB_PATH.name}\n")
    return checkpoint.session_id


async def process_b(session_id: str) -> None:
    """Fresh process days later: reload state from disk and continue."""

    runtime = _fresh_runtime()
    store = SQLiteRunStore(DB_PATH)
    loaded = await store.load(session_id)
    await store.close()
    assert loaded is not None and loaded.provider_state is not None
    print(f"process B  loaded session {session_id} "
          f"({len(loaded.provider_state.native_history)} history entries, "
          f"metadata={loaded.metadata})")

    result = await runtime.run(
        provider="memoryful:demo",
        input="Hey, it's me again - do you still know my name?",
        provider_state=loaded.provider_state,
    )
    print("process B  customer: Hey, it's me again - do you still know my name?")
    print(f"process B  agent:    {result.text}")
    assert "Helena" in result.text, "resumed state did not reach the model"


async def main() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    if DB_PATH.exists():
        DB_PATH.unlink()
    session_id = await process_a()
    await process_b(session_id)


if __name__ == "__main__":
    asyncio.run(main())
