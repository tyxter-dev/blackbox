from __future__ import annotations

import importlib
import json
import re
from collections.abc import Mapping
from typing import Any

from blackbox.core.errors import ConfigurationError

_FRONTMATTER_BOUNDARY = "---"


def parse_frontmatter_markdown(text: str) -> tuple[dict[str, Any], str]:
    """Split YAML frontmatter from a Markdown body."""

    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    if not normalized.startswith(f"{_FRONTMATTER_BOUNDARY}\n"):
        return {}, normalized
    lines = normalized.split("\n")
    end_index: int | None = None
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == _FRONTMATTER_BOUNDARY:
            end_index = index
            break
    if end_index is None:
        raise ConfigurationError("SKILL.md frontmatter is missing its closing '---'.")
    raw = "\n".join(lines[1:end_index])
    body = "\n".join(lines[end_index + 1 :])
    return load_frontmatter(raw), body


def load_frontmatter(raw: str) -> dict[str, Any]:
    """Load small YAML frontmatter without making YAML a hard dependency."""

    try:
        yaml = importlib.import_module("yaml")
    except ModuleNotFoundError:
        yaml = None
    if yaml is not None:
        try:
            value = yaml.safe_load(raw)
        except Exception as exc:  # pragma: no cover - exercised when PyYAML is present
            raise ConfigurationError(f"SKILL.md frontmatter is not parseable: {exc}") from exc
        if value is None:
            return {}
        if isinstance(value, Mapping):
            return {str(key): item for key, item in value.items()}
        raise ConfigurationError("SKILL.md frontmatter must be a mapping.")
    return _load_simple_yaml(raw)


def dump_frontmatter(payload: Mapping[str, Any]) -> str:
    """Render a deterministic YAML subset suitable for `SKILL.md`."""

    lines: list[str] = []
    for key in sorted(payload):
        value = payload[key]
        lines.extend(_dump_key_value(str(key), value, indent=0))
    return "\n".join(lines)


def _load_simple_yaml(raw: str) -> dict[str, Any]:
    lines = [line.rstrip() for line in raw.splitlines()]
    result, index = _parse_mapping(lines, 0, 0)
    while index < len(lines):
        if lines[index].strip() and not lines[index].lstrip().startswith("#"):
            raise ConfigurationError(f"Unsupported SKILL.md frontmatter line: {lines[index]!r}.")
        index += 1
    return result


def _parse_mapping(
    lines: list[str],
    start: int,
    indent: int,
) -> tuple[dict[str, Any], int]:
    result: dict[str, Any] = {}
    index = start
    while index < len(lines):
        line = lines[index]
        if not line.strip() or line.lstrip().startswith("#"):
            index += 1
            continue
        current_indent = _indent(line)
        if current_indent < indent:
            break
        if current_indent > indent:
            raise ConfigurationError(f"Unexpected indentation in SKILL.md frontmatter: {line!r}.")
        stripped = line.strip()
        if stripped.startswith("- "):
            break
        key, sep, raw_value = stripped.partition(":")
        if not sep or not key.strip():
            raise ConfigurationError(f"Unsupported SKILL.md frontmatter line: {line!r}.")
        key = key.strip()
        raw_value = raw_value.strip()
        if raw_value:
            result[key] = _parse_scalar(raw_value)
            index += 1
            continue
        value, index = _parse_nested(lines, index + 1, indent + 2)
        result[key] = value
    return result, index


def _parse_nested(lines: list[str], start: int, indent: int) -> tuple[Any, int]:
    index = _skip_blank(lines, start)
    if index >= len(lines) or _indent(lines[index]) < indent:
        return None, index
    stripped = lines[index].strip()
    if stripped.startswith("- "):
        return _parse_list(lines, index, indent)
    return _parse_mapping(lines, index, indent)


def _parse_list(lines: list[str], start: int, indent: int) -> tuple[list[Any], int]:
    values: list[Any] = []
    index = start
    while index < len(lines):
        line = lines[index]
        if not line.strip() or line.lstrip().startswith("#"):
            index += 1
            continue
        current_indent = _indent(line)
        if current_indent < indent:
            break
        if current_indent != indent or not line.strip().startswith("- "):
            break
        item_text = line.strip()[2:].strip()
        index += 1
        if not item_text:
            nested_item, index = _parse_nested(lines, index, indent + 2)
            values.append(nested_item)
            continue
        if _looks_like_mapping_item(item_text):
            key, _, raw_value = item_text.partition(":")
            mapping_item: dict[str, Any] = {key.strip(): _parse_scalar(raw_value.strip())}
            while index < len(lines):
                child = lines[index]
                if not child.strip() or child.lstrip().startswith("#"):
                    index += 1
                    continue
                if _indent(child) < indent + 2:
                    break
                if _indent(child) != indent + 2 or child.strip().startswith("- "):
                    break
                child_key, sep, child_raw = child.strip().partition(":")
                if not sep:
                    raise ConfigurationError(
                        f"Unsupported SKILL.md frontmatter line: {child!r}."
                    )
                mapping_item[child_key.strip()] = _parse_scalar(child_raw.strip())
                index += 1
            values.append(mapping_item)
            continue
        values.append(_parse_scalar(item_text))
    return values, index


