from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timedelta, timezone

from ..processors.source_quality import host_of, source_score
from ..state import Article

# Function words only: content words like "tin", "giá", "AI" must survive,
# otherwise short news questions would look like they have no keywords.
STOPWORDS = {
    "va", "la", "cua", "co", "cho", "voi", "nhung", "cac", "mot", "nay", "do",
    "thi", "ma", "de", "tu", "den", "trong", "ngoai", "khi", "sau", "truoc",
    "the", "and", "for", "with", "from", "that", "this", "what", "when", "how",
    "mot", "cung", "rat", "hon", "nhat", "moi", "hom", "nay", "bay", "gio",
}

# (relevance, freshness, credibility, corroboration, completeness)
DEFAULT_WEIGHTS = (0.40, 0.25, 0.20, 0.10, 0.05)
GENERIC_WEIGHTS = (0.00, 0.45, 0.30, 0.15, 0.10)


def fold(text: str) -> str:
    """Lower-case and remove Vietnamese diacritics (for tolerant token matching)."""
    decomposed = unicodedata.normalize("NFD", text or "")
    return "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn").casefold()


def tokens(text: str) -> list[str]:
    return [token for token in re.findall(r"\w+", fold(text)) if len(token) > 1]


def query_terms(*texts: str, limit: int = 24) -> list[str]:
    """Meaningful query keywords, de-duplicated and order-preserving."""
    terms: list[str] = []
    for text in texts:
        for token in tokens(text):
            if token in STOPWORDS or token in terms:
                continue
            terms.append(token)
    return terms[:limit]


def _coverage(haystack: list[str], terms: list[str]) -> float:
    if not terms or not haystack:
        return 0.0
    pool = set(haystack)
    matched = sum(1 for term in terms if term in pool)
    hits = sum(haystack.count(term) for term in terms)
    frequency = min(1.0, hits / (2 * len(terms)))
    return min(1.0, 0.75 * (matched / len(terms)) + 0.25 * frequency)


def relevance_score(article: Article, terms: list[str]) -> float:
    text = f"{article.title} {article.snippet} {article.content[:800]}"
    return _coverage(tokens(text), terms)


def _recency_score(published_at: str | None, window_days: float = 7) -> float | None:
    """1.0 = just published, 0.0 = older than the window, None = date unknown."""
    if not published_at:
        return None
    try:
        relative = re.fullmatch(r"\s*(\d+)\s+(minute|hour|day)s?\s+ago\s*",
                                published_at.lower())
        if relative:
            amount = int(relative.group(1))
            unit = relative.group(2)
            delta = (timedelta(minutes=amount) if unit == "minute"
                     else timedelta(hours=amount) if unit == "hour"
                     else timedelta(days=amount))
            value = datetime.now(timezone.utc) - delta
        elif published_at.lower().strip() == "yesterday":
            value = datetime.now(timezone.utc) - timedelta(days=1)
        else:
            value = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        hours = max(0.0, (datetime.now(timezone.utc) - value).total_seconds() / 3600)
        return max(0.0, 1.0 - hours / max(1.0, window_days * 24))
    except (ValueError, TypeError, OverflowError):
        return None


def _weights(terms: list[str], intent: str, date_known: bool) -> tuple[float, ...]:
    relevance, freshness, credibility, corroboration, completeness = (
        DEFAULT_WEIGHTS if len(terms) >= 2 else GENERIC_WEIGHTS
    )
    if not date_known:
        # Unknown dates must not look fresh. In digest mode the lost freshness
        # weight moves to relevance/credibility so old reprints fall down.
        lost = freshness * (0.7 if intent == "digest" else 0.3)
        freshness -= lost
        relevance += lost * 0.6
        credibility += lost * 0.4
    return relevance, freshness, credibility, corroboration, completeness


def _preferred_boost(url: str, preferred_domains: list[str] | None) -> float:
    """Small nudge for domains the planner suggested; never a hard filter."""
    if not preferred_domains:
        return 0.0
    host = host_of(url)
    return 0.08 if any(host == d or host.endswith("." + d) for d in preferred_domains) else 0.0


def rank_articles(articles: list[Article], terms: list[str] | None = None,
                  intent: str = "digest", recency_days: int = 7,
                  preferred_domains: list[str] | None = None) -> list[Article]:
    terms = terms or []
    for article in articles:
        freshness = _recency_score(article.published_at, recency_days)
        date_known = freshness is not None
        relevance, fresh_w, credibility, corroboration_w, completeness = _weights(
            terms, intent, date_known)
        independent = len(set(article.corroborating_sources))
        relevance_fraction = relevance_score(article, terms)
        article.relevance_score = round(100 * relevance_fraction, 2)
        credibility_score = min(
            1.0, source_score(article.url) + _preferred_boost(article.url, preferred_domains))
        article.heuristic_score = round(100 * (
            relevance * relevance_fraction
            + fresh_w * (freshness or 0.0)
            + credibility * credibility_score
            + corroboration_w * min(1.0, independent / 3.0)
            + completeness * min(1.0, len(article.content) / 5000.0)
        ), 2)
        article.final_score = article.heuristic_score
    return sorted(articles, key=lambda item: item.final_score, reverse=True)


def rank_search_rows(rows: list[dict], terms: list[str], recency_days: int = 7,
                     preferred_domains: list[str] | None = None) -> list[dict]:
    """Score candidates before fetching, so budgets are spent on the best URLs."""
    scored: list[tuple[float, dict]] = []
    for row in rows:
        relevance = _coverage(tokens(f"{row.get('title', '')} {row.get('snippet', '')}"), terms)
        freshness = _recency_score(row.get("published_at"), recency_days)
        score = (0.55 * relevance + 0.30 * source_score(row.get("url", ""))
                 + 0.15 * (freshness if freshness is not None else 0.25)
                 + _preferred_boost(row.get("url", ""), preferred_domains))
        scored.append((score, row))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [row for _score, row in scored]
