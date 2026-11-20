"""LLM summary + junk filter, with a deterministic extractive fallback.

The summariser receives only text that was actually fetched, so it cannot invent
facts. It returns at most ``HUB_RESEARCH_TOP_ITEMS`` clean items; duplicate
stories, clickbait and off-topic pieces are reported as ``dropped`` so the final
report can say what was filtered out.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field

from ....model_router import get_llm
from ..processors.deduplicate import independent_source_count, independent_domains, same_story
from ..state import Article

DEFAULT_TOP_ITEMS = 5
DEFAULT_ARTICLE_CHARS = 1500
DEFAULT_INPUT_CHARS = 12000
MIN_CONTENT_CHARS = 200
_JSON_BLOCK = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?…])\s+")


@dataclass
class SummaryItem:
    url: str
    headline: str
    summary: str
    why: str = ""
    confidence: str = "medium"          # high | medium | low
    sources: list[str] = field(default_factory=list)
    domain: str = ""
    published_at: str | None = None
    score: float = 0.0
    independent: int = 1


def _int_env(name: str, default: int, low: int, high: int) -> int:
    try:
        return max(low, min(high, int(os.getenv(name, str(default)))))
    except (TypeError, ValueError):
        return default


def _text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            block if isinstance(block, str) else block.get("text", "")
            for block in content
        )
    return str(content)


def first_sentences(text: str, count: int = 2, max_chars: int = 420) -> str:
    """Take whole sentences only: the old report cut mid-sentence at 450 chars."""
    clean = " ".join((text or "").split())
    if not clean:
        return ""
    pieces = [piece.strip() for piece in _SENTENCE_SPLIT.split(clean) if piece.strip()]
    kept: list[str] = []
    for piece in pieces[:count]:
        if kept and len(" ".join(kept + [piece])) > max_chars:
            break
        kept.append(piece)
        if len(" ".join(kept)) >= max_chars * 0.8:
            break
    summary = " ".join(kept)
    if len(summary) > max_chars:
        cut = summary.rfind(" ", 0, max_chars - 1)
        summary = summary[: cut if cut > 40 else max_chars - 1].rstrip(" ,;:") + " …"
    elif summary and summary[-1] not in ".!?…":
        summary += " …"
    return summary


def _select_candidates(articles: list[Article], limit: int) -> list[Article]:
    """Top distinct stories with usable text, one article per story."""
    ordered = sorted(articles, key=lambda item: item.final_score, reverse=True)
    chosen: list[Article] = []
    for article in ordered:
        if len((article.content or "").strip()) < MIN_CONTENT_CHARS:
            continue
        if any(same_story(article.title, other.title) for other in chosen):
            continue
        chosen.append(article)
        if len(chosen) >= max(1, limit):
            break
    return chosen


def _enrich(item: SummaryItem, article: Article, cluster: list[Article]) -> SummaryItem:
    item.url = article.url
    item.headline = item.headline or article.title
    item.domain = article.source
    item.published_at = article.published_at
    item.score = article.final_score
    item.sources = sorted(independent_domains(cluster))
    item.independent = independent_source_count(cluster)
    if not item.why:
        item.why = (f"{item.independent} nguồn độc lập" if item.independent >= 2
                    else "chỉ 1 nguồn, nên kiểm chứng thêm")
    return item


def build_prompt(query: str, articles: list[Article], *, limit: int,
                 article_chars: int, input_chars: int) -> str:
    blocks: list[str] = []
    used = 0
    for index, article in enumerate(articles, 1):
        body = " ".join(article.content.split())
        body = body[:article_chars]
        block = (
            f"[{index}] url: {article.url}\n"
            f"title: {article.title}\n"
            f"source: {article.source or 'unknown'} | published: {article.published_at or 'unknown'}\n"
            f"body: {body}\n"
        )
        if used + len(block) > input_chars and blocks:
            break
        blocks.append(block)
        used += len(block)
    return (
        "Bạn là biên tập viên research. Người dùng hỏi: " + query + "\n\n"
        "Dưới đây là các bài đã tải về (điểm cao trước). Chọn tối đa "
        f"{limit} tin sạch nhất và tóm tắt.\n\n"
        + "\n".join(blocks)
        + "\nTrả về DUY NHẤT JSON:\n"
        '{"items": [{"url": "...", "headline": "...", "summary": "...", '
        '"why": "...", "confidence": "high|medium|low"}], '
        '"dropped": [{"url": "...", "reason": "duplicate|clickbait|off-topic|thin"}]}\n'
        "Quy tắc:\n"
        "- summary 2-3 câu tiếng Việt, chỉ dùng thông tin trong body; nêu số liệu nếu có.\n"
        "- Không bịa, không thêm bối cảnh ngoài bài, không quảng cáo.\n"
        "- Loại tin trùng ý nhau, giật tít, PR, hoặc lạc chủ đề.\n"
        "- url phải đúng như trong danh sách trên.\n"
        "Chỉ trả JSON."
    )


def _payload(raw: str) -> dict:
    text = raw.strip()
    match = _JSON_BLOCK.search(text)
    if match:
        text = match.group(1)
    elif not text.startswith("{"):
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise ValueError("no JSON object in summary response")
        text = text[start:end + 1]
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("summary response is not an object")
    return data


class ArticleSummarizer:
    def __init__(self, llm_factory=None, enabled: bool | None = None,
                 top_items: int | None = None):
        self.llm_factory = llm_factory or get_llm
        self.enabled = enabled
        self.top_items = top_items

    def _on(self) -> bool:
        from ..controller.planner import llm_enabled

        return llm_enabled(self.enabled)

    def summarize(self, query: str, articles: list[Article],
                  limit: int | None = None) -> tuple[list[SummaryItem], list[dict]]:
        limit = limit or self.top_items or _int_env(
            "HUB_RESEARCH_TOP_ITEMS", DEFAULT_TOP_ITEMS, 1, 10)
        candidates = _select_candidates(articles, limit * 2)
        if not candidates:
            return [], []
        if not self._on():
            return self._extractive(candidates, limit), []
        try:
            prompt = build_prompt(
                query, candidates, limit=limit,
                article_chars=_int_env("HUB_RESEARCH_SUMMARY_ARTICLE_CHARS",
                                       DEFAULT_ARTICLE_CHARS, 400, 6000),
                input_chars=_int_env("HUB_RESEARCH_SUMMARY_INPUT_CHARS",
                                     DEFAULT_INPUT_CHARS, 2000, 40000),
            )
            llm = self.llm_factory("research")
            response = llm.invoke(prompt)
            data = _payload(_text(getattr(response, "content", response)))
        except Exception:
            return self._extractive(candidates, limit), []

        by_url = {article.url: article for article in candidates}
        clusters = self._clusters(candidates)
        items: list[SummaryItem] = []
        dropped: list[dict] = []
        seen_urls: set[str] = set()
        for raw in data.get("items") or []:
            if not isinstance(raw, dict):
                continue
            url = str(raw.get("url") or "").strip()
            article = by_url.get(url)
            if article is None:
                dropped.append({"url": url[:300], "reason": "unknown-url"})
                continue
            if url in seen_urls:
                dropped.append({"url": url, "reason": "duplicate"})
                continue
            summary = " ".join(str(raw.get("summary") or "").split())
            if len(summary) < 40:
                dropped.append({"url": url, "reason": "thin"})
                continue
            seen_urls.add(url)
            headline = " ".join(str(raw.get("headline") or article.title).split())[:160]
            confidence = str(raw.get("confidence") or "").lower()
            item = SummaryItem(
                url=url, headline=headline, summary=summary[:800],
                why=" ".join(str(raw.get("why") or "").split())[:200],
                confidence=confidence if confidence in {"high", "medium", "low"} else "medium",
            )
            items.append(_enrich(item, article, clusters.get(url, [article])))
            if len(items) >= limit:
                break
        for raw in data.get("dropped") or []:
            if isinstance(raw, dict) and raw.get("url"):
                dropped.append({"url": str(raw["url"])[:300],
                                "reason": str(raw.get("reason") or "filtered")[:40]})
        if not items:
            return self._extractive(candidates, limit), dropped
        return items, dropped

    @staticmethod
    def _clusters(articles: list[Article]) -> dict[str, list[Article]]:
        clusters: list[list[Article]] = []
        for article in articles:
            for cluster in clusters:
                if same_story(article.title, cluster[0].title):
                    cluster.append(article)
                    break
            else:
                clusters.append([article])
        return {article.url: cluster for cluster in clusters for article in cluster}

    def _extractive(self, articles: list[Article], limit: int) -> list[SummaryItem]:
        clusters = self._clusters(articles)
        items: list[SummaryItem] = []
        for article in articles[:limit]:
            cluster = clusters.get(article.url, [article])
            sources = independent_source_count(cluster)
            confidence = "high" if sources >= 3 else "medium" if sources >= 2 else "low"
            item = SummaryItem(
                url=article.url, headline=article.title,
                summary=first_sentences(article.content, count=2),
                confidence=confidence,
            )
            items.append(_enrich(item, article, cluster))
        return items
