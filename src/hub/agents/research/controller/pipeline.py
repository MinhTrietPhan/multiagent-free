"""Research pipeline: LLM plan → search → select → fetch → rank → stop → summarize.

The LLM is used exactly twice per run (plan and summary). Every loop decision
between them is deterministic and bounded by explicit budgets, so a bad plan or
a flaky network can only make research narrower — never endless or expensive.
"""
from __future__ import annotations

import os
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

from ..collectors.fetch import fetch_article_meta
from ..collectors.search import WebSearchClient
from ..processors.deduplicate import (
    canonical_url,
    content_fingerprint,
    deduplicate,
    same_story,
)
from ..processors.synthesize import ArticleSummarizer, SummaryItem
from ..ranking.engine import query_terms, rank_articles, rank_search_rows
from ..reporting.formatter import format_report
from ..state import Article, ResearchState
from .planner import ResearchPlan, ResearchPlanner
from .policy import (
    best_story,
    deadline_passed,
    evidence_sufficient,
    finalize_evidence,
)

MIN_CONTENT_CHARS = 200


def _int_env(name: str, default: int, low: int, high: int) -> int:
    try:
        return max(low, min(high, int(os.getenv(name, str(default)))))
    except (TypeError, ValueError):
        return default


def _host(url: str) -> str:
    return (urlparse(url).hostname or "").lower().removeprefix("www.")


