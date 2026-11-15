from __future__ import annotations
from datetime import datetime, timezone, timedelta
import re
from urllib.parse import urlparse
from ..state import Article
from ..processors.source_quality import source_score


def _recency_score(published_at: str | None) -> float:
    if not published_at:
        return 0.35
    try:
        relative = re.fullmatch(r"\s*(\d+)\s+(minute|hour|day)s?\s+ago\s*", published_at.lower())
        if relative:
            amount = int(relative.group(1))
            unit = relative.group(2)
            delta = timedelta(minutes=amount) if unit == "minute" else timedelta(hours=amount) if unit == "hour" else timedelta(days=amount)
            value = datetime.now(timezone.utc) - delta
        elif published_at.lower().strip() == "yesterday":
            value = datetime.now(timezone.utc) - timedelta(days=1)
        else:
            value = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        hours = max(0.0, (datetime.now(timezone.utc) - value).total_seconds() / 3600)
        return max(0.0, 1.0 - hours / (72 * 1.0))
    except (ValueError, TypeError):
        return 0.35


def rank_articles(articles: list[Article], laya_scores: dict[str, float] | None = None,
                  use_laya: bool = False) -> list[Article]:
    laya_scores = laya_scores or {}
    for article in articles:
        domain_score = source_score(article.url)
        freshness = _recency_score(article.published_at)
        corroboration = min(1.0, len(set(article.corroborating_sources)) / 3.0)
        content_signal = min(1.0, len(article.content) / 5000.0)
        # Transparent baseline: no social-engagement data is available, so this is not a virality score.
        heuristic = 100 * (0.35 * freshness + 0.30 * domain_score + 0.25 * corroboration + 0.10 * content_signal)
        article.heuristic_score = round(heuristic, 2)
        if use_laya and article.url in laya_scores:
            article.laya_score = round(laya_scores[article.url], 2)
            article.final_score = round(0.65 * article.laya_score + 0.35 * heuristic, 2)
        else:
            article.final_score = article.heuristic_score
    return sorted(articles, key=lambda item: item.final_score, reverse=True)
