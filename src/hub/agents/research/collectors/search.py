"""Web search with two interchangeable backends.

* ``ddgs`` — DuckDuckGo news + text (the original backend).
* ``rss``  — Google News RSS, free and much better for Vietnamese headlines.

The backends are merged and de-duplicated by canonical URL and by normalised
title, so the caller sees one ranked list of candidates regardless of source.
If one backend fails (blocked, rate limited, malformed feed) the other still
returns results and the error is recorded in ``errors``.
"""
from __future__ import annotations

import os
import re
import unicodedata
from email.utils import parsedate_to_datetime
from urllib.parse import urlencode

from ..processors.deduplicate import canonical_url

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
RSS_URL = "https://news.google.com/rss/search"
_TAG = re.compile(r"<[^>]+>")
_SPACES = re.compile(r"\s+")


def _normalize_title(title: str) -> str:
    folded = unicodedata.normalize("NFD", title or "")
    folded = "".join(ch for ch in folded if unicodedata.category(ch) != "Mn")
    return " ".join(re.findall(r"[\w]+", folded.casefold()))


def _clean_text(value: str) -> str:
    from html import unescape

    return _SPACES.sub(" ", unescape(_TAG.sub(" ", value or ""))).strip()


def _fetch_rss(url: str, params: dict) -> bytes:
    try:
        import requests
    except ImportError as exc:
        raise RuntimeError("Thiếu requests. Cài bằng: pip install -e '.[research]'") from exc
    response = requests.get(f"{url}?{urlencode(params)}", headers={"User-Agent": USER_AGENT},
                            timeout=10)
    response.raise_for_status()
    return response.content


def _parse_rss(payload: bytes | str) -> list[dict]:
    """Parse a Google News RSS document into search rows (bounded, no network)."""
    import xml.etree.ElementTree as ET

    if isinstance(payload, str):
        payload = payload.encode("utf-8")
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        raise ValueError(f"RSS không hợp lệ: {exc}") from exc

    rows: list[dict] = []
    for item in root.iter("item"):
        link = (item.findtext("link") or "").strip()
        title = _clean_text(item.findtext("title") or "")
        if not link or not title:
            continue
        source_name = (item.findtext("source") or "").strip()
        source_url = ""
        for child in item:
            if child.tag == "source":
                source_url = child.attrib.get("url", "")
                break
        # Google News appends " - Publisher" to every headline.
        if source_name and title.endswith(f" - {source_name}"):
            title = title[: -(len(source_name) + 3)].rstrip()

        published = None
        raw_date = (item.findtext("pubDate") or "").strip()
        if raw_date:
            try:
                published = parsedate_to_datetime(raw_date).isoformat()
            except (TypeError, ValueError, OverflowError):
                published = None

        rows.append({
            "url": link,
            "title": title,
            "snippet": _clean_text(item.findtext("description") or "")[:500],
            "published_at": published,
            "publisher_url": source_url,
            "backend": "rss",
        })
    return rows


def _merge(rows: list[dict]) -> list[dict]:
    """De-duplicate across backends by canonical URL, then by normalised title."""
    output: list[dict] = []
    seen_urls: set[str] = set()
    seen_titles: set[str] = set()
    for row in rows:
        url = canonical_url(row.get("url", ""))
        title = _normalize_title(row.get("title", ""))
        if not url or not title:
            continue
        if url in seen_urls or title in seen_titles:
            continue
        seen_urls.add(url)
        seen_titles.add(title)
        output.append({**row, "url": url})
    return output


class WebSearchClient:
    def __init__(self, max_results: int = 12, backends: list[str] | None = None,
                 region: str | None = None, rss_fetcher=None):
        configured = backends if backends is not None else [
            part.strip().lower()
            for part in os.getenv("HUB_RESEARCH_SEARCH_BACKENDS", "ddgs,rss").split(",")
            if part.strip()
        ]
        self.backends = [name for name in configured if name in {"ddgs", "rss"}] or ["ddgs"]
        self.max_results = max_results
        self.region = region or os.getenv("HUB_RESEARCH_REGION", "wt-wt")
        self.rss_fetcher = rss_fetcher or _fetch_rss
        self.errors: list[str] = []

    # -- backends ---------------------------------------------------------
    def _search_ddgs(self, query: str) -> list[dict]:
        try:
            from ddgs import DDGS
            from ddgs.exceptions import DDGSException
        except ImportError as exc:
            raise RuntimeError("Thiếu ddgs. Cài bằng: pip install -e '.[research]'") from exc

        rows: list[dict] = []
        with DDGS() as client:
            for kind in ("news", "text"):
                if len(rows) >= 5:
                    break
                try:
                    rows += list(getattr(client, kind)(
                        query, region=self.region, max_results=self.max_results) or [])
                except DDGSException:
                    continue
        results = []
        for row in rows:
            url = row.get("href") or row.get("url")
            if not url:
                continue
            results.append({
                "url": url,
                "title": row.get("title") or url,
                "snippet": row.get("body") or row.get("snippet") or "",
                "published_at": row.get("date") or row.get("published") or None,
                "backend": "ddgs",
            })
        return results

    def _search_rss(self, query: str) -> list[dict]:
        params = {
            "q": query,
            "hl": os.getenv("HUB_RESEARCH_RSS_HL", "vi"),
            "gl": os.getenv("HUB_RESEARCH_RSS_GL", "VN"),
            "ceid": os.getenv("HUB_RESEARCH_RSS_CEID", "VN:vi"),
        }
        return _parse_rss(self.rss_fetcher(RSS_URL, params))

    # -- public -----------------------------------------------------------
    def search(self, query: str) -> list[dict]:
        self.errors = []
        rows: list[dict] = []
        for backend in self.backends:
            try:
                rows += (self._search_ddgs if backend == "ddgs" else self._search_rss)(query)
            except Exception as exc:
                self.errors.append(f"Search backend {backend} lỗi: {type(exc).__name__}: {exc}")
        return _merge(rows)[: self.max_results]