class ResearchPipeline:
    def __init__(self, searcher=None, fetcher=None, planner=None, summarizer=None):
        self.searcher = searcher or WebSearchClient(
            max_results=_int_env("HUB_RESEARCH_RESULTS_PER_QUERY", 12, 3, 30))
        self.fetcher = fetcher or fetch_article_meta
        self.planner = planner or ResearchPlanner()
        self.summarizer = summarizer or ArticleSummarizer()
        self.max_rounds = _int_env("HUB_RESEARCH_MAX_SEARCH_ROUNDS", 3, 1, 6)
        self.max_pages = _int_env("HUB_RESEARCH_MAX_PAGES", 12, 1, 40)
        self.pages_per_round = _int_env("HUB_RESEARCH_PAGES_PER_ROUND", 4, 1, 12)
        self.max_per_domain = _int_env("HUB_RESEARCH_MAX_PER_DOMAIN", 2, 1, 6)
        self.content_chars = _int_env("HUB_RESEARCH_CONTENT_CHARS", 12000, 1000, 40000)
        self.workers = _int_env("HUB_RESEARCH_FETCH_WORKERS", 4, 1, 8)
        self.deadline_seconds = _int_env("HUB_RESEARCH_DEADLINE_SECONDS", 90, 1, 600)
        self.min_items = _int_env("HUB_RESEARCH_MIN_ITEMS", 5, 1, 10)

    # -- search -----------------------------------------------------------
    @staticmethod
    def _queue_rows(state: ResearchState, rows: list[dict]) -> list[dict]:
        """Queue candidates by URL only.

        Cross-publisher copies of one story are kept here: ``verify`` needs them
        to reach a second independent source, and selection decides which story
        to fetch (not the search backend).
        """
        queued: list[dict] = []
        seen = set(state.seen_urls)
        for row in state.pending_urls + rows:
            url = canonical_url(row.get("url", ""))
            if not url.startswith(("http://", "https://")) or url in seen:
                continue
            seen.add(url)
            queued.append({**row, "url": url})
        return queued

    def _search(self, state: ResearchState, query: str) -> list[dict]:
        state.search_rounds += 1
        state.queries_tried.append(query)
        try:
            rows = list(self.searcher.search(query) or [])
        except Exception as exc:
            state.warnings.append(f"Tìm kiếm lỗi: {type(exc).__name__}: {exc}")
            state.actions.append(f"SEARCH[{state.search_rounds}]: lỗi")
            return []
        state.search_evidence.extend(rows)
        state.all_search_results = deduplicate(state.all_search_results + rows)
        state.pending_urls = self._queue_rows(state, rows)
        for error in getattr(self.searcher, "errors", None) or []:
            if error not in state.warnings:
                state.warnings.append(error)
        state.actions.append(
            f"SEARCH[{state.search_rounds}]: {len(rows)} kết quả cho “{query[:70]}”")
        return rows

    def _next_query(self, state: ResearchState, plan: ResearchPlan) -> str:
        tried = {item.casefold() for item in state.queries_tried}
        if state.search_rounds < len(plan.queries):
            candidate = plan.queries[state.search_rounds]
            if candidate.casefold() not in tried:
                return candidate
        if state.intent == "verify":
            story = best_story(state)
            if story:
                candidate = f'"{story[0].title[:140]}"'
                if candidate.casefold() not in tried:
                    return candidate
        return ""

    # -- selection --------------------------------------------------------
    def _select_urls(self, state: ResearchState, terms: list[str],
                     preferred_domains: list[str] | None) -> list[dict]:
        """Score candidates *before* fetching: best 4 URLs, ≤2 per domain, one per story."""
        budget = state.max_pages - state.fetched_count
        limit = min(state.pages_per_round, budget)
        if limit <= 0:
            return []
        ranked = rank_search_rows(
            [row for row in state.pending_urls if row.get("url") not in state.seen_urls],
            terms, preferred_domains=preferred_domains,
        )
        chosen: list[dict] = []
        per_domain: Counter[str] = Counter()
        fetched_titles = [article.title for article in state.articles]
        for row in ranked:
            url = row.get("url", "")
            host = _host(url)
            if not url.startswith(("http://", "https://")) or not host:
                continue
            if per_domain[host] >= self.max_per_domain:
                continue
            if any(same_story(row.get("title", ""), other.get("title", "")) for other in chosen):
                continue
            if state.intent != "verify" and any(
                same_story(row.get("title", ""), title) for title in fetched_titles
            ):
                continue      # digest wants new stories, not copies of fetched ones
            chosen.append(row)
            per_domain[host] += 1
            if len(chosen) >= limit:
                break
        selected = {row["url"] for row in chosen}
        state.seen_urls.update(selected)
        state.pending_urls = [row for row in state.pending_urls if row.get("url") not in selected]
        return chosen

    # -- fetch ------------------------------------------------------------
    def _call_fetcher(self, url: str) -> tuple[str, str, str | None]:
        result = self.fetcher(url)
        if isinstance(result, tuple) and len(result) == 3:
            text, final_url, published = result
        else:
            text, final_url = result
            published = None
        return text or "", final_url or url, published

    def _fetch_one(self, row: dict) -> tuple[Article, list[str]]:
        url = row.get("url", "")
        # Google News links redirect; use the publisher URL from RSS as fallback
        # so failed fetches still carry the real domain, not news.google.com.
        domain = _host(row.get("publisher_url") or url)
        article = Article(url=url, title=row.get("title") or url,
                          snippet=row.get("snippet") or "", source=domain,
                          published_at=row.get("published_at"),
                          date_source="search" if row.get("published_at") else "unknown")
        warnings: list[str] = []
        try:
            text, final_url, published = self._call_fetcher(url)
            article.content = text[: self.content_chars]
            article.url = final_url
            article.source = _host(final_url) or domain
            if published:
                article.published_at = published
                article.date_source = "metadata"
            article.fingerprint = content_fingerprint(article.content)
        except Exception as exc:
            article.fetch_error = f"{type(exc).__name__}: {exc}"
            detail = str(exc).replace("\n", " ")[:240]
            category = getattr(exc, "category", type(exc).__name__)
            status = getattr(exc, "status_code", None)
            status_text = f" HTTP {status}" if status else ""
            warnings.append(f"Tải lỗi {category}{status_text} cho {url}: {detail}")
        # JS-rendered pages (e.g. MSN) return a shell: keep the search snippet.
        if len(article.content.strip()) < MIN_CONTENT_CHARS and article.snippet:
            article.content = article.snippet
            article.fetch_error = article.fetch_error or "Nội dung mỏng, dùng snippet"
        return article, warnings

    def _fetch_many(self, state: ResearchState, rows: list[dict]) -> None:
        if not rows:
            return
        workers = max(1, min(self.workers, len(rows)))
        if workers == 1:
            results = [self._fetch_one(row) for row in rows]
        else:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                results = list(pool.map(self._fetch_one, rows))
        for article, warnings in results:
            state.articles.append(article)
            state.fetched_count += 1
            state.seen_urls.add(article.url)
            state.warnings.extend(warnings)
            state.actions.append(f"FETCH: {article.title[:90]}")

    # -- corroboration ----------------------------------------------------
    def _annotate_corroboration(self, state: ResearchState) -> None:
        """Count independent domains per story, ignoring syndicated copies."""
        for article in state.articles:
            sources: set[str] = set()
            if article.source:
                sources.add(article.source)
            for other in state.articles:
                if other is article or not same_story(article.title, other.title):
                    continue
                if (article.fingerprint and other.fingerprint
                        and article.fingerprint == other.fingerprint):
                    continue      # same wire copy republished, not independent
                if other.source:
                    sources.add(other.source)
            for row in state.search_evidence:
                if row.get("url") == article.url:
                    continue
                if not same_story(article.title, row.get("title", "")):
                    continue
                host = _host(row.get("url", ""))
                if host and host != article.source:
                    sources.add(host)
            article.corroborating_sources = sorted(sources)

    # -- run --------------------------------------------------------------
    def run(self, query: str) -> tuple[ResearchState, list[SummaryItem], list[dict]]:
        query = " ".join((query or "").split())
        plan = self.planner.plan(query)
        state = ResearchState(
            query=query,
            plan=plan,
            intent=plan.intent,
            max_search_rounds=self.max_rounds,
            max_pages=self.max_pages,
            pages_per_round=self.pages_per_round,
            min_items=self.min_items,
        )
        state.deadline_at = time.time() + self.deadline_seconds
        if plan.note:
            state.warnings.append(plan.note)
        legacy = [name for name in ("HUB_RESEARCH_LAYA_MODE", "HUB_LAYA_MODEL", "HUB_LAYA_DEVICE")
                  if os.getenv(name)]
        if legacy:
            state.warnings.append(
                f"{', '.join(legacy)} đã bỏ và không còn tác dụng; có thể xoá khỏi .env.")
        state.actions.append(
            f"PLAN: {len(plan.queries)} truy vấn · {plan.intent} · {plan.origin}")
        terms = query_terms(query, " ".join(plan.queries))

        while not deadline_passed(state):
            sufficient, reason = evidence_sufficient(state)
            if sufficient:
                state.stop_reason = reason
                break
            if state.search_rounds >= state.max_search_rounds:
                state.stop_reason = f"đã dùng hết {state.max_search_rounds} vòng tìm kiếm"
                break
            if state.fetched_count >= state.max_pages:
                state.stop_reason = f"đã đọc đủ {state.max_pages} trang"
                break
            next_query = self._next_query(state, plan)
            if not next_query:
                state.stop_reason = "hết truy vấn trong kế hoạch"
                break
            self._search(state, next_query)
            selected = self._select_urls(state, terms, plan.domains)
            if not selected:
                continue
            self._fetch_many(state, selected)
            self._annotate_corroboration(state)
            rank_articles(state.articles, terms, intent=plan.intent,
                          recency_days=plan.recency_days,
                          preferred_domains=plan.domains)
        else:
            state.stop_reason = f"hết thời gian {self.deadline_seconds}s"

        ranked = rank_articles(state.articles, terms, intent=plan.intent,
                               recency_days=plan.recency_days,
                               preferred_domains=plan.domains)
        sufficient, reason = evidence_sufficient(state)
        finalize_evidence(state, state.stop_reason or reason)
        if not state.stop_reason:
            state.stop_reason = reason or "hết ngân sách"
        items, dropped = self.summarizer.summarize(query, ranked)
        return state, items, dropped


def run_research(query: str) -> str:
    pipeline = ResearchPipeline()
    state, items, dropped = pipeline.run(query)
    return format_report(state, items, dropped)
