"""Tests for OutputSpec.posthoc_parse_with_retry.

Covers VALIDATION row 1.9: when the model returns text that fails schema
validation, the runtime can feed the error back and let the model repair its
answer up to ``max_validation_retries`` times.
"""
from __future__ import annotations

import json

import pytest
from pydantic import BaseModel

from agent_runtime import AgentRuntime, OutputValidationError
from agent_runtime.core.results import OutputSpec
from tests.fixtures.scripted_model import ScriptedModelProvider, text_only_turn


class Decision(BaseModel):
    priority: str
    summary: str


def _runtime() -> tuple[AgentRuntime, ScriptedModelProvider]:
    runtime = AgentRuntime()
    scripted = ScriptedModelProvider()
    runtime.registry.register_model(scripted)
    return runtime, scripted


async def test_retry_succeeds_after_validation_failure() -> None:
    runtime, scripted = _runtime()
    scripted.queue(text_only_turn("not even close to JSON"))
    scripted.queue(
        text_only_turn(json.dumps({"priority": "high", "summary": "fix it"}))
    )

    result = await runtime.run(
        provider="scripted:test",
        input="Decide priority for ticket #42.",
        output_spec=OutputSpec(
            schema=Decision,
            strategy="posthoc_parse_with_retry",
            max_validation_retries=1,
        ),
    )

    assert isinstance(result.output, Decision)
    assert result.output.priority == "high"
    assert result.metadata["validation_attempts"] == 2
    # Both attempts consumed by the scripted provider.
    assert len(scripted.calls) == 2
    # The repair prompt embeds the prior bad text and the validation error.
    second_input = scripted.calls[1].input
    assert isinstance(second_input, str)
    assert "not even close to JSON" in second_input
    assert "Validation error" in second_input


async def test_retry_exhausted_raises_output_validation_error() -> None:
    runtime, scripted = _runtime()
    scripted.queue(text_only_turn("garbage 1"))
    scripted.queue(text_only_turn("garbage 2"))

    with pytest.raises(OutputValidationError) as excinfo:
        await runtime.run(
            provider="scripted:test",
            input="please decide",
            output_spec=OutputSpec(
                schema=Decision,
                strategy="posthoc_parse_with_retry",
                max_validation_retries=1,
            ),
        )

    # Final attempt's text is preserved on the raised error.
    assert excinfo.value.raw_text == "garbage 2"
    assert len(scripted.calls) == 2


async def test_default_strategy_does_not_retry() -> None:
    runtime, scripted = _runtime()
    scripted.queue(text_only_turn("garbage"))

    with pytest.raises(OutputValidationError):
        await runtime.run(
            provider="scripted:test",
            input="please decide",
            output_type=Decision,  # default strategy = posthoc_parse
        )
    assert len(scripted.calls) == 1


async def test_first_attempt_success_records_single_attempt() -> None:
    runtime, scripted = _runtime()
    scripted.queue(
        text_only_turn(json.dumps({"priority": "low", "summary": "ok"}))
    )

    result = await runtime.run(
        provider="scripted:test",
        input="please decide",
        output_spec=OutputSpec(
            schema=Decision,
            strategy="posthoc_parse_with_retry",
            max_validation_retries=2,
        ),
    )

    assert result.metadata["validation_attempts"] == 1
    assert len(scripted.calls) == 1


async def test_output_type_and_output_spec_are_mutually_exclusive() -> None:
    runtime, _ = _runtime()
    with pytest.raises(ValueError, match="not both"):
        await runtime.run(
            provider="scripted:test",
            input="hi",
            output_type=Decision,
            output_spec=OutputSpec(schema=Decision),
        )
