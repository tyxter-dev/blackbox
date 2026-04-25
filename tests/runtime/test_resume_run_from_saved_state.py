"""Resume-from-saved-state (VALIDATION 5.7).

Demonstrates the full loop: run a turn, save the captured ``RunState`` to a
``SQLiteRunStore``, then load it from a fresh ``AgentRuntime`` instance and
continue. The scripted provider asserts that the second turn's
``TurnRequest`` carries the prior ``provider_state``.
"""
from __future__ import annotations

from pathlib import Path

from agent_runtime import AgentRuntime
from agent_runtime.core.state import RunState
from agent_runtime.core.stores import SQLiteRunStore
from tests.fixtures.scripted_model import ScriptedModelProvider, text_only_turn


def _runtime() -> tuple[AgentRuntime, ScriptedModelProvider]:
    runtime = AgentRuntime()
    scripted = ScriptedModelProvider()
    runtime.registry.register_model(scripted)
    return runtime, scripted


async def test_resume_run_picks_up_saved_provider_state(tmp_path: Path) -> None:
    db_path = tmp_path / "runs.sqlite"

    # First runtime: complete a turn and save state to disk.
    runtime_a, scripted_a = _runtime()
    scripted_a.queue(text_only_turn("first answer"))
    first = await runtime_a.run(provider="scripted:test", input="initial")
    assert first.text == "first answer"
    assert first.provider_state is not None

    saved = RunState(
        provider="scripted",
        model="test",
        provider_state=first.provider_state,
        metadata={"phase": "checkpoint"},
    )
    store_a = SQLiteRunStore(db_path)
    await store_a.save(saved)
    await store_a.close()

    # Fresh process: new runtime, new scripted provider, same DB on disk.
    runtime_b, scripted_b = _runtime()
    store_b = SQLiteRunStore(db_path)
    loaded = await store_b.load(saved.session_id)
    await store_b.close()
    assert loaded is not None
    assert loaded.provider_state is not None
    assert (
        loaded.provider_state.previous_response_id
        == first.provider_state.previous_response_id
    )
    assert loaded.metadata == {"phase": "checkpoint"}

    # Resume: feed the loaded state back into runtime.run.
    scripted_b.queue(text_only_turn("second answer"))
    second = await runtime_b.run(
        provider="scripted:test",
        input="continue",
        provider_state=loaded.provider_state,
    )
    assert second.text == "second answer"

    # The scripted provider saw exactly one turn whose request carried the
    # resumed provider_state — proves the runtime plumbed it through the
    # high-level surface.
    assert len(scripted_b.calls) == 1
    resumed_request = scripted_b.calls[0]
    assert resumed_request.provider_state is not None
    assert (
        resumed_request.provider_state.previous_response_id
        == loaded.provider_state.previous_response_id
    )


async def test_resume_without_saved_state_starts_fresh() -> None:
    runtime, scripted = _runtime()
    scripted.queue(text_only_turn("ok"))
    await runtime.run(provider="scripted:test", input="hi")
    assert scripted.calls[0].provider_state is None
