"""Network-gated Codex subscription smoke test.

Run explicitly with ``RUN_CODEX_INTEGRATION=1 pytest -m integration_codex``
after installing ``blackbox[codex]`` and signing in to Codex.  The task is
ephemeral and read-only, so it neither reuses a persisted thread nor writes to
the checkout.
"""
from __future__ import annotations

import importlib.util
import os

import pytest

from blackbox.core.events import EventTypes
from blackbox.providers.agent_adapters.codex import CodexAgentProvider
from blackbox.providers.base import AgentSpec, TaskSpec

pytestmark = pytest.mark.integration_codex


async def test_codex_subscription_thread_streams_to_completion() -> None:
    if os.environ.get("RUN_CODEX_INTEGRATION") != "1":
        pytest.skip("Set RUN_CODEX_INTEGRATION=1 to exercise a Codex subscription.")
    if importlib.util.find_spec("openai_codex") is None:
        pytest.skip("Install blackbox[codex] to run the Codex integration smoke test.")

    provider = CodexAgentProvider()
    agent = await provider.create_agent(AgentSpec(name="read-only-codex"))
    session = await provider.start_session(
        agent,
        TaskSpec(
            prompt="Reply with exactly: blackbox codex integration ok",
            extra={"ephemeral": True, "sandbox": "read-only"},
        ),
    )
    events = [event async for event in provider.stream_events(session)]
    await provider.close()

    assert EventTypes.MODEL_TEXT_DELTA in [event.type for event in events]
    assert events[-1].type == EventTypes.SESSION_COMPLETED
