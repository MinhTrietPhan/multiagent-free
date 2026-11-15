from __future__ import annotations


class WebSearchClient:
    def __init__(self, max_results: int = 12):
        self.max_results = max_results

    def search(self, query: str) -> list[dict]:
        try:
            from ddgs import DDGS
        except ImportError as exc:
            raise RuntimeError("Thiếu ddgs. Cài bằng: pip install -e '.[research]'") from exc
        with DDGS() as client:
            rows = client.text(query, max_results=self.max_results)
        results = []
        for row in rows or []:
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
