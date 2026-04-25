from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, Field

from agent_runtime.core.results import OutputSpec
from agent_runtime.output.schema import build_output_schema


class Decision(BaseModel):
    priority: str = Field(description="Priority label")
    escalate: bool


@dataclass
class Incident:
    title: str
    count: int
    tags: list[str]


def test_pydantic_model_builds_output_schema() -> None:
    schema = build_output_schema(
        OutputSpec(schema=Decision, name="decision-output", description="Decision", strict=True)
    )

    assert schema is not None
    assert schema.name == "decision-output"
    assert schema.description == "Decision"
    assert schema.strict is True
    assert schema.target_type is Decision
    assert schema.schema["type"] == "object"
    assert "priority" in schema.schema["properties"]


def test_dataclass_builds_json_schema() -> None:
    schema = build_output_schema(OutputSpec(schema=Incident))

    assert schema is not None
    assert schema.name == "Incident"
    assert schema.schema == {
        "type": "object",
        "title": "Incident",
        "properties": {
            "title": {"type": "string"},
            "count": {"type": "integer"},
            "tags": {"type": "array", "items": {"type": "string"}},
        },
        "additionalProperties": False,
        "required": ["title", "count", "tags"],
    }


def test_raw_dict_schema_is_preserved() -> None:
    raw = {
        "title": "Report",
        "type": "object",
        "properties": {"summary": {"type": "string"}},
        "required": ["summary"],
    }

    schema = build_output_schema(OutputSpec(schema=raw, strict=False))

    assert schema is not None
    assert schema.name == "Report"
    assert schema.schema == raw
    assert schema.strict is False
    assert schema.target_type == raw


def test_schema_name_is_normalized_and_limited() -> None:
    schema = build_output_schema(
        OutputSpec(schema={"type": "object"}, name="bad name with spaces!" * 10)
    )

    assert schema is not None
    assert " " not in schema.name
    assert "!" not in schema.name
    assert len(schema.name) == 64


def test_str_or_missing_schema_does_not_build_output_schema() -> None:
    assert build_output_schema(OutputSpec(schema=None)) is None
    assert build_output_schema(OutputSpec(schema=str)) is None
