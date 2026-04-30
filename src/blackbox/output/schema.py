from __future__ import annotations

import dataclasses
import re
from collections.abc import Hashable
from copy import deepcopy
from dataclasses import MISSING, dataclass
from functools import lru_cache
from types import UnionType
from typing import Any, Union, cast, get_args, get_origin, get_type_hints

from blackbox.core.results import OutputSpec


@dataclass(slots=True, frozen=True)
class OutputSchema:
    """Provider-ready schema metadata for structured model responses."""

    name: str
    schema: dict[str, Any]
    description: str | None = None
    strict: bool = True
    target_type: type[Any] | dict[str, Any] | None = None


def build_output_schema(spec: OutputSpec) -> OutputSchema | None:
    if spec.schema is None or spec.schema is str:
        return None
    schema = _schema_for_spec(spec.schema, strict=spec.strict)
    name = _normalize_name(spec.name or _schema_title(schema) or _schema_name(spec.schema))
    description = spec.description or _schema_description(schema)
    return OutputSchema(
        name=name,
        schema=schema,
        description=description,
        strict=spec.strict,
        target_type=spec.schema,
    )


def clear_output_schema_cache() -> None:
    """Clear generated schema caches used by hot runtime paths."""

    _schema_for_type_cached.cache_clear()
    _strict_json_schema_for_type_cached.cache_clear()


def _schema_for_spec(target: type[Any] | dict[str, Any], *, strict: bool) -> dict[str, Any]:
    if isinstance(target, dict):
        schema = dict(target)
        return _strict_json_schema(schema) if strict else schema
    cache_key = cast(Hashable, target)
    if strict:
        return deepcopy(_strict_json_schema_for_type_cached(cache_key))
    return deepcopy(_schema_for_type_cached(cache_key))


@lru_cache(maxsize=128)
def _schema_for_type_cached(target: Hashable) -> dict[str, Any]:
    return _schema_for_type(cast(type[Any], target))


@lru_cache(maxsize=128)
def _strict_json_schema_for_type_cached(target: Hashable) -> dict[str, Any]:
    return _strict_json_schema(_schema_for_type(cast(type[Any], target)))


def _schema_for(target: type[Any] | dict[str, Any]) -> dict[str, Any]:
    if isinstance(target, dict):
        return dict(target)
    return deepcopy(_schema_for_type_cached(cast(Hashable, target)))


def _schema_for_type(target: type[Any]) -> dict[str, Any]:
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


def _strict_json_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Return a provider-friendly strict object schema.

    OpenAI's strict structured output mode requires object schemas to opt out
    of unknown keys with ``additionalProperties: false``. Pydantic does not add
    that by default, so normalize it here without mutating caller-owned schema
    objects.
    """

    normalized: dict[str, Any] = {}
    for key, value in schema.items():
        if key == "default":
            continue
        if isinstance(value, dict):
            normalized[key] = _strict_json_schema(value)
        elif isinstance(value, list):
            normalized[key] = [
                _strict_json_schema(item) if isinstance(item, dict) else item
                for item in value
            ]
        else:
            normalized[key] = value
    if normalized.get("type") == "object":
        normalized.setdefault("additionalProperties", False)
        properties = normalized.get("properties")
        if isinstance(properties, dict):
            normalized["required"] = list(properties)
        else:
            normalized.setdefault("required", [])
    return normalized


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
