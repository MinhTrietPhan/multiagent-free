from __future__ import annotations

import hashlib
import re
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

TRACKING_KEYS = {"fbclid", "gclid", "mc_cid", "mc_eid"}

# Minimum body length before a content fingerprint is trusted as a duplicate signal.
FINGERPRINT_MIN_CHARS = 500
FINGERPRINT_CHARS = 300


def canonical_url(url: str) -> str:
    parts = urlsplit((url or "").strip())
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if not k.lower().startswith("utm_") and k.lower() not in TRACKING_KEYS]
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/") or "/",
                       urlencode(query), ""))


def title_key(title: str) -> set[str]:
    """Normalised token set of a title, used for same-story similarity."""
    return set(re.findall(r"[\w]+", (title or "").casefold()))


def same_story(title: str, other: str, threshold: float = 0.45) -> bool:
    """True when two headlines describe the same story (token overlap, not exact match)."""
    left, right = title_key(title), title_key(other)
    union = left | right
    return bool(union) and len(left & right) / len(union) >= threshold


def content_fingerprint(text: str) -> str | None:
    """Hash the opening of an article body to spot syndicated copies.

    Only used for bodies long enough to be distinctive; short snippets and
    pages that failed extraction return ``None`` (caller falls back to domain).
    """
    if not text or len(text.strip()) < FINGERPRINT_MIN_CHARS:
        return None
    body = re.sub(r"[^\w\s]", "", " ".join(text.split()).casefold())
    if len(body) < FINGERPRINT_MIN_CHARS:
        return None
    return hashlib.sha1(body[:FINGERPRINT_CHARS].encode("utf-8")).hexdigest()[:16]


def independent_source_count(articles: list) -> int:
    """Count distinct original sources: one per unique fingerprint, otherwise one per domain."""
    keys: set[str] = set()
    for article in articles:
        if not getattr(article, "content", "").strip():
            continue
        fingerprint = getattr(article, "fingerprint", None)
        keys.add(f"fp:{fingerprint}" if fingerprint else f"host:{getattr(article, 'source', '')}")
    return len(keys)


def independent_domains(articles: list) -> set[str]:
    return {getattr(article, "source", "") for article in articles if getattr(article, "source", "")}


def _host(url: str) -> str:
    return (urlsplit(url).hostname or "").lower().removeprefix("www.")


def _path(url: str) -> str:
    return (urlsplit(url).path or "/").rstrip("/") or "/"


def _similarity(left: set[str], right: set[str]) -> float:
    union = left | right
    return len(left & right) / len(union) if union else 1.0


def deduplicate(rows: list[dict]) -> list[dict]:
    """Remove duplicate search hits while preserving same-path cross-publisher copies.

    Exact canonical URLs are always removed. Near-identical titles are collapsed
    within one publisher. Across publishers, retain a matching title only when
    both publishers point to the same article path (syndicated coverage).
    """
    output: list[dict] = []
    seen_urls: set[str] = set()
    accepted: list[tuple[str, str, set[str]]] = []

    for row in rows:
        url = canonical_url(row.get("url", ""))
        if not url or url in seen_urls:
            continue
        host, path = _host(url), _path(url)
        tokens = title_key(row.get("title", ""))
        duplicate = False
        for old_host, old_path, old_tokens in accepted:
            if _similarity(tokens, old_tokens) < 0.78:
                continue
            # Same host: same story copied to another URL. Different hosts:
            # different paths imply duplicate search noise; same path is retained.
            if host == old_host or path != old_path:
                duplicate = True
                break
        if duplicate:
            continue
        seen_urls.add(url)
        accepted.append((host, path, tokens))
        output.append({**row, "url": url})
    return output
