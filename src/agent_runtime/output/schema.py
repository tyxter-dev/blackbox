from __future__ import annotations

import dataclasses
import re
from dataclasses import MISSING, dataclass
from types import UnionType
from typing import Any, Union, get_args, get_origin, get_type_hints

from agent_runtime.core.results import OutputSpec


@dataclass(slots=True, frozen=True)
class OutputSchema:
    name: str
    schema: dict[str, Any]
    description: str | None = None
    strict: bool = True
    target_type: type[Any] | dict[str, Any] | None = None


def build_output_schema(spec: OutputSpec) -> OutputSchema | None:
    if spec.schema is None or spec.schema is str:
        return None
    schema = _schema_for(spec.schema)
    name = _normalize_name(spec.name or _schema_title(schema) or _schema_name(spec.schema))
    description = spec.description or _schema_description(schema)
    return OutputSchema(
        name=name,
        schema=schema,
        description=description,
        strict=spec.strict,
        target_type=spec.schema,
    )


def _schema_for(target: type[Any] | dict[str, Any]) -> dict[str, Any]:
    if isinstance(target, dict):
        return dict(target)

    model_json_schema = getattr(target, "model_json_schema", None)
    if callable(model_json_schema):
        schema = model_json_schema()
        return dict(schema) if isinstance(schema, dict) else {}

    if dataclasses.is_dataclass(target):
        return _dataclass_schema(target)

    raise TypeError(
        f"Unsupported output schema target: {target!r}. "
        "Use a Pydantic model, dataclass, raw JSON Schema dict, or str."
    )


def _dataclass_schema(target: type[Any]) -> dict[str, Any]:
    properties: dict[str, Any] = {}
    required: list[str] = []
    type_hints = get_type_hints(target)
    for field in dataclasses.fields(target):
        properties[field.name] = _annotation_schema(type_hints.get(field.name, field.type))
        if field.default is MISSING and field.default_factory is MISSING:
            required.append(field.name)

    schema: dict[str, Any] = {
        "type": "object",
        "title": target.__name__,
        "properties": properties,
        "additionalProperties": False,
    }
    if required:
        schema["required"] = required
    return schema


def _annotation_schema(annotation: Any) -> dict[str, Any]:
    origin = get_origin(annotation)
    args = get_args(annotation)

    if annotation is str:
        return {"type": "string"}
    if annotation is int:
        return {"type": "integer"}
    if annotation is float:
        return {"type": "number"}
    if annotation is bool:
        return {"type": "boolean"}
    if annotation is Any:
        return {}
    if origin is list:
        item_type = args[0] if args else Any
        return {"type": "array", "items": _annotation_schema(item_type)}
    if origin is dict:
        return {"type": "object"}
    if origin in {Union, UnionType}:
        non_none = [arg for arg in args if arg is not type(None)]
        has_none = len(non_none) != len(args)
        if len(non_none) == 1:
            schema = _annotation_schema(non_none[0])
            if has_none and "type" in schema:
                schema = dict(schema)
                schema["type"] = [schema["type"], "null"]
            return schema

    if isinstance(annotation, type) and dataclasses.is_dataclass(annotation):
        return _dataclass_schema(annotation)

    return {}


def _normalize_name(value: str | None) -> str:
    base = value or "output"
    normalized = re.sub(r"[^a-zA-Z0-9_-]+", "_", base).strip("_")
    if not normalized:
        normalized = "output"
    return normalized[:64]


def _schema_title(schema: dict[str, Any]) -> str | None:
    title = schema.get("title")
    return title if isinstance(title, str) else None


def _schema_description(schema: dict[str, Any]) -> str | None:
    description = schema.get("description")
    return description if isinstance(description, str) else None


def _schema_name(target: type[Any] | dict[str, Any]) -> str:
    if isinstance(target, dict):
        return "output"
    return getattr(target, "__name__", "output")
