from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Article:
    url: str
    title: str
    snippet: str = ""
    source: str = ""
    published_at: str | None = None
    content: str = ""
    corroborating_sources: list[str] = field(default_factory=list)
    laya_score: float | None = None
    heuristic_score: float = 0.0
    relevance_score: float = 0.0
    final_score: float = 0.0
    fetch_error: str | None = None


@dataclass
class ResearchState:
    query: str
    query_intent: str = "general"
    search_rounds: int = 0
    fetched_count: int = 0
    max_search_rounds: int = 3
    max_pages: int = 12
    max_iterations: int = 20
    pending_urls: list[dict[str, str]] = field(default_factory=list)
    all_search_results: list[dict[str, str]] = field(default_factory=list)
    search_evidence: list[dict[str, str]] = field(default_factory=list)
    articles: list[Article] = field(default_factory=list)
    actions: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    evidence_level: str = "insufficient"
    evidence_score: float = 0.0

    def compact(self) -> dict[str, Any]:
        return {
            "query": self.query[:300],
            "query_intent": self.query_intent,
            "search_rounds": self.search_rounds,
            "fetched_count": self.fetched_count,
            "pending_count": len(self.pending_urls),
            "article_count": len(self.articles),
            "max_search_rounds": self.max_search_rounds,
            "max_pages": self.max_pages,
            "top_candidates": [
                {"title": a.title[:100], "source": a.source, "published_at": a.published_at}
                for a in self.articles[:5]
            ],
        }

