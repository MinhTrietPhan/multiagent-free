"""Deterministic research policy: when to keep going and when to stop.

The LLM only plans the queries (see ``planner.py``); every loop decision here is
plain Python with hard budgets, so research can never run away and the choice is
reproducible in tests.
"""
from __future__ import annotations

import os
import time

from ..processors.deduplicate import independent_source_count, same_story
from ..state import Article, ResearchState

MIN_CONTENT_CHARS = 200
VERIFY_MIN_CHARS = 800


def deadline_passed(state: ResearchState) -> bool:
    return bool(state.deadline_at) and time.time() >= state.deadline_at


def usable_articles(state: ResearchState) -> list[Article]:
    return [article for article in state.articles
            if len((article.content or "").strip()) >= MIN_CONTENT_CHARS]


def cluster_stories(articles: list[Article]) -> list[list[Article]]:
    """Group articles that describe the same story, best-scored story first."""
    clusters: list[list[Article]] = []
    for article in sorted(articles, key=lambda item: item.final_score, reverse=True):
        for cluster in clusters:
            if same_story(article.title, cluster[0].title):
                cluster.append(article)
                break
        else:
            clusters.append([article])
    return clusters


def best_story(state: ResearchState) -> list[Article]:
    clusters = cluster_stories(usable_articles(state))
    return clusters[0] if clusters else []


def min_items(state: ResearchState) -> int:
    value = getattr(state, "min_items", None)
    if value:
        return max(1, int(value))
    try:
        return max(1, int(os.getenv("HUB_RESEARCH_MIN_ITEMS", "5")))
    except ValueError:
        return 5


def evidence_sufficient(state: ResearchState) -> tuple[bool, str]:
    """Intent-aware stop rule: a digest needs breadth, a verify needs independence."""
    usable = usable_articles(state)
    if not usable:
        return False, ""
    if state.intent == "verify":
        story = best_story(state)
        sources = independent_source_count(story)
        if sources >= 2 and sum(len(a.content) for a in story) >= VERIFY_MIN_CHARS:
            return True, f"đủ {sources} nguồn độc lập cho câu chuyện trọng tâm"
        return False, ""

    stories = cluster_stories(usable)
    domains = {article.source for article in usable if article.source}
    needed = min_items(state)
    if len(stories) >= needed and len(domains) >= 2:
        return True, f"đủ {len(stories)} câu chuyện khác nhau từ {len(domains)} domain"
    return False, ""


def stop_reason(state: ResearchState) -> str:
    """First applicable hard stop; empty string when research should continue."""
    if deadline_passed(state):
        return f"hết thời gian {int(os.getenv('HUB_RESEARCH_DEADLINE_SECONDS', '90'))}s"
    if state.fetched_count >= state.max_pages:
        return f"đã đọc đủ {state.max_pages} trang"
    if state.search_rounds >= state.max_search_rounds:
        return f"đã dùng hết {state.max_search_rounds} vòng tìm kiếm"
    return ""


def next_action(state: ResearchState) -> str:
    """FETCH_NEXT (queued URLs), SEARCH_MORE (new round) or STOP."""
    if state.fetched_count >= state.max_pages or deadline_passed(state):
        return "STOP"
    if state.pending_urls:
        return "FETCH_NEXT"
    if state.search_rounds < state.max_search_rounds:
        return "SEARCH_MORE"
    return "STOP"


def finalize_evidence(state: ResearchState, reason: str = "") -> None:
    """Record the evidence level so the report can state it explicitly."""
    usable = usable_articles(state)
    if not usable:
        state.evidence_level, state.evidence_score = "insufficient", 0.0
    else:
        stories = cluster_stories(usable)
        corroborated = sum(
            1 for cluster in stories[:5] if independent_source_count(cluster) >= 2
        )
        state.evidence_score = round(corroborated / max(1, min(5, len(stories))), 2)
        if state.intent == "verify":
            sources = independent_source_count(best_story(state))
            state.evidence_level = "corroborated" if sources >= 2 else "single_source"
        else:
            # Digest quality is breadth of corroborated stories, not the top one.
            state.evidence_level = "corroborated" if corroborated >= 2 else "single_source"
    state.stop_reason = reason or state.stop_reason
