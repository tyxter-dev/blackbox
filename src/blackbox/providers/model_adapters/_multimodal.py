"""Map runtime content parts into provider-native multimodal input payloads.

``ContentItem`` entries passed through ``TurnRequest.input`` are converted to
each provider's native message/content shape here. Parts a provider cannot
express raise ``UnsupportedFeatureError`` instead of being dropped, matching
the adapter convention for explicit unsupported requests.

Resolution order for media references is the same everywhere:
``provider_file_id`` (already uploaded), then ``url``, then inline base64
data. Artifact-only references must be resolved by the application first.
"""
from __future__ import annotations

from typing import Any

from blackbox.core.content import (
    AudioPart,
    ContentItem,
    FilePart,
    ImagePart,
    ProviderNativePart,
    TextPart,
)
from blackbox.core.errors import UnsupportedFeatureError
from blackbox.core.media import MediaRef


def _resolved_media(media: MediaRef | None, *, provider: str, part_kind: str) -> MediaRef:
    if media is None:
        raise UnsupportedFeatureError(
            f"{provider}: {part_kind} content part has no MediaRef to send."
        )
    if media.provider_file_id is None and media.url is None and media.data_base64 is None:
        raise UnsupportedFeatureError(
            f"{provider}: {part_kind} media is only an artifact reference "
            f"(artifact_id={media.artifact_id!r}); resolve it to bytes or a URL "
            "before sending it as model input."
        )
    return media


def _data_uri(media: MediaRef) -> str:
    return f"data:{media.mime_type};base64,{media.data_base64}"


def _unsupported_part(provider: str, part: Any) -> UnsupportedFeatureError:
    kind = getattr(part, "type", type(part).__name__)
    return UnsupportedFeatureError(
        f"{provider} model input does not support {kind!r} content parts; "
        "use ProviderNativePart as an explicit escape hatch."
    )


def content_item_to_openai_responses(item: ContentItem) -> dict[str, Any]:
    """Convert a ContentItem into an OpenAI Responses input message."""

    provider = "OpenAI Responses"
    if item.role not in {"user", "system", "assistant"}:
        raise UnsupportedFeatureError(
            f"{provider}: content items with role {item.role!r} are not supported "
            "as model input; tool results travel as run items."
        )
    text_type = "output_text" if item.role == "assistant" else "input_text"
    content: list[Any] = []
    for part in item.parts:
        if isinstance(part, TextPart):
            content.append({"type": text_type, "text": part.text})
        elif isinstance(part, ImagePart) and item.role == "user":
            media = _resolved_media(part.media, provider=provider, part_kind="image")
            payload: dict[str, Any] = {"type": "input_image", "detail": part.detail}
            if media.provider_file_id is not None:
                payload["file_id"] = media.provider_file_id
            elif media.url is not None:
                payload["image_url"] = media.url
            else:
                payload["image_url"] = _data_uri(media)
            content.append(payload)
        elif isinstance(part, FilePart) and item.role == "user":
            media = _resolved_media(part.media, provider=provider, part_kind="file")
            payload = {"type": "input_file"}
            if media.provider_file_id is not None:
                payload["file_id"] = media.provider_file_id
            elif media.url is not None:
                payload["file_url"] = media.url
            else:
                payload["filename"] = (
                    part.filename or media.metadata.get("filename") or "attachment"
                )
                payload["file_data"] = _data_uri(media)
            content.append(payload)
        elif isinstance(part, ProviderNativePart):
            content.append(part.value)
        else:
            raise _unsupported_part(provider, part)
    return {"role": item.role, "content": content}


def content_item_to_anthropic(item: ContentItem) -> dict[str, Any]:
    """Convert a ContentItem into an Anthropic Messages turn."""

    provider = "Anthropic Messages"
    if item.role not in {"user", "assistant"}:
        raise UnsupportedFeatureError(
            f"{provider}: content items with role {item.role!r} are not supported; "
            "system text belongs in instructions and tool results travel as run items."
        )
    content: list[Any] = []
    for part in item.parts:
        if isinstance(part, TextPart):
            content.append({"type": "text", "text": part.text})
        elif isinstance(part, ImagePart) and item.role == "user":
            media = _resolved_media(part.media, provider=provider, part_kind="image")
            content.append({"type": "image", "source": _anthropic_source(media)})
        elif isinstance(part, FilePart) and item.role == "user":
            media = _resolved_media(part.media, provider=provider, part_kind="file")
            content.append({"type": "document", "source": _anthropic_source(media)})
        elif isinstance(part, ProviderNativePart):
            content.append(part.value)
        else:
            raise _unsupported_part(provider, part)
    return {"role": item.role, "content": content}


def _anthropic_source(media: MediaRef) -> dict[str, Any]:
    if media.provider_file_id is not None:
        return {"type": "file", "file_id": media.provider_file_id}
    if media.url is not None:
        return {"type": "url", "url": media.url}
    return {"type": "base64", "media_type": media.mime_type, "data": media.data_base64}


def content_item_to_gemini(item: ContentItem) -> dict[str, Any]:
    """Convert a ContentItem into a Gemini GenerateContent content entry."""

    provider = "Gemini GenerateContent"
    if item.role not in {"user", "assistant"}:
        raise UnsupportedFeatureError(
            f"{provider}: content items with role {item.role!r} are not supported; "
            "system text belongs in instructions and tool results travel as run items."
        )
    role = "model" if item.role == "assistant" else "user"
    parts: list[Any] = []
    for part in item.parts:
        if isinstance(part, TextPart):
            parts.append({"text": part.text})
        elif isinstance(part, ImagePart | FilePart | AudioPart) and item.role == "user":
            kind = str(getattr(part, "type", "media"))
            media = _resolved_media(part.media, provider=provider, part_kind=kind)
            parts.append(_gemini_media_part(media))
        elif isinstance(part, ProviderNativePart):
            parts.append(part.value)
        else:
            raise _unsupported_part(provider, part)
    return {"role": role, "parts": parts}


def _gemini_media_part(media: MediaRef) -> dict[str, Any]:
    file_uri = media.provider_file_id or media.url
    if file_uri is not None:
        return {"file_data": {"file_uri": file_uri, "mime_type": media.mime_type}}
    return {"inline_data": {"mime_type": media.mime_type, "data": media.data_base64}}
