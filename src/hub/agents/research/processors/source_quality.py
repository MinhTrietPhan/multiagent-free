from urllib.parse import urlparse

# A conservative starter allowlist. This is a heuristic, not a factual guarantee of credibility.
TRUSTED_DOMAINS = {
    "reuters.com": 1.0, "apnews.com": 1.0, "bbc.com": 0.95, "bbc.co.uk": 0.95,
    "theguardian.com": 0.9, "nytimes.com": 0.9, "arstechnica.com": 0.9,
    "technologyreview.com": 0.9, "nature.com": 1.0, "science.org": 1.0,
    "openai.com": 0.85, "deepmind.google": 0.85, "ai.google": 0.85,
    "nvidia.com": 0.85, "microsoft.com": 0.85, "anthropic.com": 0.85,
}


def source_score(url: str) -> float:
    host = (urlparse(url).hostname or "").lower().removeprefix("www.")
    if host in TRUSTED_DOMAINS:
        return TRUSTED_DOMAINS[host]
    for domain, score in TRUSTED_DOMAINS.items():
        if host.endswith("." + domain):
            return score
    return 0.75 if host.endswith(".gov") else 0.65 if host.endswith(".edu") else 0.4