def _parse_scalar(value: str) -> Any:
    text = value.strip()
    if text == "":
        return ""
    if text.startswith("#"):
        return None
    if text in {"null", "Null", "NULL", "~"}:
        return None
    if text in {"true", "True", "TRUE"}:
        return True
    if text in {"false", "False", "FALSE"}:
        return False
    if (text.startswith('"') and text.endswith('"')) or (
        text.startswith("'") and text.endswith("'")
    ):
        try:
            return json.loads(text) if text.startswith('"') else text[1:-1]
        except json.JSONDecodeError:
            return text[1:-1]
    if text.startswith("[") and text.endswith("]"):
        inner = text[1:-1].strip()
        if not inner:
            return []
        return [_parse_scalar(part) for part in _split_inline(inner)]
    if text.startswith("{") and text.endswith("}"):
        inner = text[1:-1].strip()
        if not inner:
            return {}
        mapping: dict[str, Any] = {}
        for part in _split_inline(inner):
            key, sep, raw_value = part.partition(":")
            if not sep:
                raise ConfigurationError(f"Unsupported inline mapping item: {part!r}.")
            mapping[_unquote(key.strip())] = _parse_scalar(raw_value.strip())
        return mapping
    if re.fullmatch(r"-?\d+", text):
        try:
            return int(text)
        except ValueError:
            pass
    if re.fullmatch(r"-?\d+\.\d+", text):
        try:
            return float(text)
        except ValueError:
            pass
    return text


def _split_inline(text: str) -> list[str]:
    parts: list[str] = []
    depth = 0
    quote: str | None = None
    start = 0
    for index, char in enumerate(text):
        if quote is not None:
            if char == quote and (index == 0 or text[index - 1] != "\\"):
                quote = None
            continue
        if char in {"'", '"'}:
            quote = char
            continue
        if char in "[{":
            depth += 1
            continue
        if char in "]}":
            depth -= 1
            continue
        if char == "," and depth == 0:
            parts.append(text[start:index].strip())
            start = index + 1
    parts.append(text[start:].strip())
    return [part for part in parts if part]


def _dump_key_value(key: str, value: Any, *, indent: int) -> list[str]:
    prefix = " " * indent
    if isinstance(value, Mapping):
        if not value:
            return [f"{prefix}{key}: {{}}"]
        lines = [f"{prefix}{key}:"]
        for child_key in sorted(value):
            lines.extend(_dump_key_value(str(child_key), value[child_key], indent=indent + 2))
        return lines
    if isinstance(value, (list, tuple)):
        if not value:
            return [f"{prefix}{key}: []"]
        lines = [f"{prefix}{key}:"]
        for item in value:
            lines.extend(_dump_list_item(item, indent=indent + 2))
        return lines
    return [f"{prefix}{key}: {_dump_scalar(value)}"]


def _dump_list_item(value: Any, *, indent: int) -> list[str]:
    prefix = " " * indent
    if isinstance(value, Mapping):
        if not value:
            return [f"{prefix}- {{}}"]
        lines: list[str] = []
        for index, key in enumerate(sorted(value)):
            rendered = _dump_key_value(str(key), value[key], indent=indent + 2)
            if index == 0 and len(rendered) == 1:
                lines.append(f"{prefix}- {rendered[0].strip()}")
            else:
                if index == 0:
                    lines.append(f"{prefix}-")
                lines.extend(rendered)
        return lines
    if isinstance(value, (list, tuple)):
        lines = [f"{prefix}-"]
        for item in value:
            lines.extend(_dump_list_item(item, indent=indent + 2))
        return lines
    return [f"{prefix}- {_dump_scalar(value)}"]


def _dump_scalar(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return str(value)
    text = str(value)
    if not text:
        return '""'
    if re.fullmatch(r"[A-Za-z0-9_./:@+-]+", text) and text not in {
        "true",
        "false",
        "null",
        "~",
    }:
        return text
    return json.dumps(text)


def _unquote(value: str) -> str:
    if (value.startswith('"') and value.endswith('"')) or (
        value.startswith("'") and value.endswith("'")
    ):
        return str(_parse_scalar(value))
    return value


def _skip_blank(lines: list[str], index: int) -> int:
    while index < len(lines) and (
        not lines[index].strip() or lines[index].lstrip().startswith("#")
    ):
        index += 1
    return index


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _looks_like_mapping_item(text: str) -> bool:
    key, sep, _ = text.partition(":")
    return bool(sep and key.strip() and not key.strip().startswith(("[", "{", "'", '"')))
