from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any

from .controller.planner import ResearchPlan


@dataclass
class Article:
    url: str
    title: str
    snippet: str = ""
    source: str = ""
    published_at: str | None = None
    date_source: str = "unknown"          # search | metadata | unknown
    content: str = ""
    fingerprint: str | None = None        # content hash, used to detect syndicated copies
    corroborating_sources: list[str] = field(default_factory=list)
    heuristic_score: float = 0.0
    relevance_score: float = 0.0
    final_score: float = 0.0
    fetch_error: str | None = None


@dataclass
class ResearchState:
    query: str
    plan: ResearchPlan | None = None
    intent: str = "digest"
    search_rounds: int = 0
    fetched_count: int = 0
    max_search_rounds: int = 3
    max_pages: int = 12
    pages_per_round: int = 4
    min_items: int = 5
    deadline_at: float = 0.0
    pending_urls: list[dict[str, str]] = field(default_factory=list)
    seen_urls: set[str] = field(default_factory=set)
    queries_tried: list[str] = field(default_factory=list)
    all_search_results: list[dict[str, str]] = field(default_factory=list)
    search_evidence: list[dict[str, str]] = field(default_factory=list)
    articles: list[Article] = field(default_factory=list)
    skipped: list[dict[str, str]] = field(default_factory=list)
    actions: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    evidence_level: str = "insufficient"
    evidence_score: float = 0.0
    stop_reason: str = ""

    def compact(self) -> dict[str, Any]:
        """Small, log-friendly summary (no article bodies)."""
        return {
            "query": self.query[:300],
            "intent": self.intent,
            "search_rounds": self.search_rounds,
            "fetched_count": self.fetched_count,
            "pending_count": len(self.pending_urls),
            "article_count": len(self.articles),
            "max_search_rounds": self.max_search_rounds,
            "max_pages": self.max_pages,
            "evidence_level": self.evidence_level,
            "stop_reason": self.stop_reason,
            "top_candidates": [
                {"title": a.title[:100], "source": a.source, "published_at": a.published_at}
                for a in sorted(self.articles, key=lambda item: item.final_score, reverse=True)[:5]
            ],
        }
