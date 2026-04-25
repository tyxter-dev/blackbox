from __future__ import annotations

from dataclasses import dataclass, field

from agent_runtime.tools.registry import ToolDefinition


@dataclass(slots=True, frozen=True)
class ToolCatalogEntry:
    name: str
    description: str
    category: str | None = None
    tags: list[str] = field(default_factory=list)

    def relevance_score(self, query: str) -> float:
        if not query.strip():
            return 0.0
        q = query.lower()
        score = 0.0
        if q == self.name.lower():
            return 1.0
        if q in self.name.lower():
            score += 0.5
        if q in self.description.lower():
            score += 0.25
        if self.category and q in self.category.lower():
            score += 0.15
        if any(q in tag.lower() for tag in self.tags):
            score += 0.25
        return min(score, 1.0)


class ToolCatalog:
    def __init__(self, tools: list[ToolDefinition]) -> None:
        self.entries = [
            ToolCatalogEntry(
                name=tool.name,
                description=tool.description,
                category=tool.category,
                tags=tool.tags,
            )
            for tool in tools
        ]

    def search(self, query: str, *, limit: int = 10) -> list[ToolCatalogEntry]:
        scored = [(entry.relevance_score(query), entry) for entry in self.entries]
        scored.sort(key=lambda item: item[0], reverse=True)
        return [entry for score, entry in scored[:limit] if score > 0 or not query.strip()]
