from __future__ import annotations

import base64
import mimetypes
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Literal

MediaSource = Literal["inline_base64", "bytes", "url", "artifact", "provider_file"]
MediaSensitivity = Literal["public", "sensitive", "secret"]


@dataclass(slots=True, frozen=True)
class MediaRef:
    """Reference to media carried by a runtime event or content part.

    Inline media is redacted by default during persistence unless callers opt in
    and ``storage_allowed`` is true. This lets live audio/video chunks flow
    through events without silently turning the event store into a media archive.
    """

    source: MediaSource
    mime_type: str
    data_base64: str | None = None
    url: str | None = None
    artifact_id: str | None = None
    provider_file_id: str | None = None
    bytes_count: int | None = None
    storage_allowed: bool = False
    sensitivity: MediaSensitivity = "sensitive"
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_bytes(
        cls,
        data: bytes,
        *,
        mime_type: str,
        storage_allowed: bool = False,
        sensitivity: MediaSensitivity = "sensitive",
        metadata: dict[str, Any] | None = None,
    ) -> MediaRef:
        return cls(
            source="inline_base64",
            mime_type=mime_type,
            data_base64=base64.b64encode(data).decode("ascii"),
            bytes_count=len(data),
            storage_allowed=storage_allowed,
            sensitivity=sensitivity,
            metadata=metadata or {},
        )

    @classmethod
    def from_base64(
        cls,
        data_base64: str,
        *,
        mime_type: str,
        bytes_count: int | None = None,
        storage_allowed: bool = False,
        sensitivity: MediaSensitivity = "sensitive",
        metadata: dict[str, Any] | None = None,
    ) -> MediaRef:
        return cls(
            source="inline_base64",
            mime_type=mime_type,
            data_base64=data_base64,
            bytes_count=bytes_count or _base64_size(data_base64),
            storage_allowed=storage_allowed,
            sensitivity=sensitivity,
            metadata=metadata or {},
        )

    @classmethod
    def from_file(
        cls,
        path: str | Path,
        *,
        mime_type: str | None = None,
        storage_allowed: bool = False,
        sensitivity: MediaSensitivity = "sensitive",
        metadata: dict[str, Any] | None = None,
    ) -> MediaRef:
        file_path = Path(path)
        data = file_path.read_bytes()
        guessed_mime_type = mime_type or mimetypes.guess_type(file_path.name)[0]
        return cls.from_bytes(
            data,
            mime_type=guessed_mime_type or "application/octet-stream",
            storage_allowed=storage_allowed,
            sensitivity=sensitivity,
            metadata={"filename": file_path.name, **(metadata or {})},
        )

    @classmethod
    def from_url(
        cls,
        url: str,
        *,
        mime_type: str,
        storage_allowed: bool = True,
        sensitivity: MediaSensitivity = "public",
        metadata: dict[str, Any] | None = None,
    ) -> MediaRef:
        return cls(
            source="url",
            mime_type=mime_type,
            url=url,
            storage_allowed=storage_allowed,
            sensitivity=sensitivity,
            metadata=metadata or {},
        )

    @classmethod
    def from_artifact(
        cls,
        artifact_id: str,
        *,
        mime_type: str,
        storage_allowed: bool = True,
        sensitivity: MediaSensitivity = "sensitive",
        metadata: dict[str, Any] | None = None,
    ) -> MediaRef:
        return cls(
            source="artifact",
            mime_type=mime_type,
            artifact_id=artifact_id,
            storage_allowed=storage_allowed,
            sensitivity=sensitivity,
            metadata=metadata or {},
        )

    def redacted(self) -> MediaRef:
        metadata = dict(self.metadata)
        metadata["redacted"] = True
        return replace(self, data_base64=None, metadata=metadata)


def _base64_size(value: str) -> int | None:
    try:
        return len(base64.b64decode(value, validate=True))
    except Exception:
        return None
