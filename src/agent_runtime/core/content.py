from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from agent_runtime.core.media import MediaRef
from agent_runtime.core.media import MediaSource as MediaSource

Role = Literal["user", "assistant", "system", "tool"]
ContentModality = Literal["text", "audio", "image", "video", "file", "tool_result", "raw"]


@dataclass(slots=True, frozen=True)
class TextPart:
    type: Literal["text"] = "text"
    text: str = ""


@dataclass(slots=True, frozen=True)
class AudioPart:
    type: Literal["audio"] = "audio"
    media: MediaRef | None = None
    transcript: str | None = None
    format: str | None = None
    sample_rate_hz: int | None = None
    channels: int | None = None
    duration_ms: int | None = None

    @classmethod
    def from_bytes(
        cls,
        data: bytes,
        *,
        mime_type: str = "audio/pcm",
        format: str | None = None,
        sample_rate_hz: int | None = None,
        channels: int | None = None,
        duration_ms: int | None = None,
        storage_allowed: bool = False,
    ) -> AudioPart:
        return cls(
            media=MediaRef.from_bytes(
                data,
                mime_type=mime_type,
                storage_allowed=storage_allowed,
            ),
            format=format,
            sample_rate_hz=sample_rate_hz,
            channels=channels,
            duration_ms=duration_ms,
        )


@dataclass(slots=True, frozen=True)
class ImagePart:
    type: Literal["image"] = "image"
    media: MediaRef | None = None
    detail: Literal["auto", "low", "high"] = "auto"

    @classmethod
    def from_file(
        cls,
        path: str | Path,
        *,
        mime_type: str | None = None,
        detail: Literal["auto", "low", "high"] = "auto",
        storage_allowed: bool = False,
    ) -> ImagePart:
        return cls(
            media=MediaRef.from_file(
                path,
                mime_type=mime_type,
                storage_allowed=storage_allowed,
            ),
            detail=detail,
        )

    @classmethod
    def from_url(
        cls,
        url: str,
        *,
        mime_type: str = "image/*",
        detail: Literal["auto", "low", "high"] = "auto",
    ) -> ImagePart:
        return cls(media=MediaRef.from_url(url, mime_type=mime_type), detail=detail)


@dataclass(slots=True, frozen=True)
class VideoFramePart:
    type: Literal["video_frame"] = "video_frame"
    media: MediaRef | None = None
    timestamp_ms: int | None = None


@dataclass(slots=True, frozen=True)
class FilePart:
    type: Literal["file"] = "file"
    media: MediaRef | None = None
    filename: str | None = None


@dataclass(slots=True, frozen=True)
class ToolResultPart:
    call_id: str
    content: str
    type: Literal["tool_result"] = "tool_result"
    payload: Any | None = None


@dataclass(slots=True, frozen=True)
class ProviderNativePart:
    provider: str
    value: Any
    type: Literal["raw"] = "raw"
    metadata: dict[str, Any] = field(default_factory=dict)


ContentPart = (
    TextPart
    | AudioPart
    | ImagePart
    | VideoFramePart
    | FilePart
    | ToolResultPart
    | ProviderNativePart
)


@dataclass(slots=True, frozen=True)
class ContentItem:
    role: Role
    parts: list[ContentPart]
    item_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
