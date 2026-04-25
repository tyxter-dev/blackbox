from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4


@dataclass(slots=True, frozen=True)
class ArtifactRef:
    id: str
    provider: str | None = None
    uri: str | None = None


@dataclass(slots=True, frozen=True)
class Artifact:
    type: str
    name: str
    data: Any | None = None
    uri: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: f"art_{uuid4().hex}")

    @property
    def ref(self) -> ArtifactRef:
        return ArtifactRef(id=self.id, uri=self.uri)
