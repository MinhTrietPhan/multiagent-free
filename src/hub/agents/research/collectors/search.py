from __future__ import annotations
import os


class WebSearchClient:
    def __init__(self, max_results: int = 12):
        self.max_results = max_results

    def search(self, query: str) -> list[dict]:
        try:
            from ddgs import DDGS
            from ddgs.exceptions import DDGSException
        except ImportError as exc:
            raise RuntimeError("Thiếu ddgs. Cài bằng: pip install -e '.[research]'") from exc
        region = os.getenv("HUB_RESEARCH_REGION", "wt-wt")
        rows: list[dict] = []
        with DDGS() as client:
            for kind in ("news", "text"):
                if len(rows) >= 5:
                    break
                try:
                    rows += list(getattr(client, kind)(query, region=region,
                                                       max_results=self.max_results) or [])
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
            })
        return results