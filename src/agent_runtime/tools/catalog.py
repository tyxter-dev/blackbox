from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from agent_runtime.tools.registry import ToolDefinition

_CHARS_PER_TOKEN = 4.0


def estimate_token_count(definition: dict[str, Any]) -> int:
    """Estimate token cost for a provider-neutral tool definition."""
    raw = json.dumps(definition, separators=(",", ":"))
    return max(1, int(len(raw) / _CHARS_PER_TOKEN + 0.5))


@dataclass(slots=True)
class ToolCatalogEntry:
    name: str
    description: str
    parameters: dict[str, Any] | None = None
    category: str | None = None
    tags: list[str] = field(default_factory=list)
    risk: str | None = None
    scopes: list[str] = field(default_factory=list)
    latency: str | None = None
    cost: str | None = None
    side_effects: list[str] = field(default_factory=list)
    examples: list[str] = field(default_factory=list)
    negative_examples: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    token_count: int = 0

    def matches_query(self, query: str) -> bool:
        tokens = query.lower().split()
        if not tokens:
            return False
        searchable = " ".join(
            [
                self.name,
                self.description,
                self.category or "",
                " ".join(self.tags),
                " ".join(self.scopes),
                " ".join(self.examples),
                " ".join(self.negative_examples),
            ]
        ).lower()
        searchable_words = set(searchable.replace("_", " ").replace("-", " ").split())
        matched = 0
        for token in tokens:
            if token in searchable:
                matched += 1
                continue
            if any(word in token for word in searchable_words if len(word) >= 3):
                matched += 1
        required = max(1, -(-len(tokens) // 2))
        return matched >= required

    def relevance_score(self, query: str) -> float:
        if not query.strip():
            return 0.0
        query_lower = query.lower().strip()
        if query_lower == self.name.lower():
            return 1.0

        tokens = query_lower.split()
        name_lower = self.name.lower()
        desc_lower = self.description.lower()
        category_lower = (self.category or "").lower()
        tags_lower = [tag.lower() for tag in self.tags]
        scopes_lower = [scope.lower() for scope in self.scopes]
        examples_lower = " ".join([*self.examples, *self.negative_examples]).lower()
        name_words = set(name_lower.replace("_", " ").replace("-", " ").split())

        total = 0.0
        for token in tokens:
            if token in name_lower:
                total += 3.0 * (len(token) / max(len(name_lower), 1))
            elif any(token in word or word in token for word in name_words if len(word) >= 3):
                total += 0.9
            if any(token == tag or token in tag or tag in token for tag in tags_lower):
                total += 2.0
            if any(token == scope or token in scope or scope in token for scope in scopes_lower):
                total += 1.5
            if token in desc_lower:
                total += 1.0 * (len(token) / max(len(desc_lower), 1))
            if category_lower and token in category_lower:
                total += 1.0
            if token in examples_lower:
                total += 0.5

        return min(1.0, total / max(len(tokens) * 9.0, 1.0))

    def to_summary(self, *, loaded: bool = False) -> dict[str, Any]:
        summary: dict[str, Any] = {
            "name": self.name,
            "description": self.description,
            "category": self.category,
            "tags": list(self.tags),
            "loaded": loaded,
        }
        if self.risk is not None:
            summary["risk"] = self.risk
        if self.scopes:
            summary["scopes"] = list(self.scopes)
        if self.latency is not None:
            summary["latency"] = self.latency
        if self.cost is not None:
            summary["cost"] = self.cost
        if self.side_effects:
            summary["side_effects"] = list(self.side_effects)
        if self.examples:
            summary["examples"] = list(self.examples)
        if self.negative_examples:
            summary["negative_examples"] = list(self.negative_examples)
        if self.token_count > 0:
            summary["estimated_tokens"] = self.token_count
        return summary


class ToolCatalog:
    def __init__(self, tools: list[ToolDefinition]) -> None:
        self.entries = [
            ToolCatalogEntry(
                name=tool.name,
                description=tool.description,
                parameters=tool.parameters,
                category=tool.category,
                tags=list(tool.tags),
                risk=tool.risk,
                scopes=list(tool.scopes),
                latency=tool.latency,
                cost=tool.cost,
                side_effects=list(tool.side_effects),
                examples=list(tool.examples),
                negative_examples=list(tool.negative_examples),
                metadata=dict(tool.metadata),
                token_count=estimate_token_count(_provider_tool_payload(tool)),
            )
            for tool in tools
        ]
        self._entries_by_name = {entry.name: entry for entry in self.entries}
        self.last_search_total = 0

    def search(
        self,
        query: str | None = None,
        *,
        category: str | None = None,
        tags: list[str] | None = None,
        scopes: list[str] | None = None,
        risk: str | None = None,
        limit: int = 10,
        offset: int = 0,
        min_score: float = 0.0,
    ) -> list[ToolCatalogEntry]:
        query_value = query or ""
        tag_set = {tag.lower() for tag in tags or []}
        scope_set = {scope.lower() for scope in scopes or []}
        results: list[ToolCatalogEntry] = []

        for entry in self.entries:
            if category and (entry.category or "").lower() != category.lower():
                continue
            if risk and (entry.risk or "").lower() != risk.lower():
                continue
            if tag_set and not tag_set.intersection(tag.lower() for tag in entry.tags):
                continue
            if scope_set and not scope_set.issubset({scope.lower() for scope in entry.scopes}):
                continue
            if query_value and not entry.matches_query(query_value):
                continue
            results.append(entry)

        if query_value:
            scored = [(entry.relevance_score(query_value), entry) for entry in results]
            scored = [(score, entry) for score, entry in scored if score >= min_score]
            scored.sort(key=lambda item: item[0], reverse=True)
            results = [entry for _, entry in scored]
        else:
            results.sort(key=lambda entry: entry.name)

        self.last_search_total = len(results)
        return results[offset : offset + limit]

    def get_entry(self, name: str) -> ToolCatalogEntry | None:
        return self._entries_by_name.get(name)

    def has_entry(self, name: str) -> bool:
        return name in self._entries_by_name

    def list_all(self) -> list[ToolCatalogEntry]:
        return list(self.entries)

    def list_categories(self) -> list[str]:
        return sorted({entry.category for entry in self.entries if entry.category})

    def get_token_count(self, name: str) -> int:
        entry = self.get_entry(name)
        return entry.token_count if entry is not None else 0


def _provider_tool_payload(tool: ToolDefinition) -> dict[str, Any]:
    return {
        "type": "function",
        "name": tool.name,
        "description": tool.description,
        "parameters": tool.parameters,
        "metadata": dict(tool.metadata),
    }
