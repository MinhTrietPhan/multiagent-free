from __future__ import annotations
import os
from datetime import datetime
from urllib.parse import urlparse

from ..collectors.search import WebSearchClient
from ..collectors.fetch import fetch_article
from ..processors.deduplicate import deduplicate, title_key
from ..ranking.engine import rank_articles
from ..reporting.formatter import format_report
from ..state import Article, ResearchState
from .decision_engine import LayaDecisionEngine
from .policy import choose_action


def _same_story(title: str, other: str) -> bool:
    a, b = title_key(title), title_key(other)
    union = a | b
    return bool(union) and len(a & b) / len(union) >= 0.45


class ResearchPipeline:
    def __init__(self, searcher=None, fetcher=None, decision_engine=None):
        self.searcher = searcher or WebSearchClient(max_results=int(os.getenv("HUB_RESEARCH_RESULTS_PER_QUERY", "12")))
        self.fetcher = fetcher or fetch_article
        self.decision_engine = decision_engine or LayaDecisionEngine()
        self.mode = os.getenv("HUB_RESEARCH_LAYA_MODE", "shadow").lower()
        if self.mode not in {"shadow", "active", "off"}:
            self.mode = "shadow"
        self.max_rounds = int(os.getenv("HUB_RESEARCH_MAX_SEARCH_ROUNDS", "2"))
        self.max_pages = int(os.getenv("HUB_RESEARCH_MAX_PAGES", "12"))

    def _search(self, state: ResearchState):
        today = datetime.now().astimezone().date().isoformat()
        if state.search_rounds == 0:
            query = f'{state.query} {today}'
        elif state.search_rounds == 1:
            query = f'{state.query} latest news developments'
        else:
            query = f'{state.query} analysis official announcement'
        rows = self.searcher.search(query)
        state.search_rounds += 1
        state.search_evidence.extend(rows)
        merged = deduplicate(state.all_search_results + rows)
        state.all_search_results = merged
        fetched = {a.url for a in state.articles}
        queued = {r["url"] for r in state.pending_urls}
        state.pending_urls.extend(r for r in merged if r["url"] not in fetched and r["url"] not in queued)
        state.actions.append(f"SEARCH: {len(rows)} results for round {state.search_rounds}")

    def _fetch_next(self, state: ResearchState):
        if not state.pending_urls or state.fetched_count >= state.max_pages:
            return
        row = state.pending_urls.pop(0)
        url = row["url"]
        domain = (urlparse(url).hostname or "").removeprefix("www.")
        article = Article(url=url, title=row.get("title") or url,
                          snippet=row.get("snippet", ""), source=domain,
                          published_at=row.get("published_at"))
        try:
            content, final_url = self.fetcher(url)
            article.content = content[:12000]
            article.url = final_url
            article.source = (urlparse(final_url).hostname or domain).removeprefix("www.")
        except Exception as exc:
            article.fetch_error = f"{type(exc).__name__}: {exc}"
            state.warnings.append(f"Fetch failed for {url}: {type(exc).__name__}")
        corroborating = set()
        for candidate in state.search_evidence:
            if candidate.get("url") != url and _same_story(article.title, candidate.get("title", "")):
                host = (urlparse(candidate["url"]).hostname or "").removeprefix("www.")
                if host and host != article.source:
                    corroborating.add(host)
        if article.source:
            corroborating.add(article.source)
        article.corroborating_sources = sorted(corroborating)
        state.articles.append(article)
        state.fetched_count += 1
        state.actions.append(f"FETCH: {article.title[:90]}")

    def _verify_top(self, state: ResearchState):
        if not state.articles:
            return
        current = rank_articles(state.articles)[0]
        query = f'"{current.title[:160]}"'
        try:
            rows = self.searcher.search(query)
        except Exception as exc:
            state.warnings.append(f"Verification search failed: {type(exc).__name__}")
            return
        state.search_evidence.extend(rows)
        domains = set(current.corroborating_sources)
        for row in rows:
            if _same_story(current.title, row.get("title", "")):
                host = (urlparse(row.get("url", "")).hostname or "").removeprefix("www.")
                if host:
                    domains.add(host)
        current.corroborating_sources = sorted(domains)
        state.actions.append(f"VERIFY: {len(domains)} distinct matching domains for top story")

    def run(self, query: str) -> tuple[ResearchState, list[Article]]:
        state = ResearchState(query=query, max_search_rounds=self.max_rounds, max_pages=self.max_pages)
        for _ in range(state.max_iterations):
            engine = self.decision_engine if self.mode != "off" else None
            action, predicted = choose_action(state, engine, self.mode)
            if action == "SEARCH_MORE":
                self._search(state)
            elif action == "FETCH_NEXT":
                self._fetch_next(state)
            elif action == "VERIFY_TOP":
                self._verify_top(state)
                # Prevent repeated VERIFY_TOP from trapping the loop.
                if self.mode == "active":
                    state.actions.append("VERIFY completed; next action must progress or stop")
                    if state.pending_urls:
                        self._fetch_next(state)
                    elif state.search_rounds < state.max_search_rounds:
                        self._search(state)
                    else:
                        break
            else:
                break
        else:
            state.warnings.append("Reached hard iteration limit; research stopped safely.")

        laya_scores = {}
        if self.mode != "off" and state.articles and not getattr(self.decision_engine, "_unavailable", False):
            candidates = [a for a in state.articles if a.content][:8]
            try:
                laya_scores = self.decision_engine.score_articles([
                    {"url": a.url, "title": a.title, "source": a.source,
                     "published_at": a.published_at, "content": a.content[:250]}
                    for a in candidates
                ])
            except Exception as exc:
                state.warnings.append(f"Laya scoring unavailable: {type(exc).__name__}")
        for article in state.articles:
            if article.url in laya_scores:
                article.laya_score = laya_scores[article.url]
        ranked = rank_articles(state.articles, laya_scores, use_laya=self.mode == "active")
        return state, ranked


def run_research(query: str) -> str:
    pipeline = ResearchPipeline()
    state, ranked = pipeline.run(query)
    return format_report(state, ranked, pipeline.mode)
