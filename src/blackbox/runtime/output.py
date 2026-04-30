from __future__ import annotations

import dataclasses
import json
from typing import Any

from blackbox.core.errors import OutputValidationError
from blackbox.core.results import OutputSpec, OutputStrategy
from blackbox.output.schema import OutputSchema

_FINALIZER_TOOL_NAME = "submit_final_output"


def _resolve_output_spec(
    output_type: type[Any] | None, output_spec: OutputSpec | None
) -> OutputSpec:
    if output_type is not None and output_spec is not None:
        raise ValueError("Pass either output_type or output_spec, not both.")
    if output_spec is not None:
        return output_spec
    return OutputSpec(schema=output_type, strategy="posthoc_parse")


def _can_fallback_provider_native(
    *,
    effective_strategy: OutputStrategy | None,
    output_schema: OutputSchema | None,
    fallback: str,
    already_used: bool,
) -> bool:
    return (
        effective_strategy == "provider_native"
        and output_schema is not None
        and fallback in {"posthoc_parse", "finalizer_tool"}
        and not already_used
    )


def _finalizer_tool_payload(output_schema: OutputSchema) -> dict[str, Any]:
    description = output_schema.description or "Submit the final structured output."
    return {
        "type": "function",
        "name": _FINALIZER_TOOL_NAME,
        "description": description,
        "parameters": output_schema.schema,
        "metadata": {
            "blackbox_hidden": True,
            "purpose": "final_output",
            "schema_name": output_schema.name,
        },
    }


def _build_repair_prompt(
    *, previous_text: str, error: OutputValidationError, attempt: int
) -> str:
    return (
        "Your previous response failed schema validation.\n\n"
        f"Previous response:\n{previous_text}\n\n"
        f"Validation error: {error}\n\n"
        f"Please return a corrected response (attempt {attempt + 1}) that "
        "matches the requested schema exactly. Do not include any commentary "
        "outside the structured output."
    )


def _validate_output(
    text: str,
    output_type: type[Any] | dict[str, Any] | None,
    *,
    allow_dict_schema: bool = False,
) -> Any:
    """Coerce final text into the requested output type or JSON schema."""

    if output_type is None or output_type is str:
        return text
    if isinstance(output_type, dict):
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise OutputValidationError(
                "Final output is not valid JSON for the requested JSON Schema.",
                raw_text=text,
                cause=exc,
            ) from exc
        try:
            _validate_json_schema(data, output_type)
        except ValueError as exc:
            raise OutputValidationError(
                f"Final output failed validation against JSON Schema: {exc}",
                raw_text=text,
                cause=exc,
            ) from exc
        return data
    if dataclasses.is_dataclass(output_type):
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise OutputValidationError(
                f"Final output is not valid JSON for {output_type.__name__}",
                raw_text=text,
                cause=exc,
            ) from exc
        try:
            return output_type(**data)
        except TypeError as exc:
            raise OutputValidationError(
                f"Final output cannot be coerced to {output_type.__name__}: {exc}",
                raw_text=text,
                cause=exc,
            ) from exc

    pydantic_base: type[Any] | None
    try:
        from pydantic import BaseModel as _PydanticBaseModel

        pydantic_base = _PydanticBaseModel
    except ImportError:
        pydantic_base = None

    if (
        pydantic_base is not None
        and isinstance(output_type, type)
        and issubclass(output_type, pydantic_base)
    ):
        try:
            return output_type.model_validate_json(text)
        except Exception as exc:
            raise OutputValidationError(
                f"Final output failed validation against {output_type.__name__}: {exc}",
                raw_text=text,
                cause=exc,
            ) from exc

    raise OutputValidationError(
        f"Unsupported output_type: {output_type!r}. Use str, a Pydantic model, or a dataclass.",
        raw_text=text,
    )


def _validate_json_schema(value: Any, schema: dict[str, Any], *, path: str = "$") -> None:
    if "const" in schema and value != schema["const"]:
        raise ValueError(f"{path} must equal {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError(f"{path} must be one of {schema['enum']!r}")
    if "anyOf" in schema:
        _validate_any_of(value, schema["anyOf"], path=path)
    if "oneOf" in schema:
        _validate_one_of(value, schema["oneOf"], path=path)

    expected_type = schema.get("type")
    if expected_type is not None and not _matches_json_type(value, expected_type):
        raise ValueError(f"{path} must be {_type_label(expected_type)}")

    if isinstance(value, dict):
        _validate_object(value, schema, path=path)
    elif isinstance(value, list):
        _validate_array(value, schema, path=path)


def _validate_object(value: dict[str, Any], schema: dict[str, Any], *, path: str) -> None:
    required = schema.get("required", [])
    if isinstance(required, list):
        for key in required:
            if isinstance(key, str) and key not in value:
                raise ValueError(f"{path}.{key} is required")
    properties = schema.get("properties", {})
    if isinstance(properties, dict):
        for key, subschema in properties.items():
            if key in value and isinstance(subschema, dict):
                _validate_json_schema(value[key], subschema, path=f"{path}.{key}")
    additional = schema.get("additionalProperties", True)
    if additional is False and isinstance(properties, dict):
        extras = sorted(set(value) - set(properties))
        if extras:
            raise ValueError(f"{path} contains unknown properties: {', '.join(extras)}")
    elif isinstance(additional, dict):
        for key in set(value) - set(properties if isinstance(properties, dict) else {}):
            _validate_json_schema(value[key], additional, path=f"{path}.{key}")


def _validate_array(value: list[Any], schema: dict[str, Any], *, path: str) -> None:
    min_items = schema.get("minItems")
    if isinstance(min_items, int) and len(value) < min_items:
        raise ValueError(f"{path} must contain at least {min_items} items")
    max_items = schema.get("maxItems")
    if isinstance(max_items, int) and len(value) > max_items:
        raise ValueError(f"{path} must contain at most {max_items} items")
    items = schema.get("items")
    if isinstance(items, dict):
        for index, item in enumerate(value):
            _validate_json_schema(item, items, path=f"{path}[{index}]")


def _validate_any_of(value: Any, schemas: Any, *, path: str) -> None:
    if not isinstance(schemas, list):
        return
    for schema in schemas:
        if not isinstance(schema, dict):
            continue
        try:
            _validate_json_schema(value, schema, path=path)
            return
        except ValueError:
            continue
    raise ValueError(f"{path} does not match any allowed schema")


def _validate_one_of(value: Any, schemas: Any, *, path: str) -> None:
    if not isinstance(schemas, list):
        return
    matches = 0
    for schema in schemas:
        if not isinstance(schema, dict):
            continue
        try:
            _validate_json_schema(value, schema, path=path)
        except ValueError:
            continue
        matches += 1
    if matches != 1:
        raise ValueError(f"{path} must match exactly one allowed schema")


def _matches_json_type(value: Any, expected_type: Any) -> bool:
    if isinstance(expected_type, list):
        return any(_matches_json_type(value, item) for item in expected_type)
    if expected_type == "object":
        return isinstance(value, dict)
    if expected_type == "array":
        return isinstance(value, list)
    if expected_type == "string":
        return isinstance(value, str)
    if expected_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected_type == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected_type == "boolean":
        return isinstance(value, bool)
    if expected_type == "null":
        return value is None
    return True


def _type_label(expected_type: Any) -> str:
    if isinstance(expected_type, list):
        return " or ".join(str(item) for item in expected_type)
    return str(expected_type)
