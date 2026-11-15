from __future__ import annotations
import re
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

TRACKING_KEYS = {"fbclid", "gclid", "mc_cid", "mc_eid"}


def canonical_url(url: str) -> str:
    parts = urlsplit(url.strip())
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if not k.lower().startswith("utm_") and k.lower() not in TRACKING_KEYS]
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/"), urlencode(query), ""))


def title_key(title: str) -> set[str]:
    return set(re.findall(r"[\w]{3,}", title.lower()))


def deduplicate(rows: list[dict]) -> list[dict]:
    output, urls, title_sets = [], set(), []
    for row in rows:
        url = canonical_url(row.get("url", ""))
        title = (row.get("title") or "").strip()
        tokens = title_key(title)
        if not url or url in urls:
            continue
        duplicate = False
        for old in title_sets:
            union = tokens | old
            similarity = len(tokens & old) / len(union) if union else 0
            if similarity >= 0.78:
                duplicate = True
                break
        if duplicate:
            continue
        urls.add(url)
        title_sets.append(tokens)
        output.append({**row, "url": url})
    return output
