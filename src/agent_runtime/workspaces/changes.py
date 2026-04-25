from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ChangeType = Literal["create", "update", "delete", "rename"]


@dataclass(slots=True, frozen=True)
class FileChange:
    path: str
    type: ChangeType
    content: str | None = None
    old_path: str | None = None


@dataclass(slots=True, frozen=True)
class Patch:
    summary: str
    diff: str
    changes: list[FileChange]
