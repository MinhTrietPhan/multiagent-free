from urllib.parse import urlparse

# Curated starting list. It is a heuristic, not a factual guarantee of credibility.
# The planner may only suggest these domains ("nguồn đã duyệt"), and the ranking
# boosts them — results outside the list are still accepted when they are relevant.
TRUSTED_DOMAINS = {
    # Wire services and international outlets
    "reuters.com": 1.0, "apnews.com": 1.0, "bbc.com": 0.95, "bbc.co.uk": 0.95,
    "theguardian.com": 0.9, "nytimes.com": 0.9, "arstechnica.com": 0.9,
    "technologyreview.com": 0.9, "nature.com": 1.0, "science.org": 1.0,
    "openai.com": 0.85, "deepmind.google": 0.85, "ai.google": 0.85,
    "nvidia.com": 0.85, "microsoft.com": 0.85, "anthropic.com": 0.85,
    "techcrunch.com": 0.8, "theverge.com": 0.8, "wired.com": 0.8,
    # Vietnamese outlets (general + technology)
    "vnexpress.net": 0.9, "tuoitre.vn": 0.85, "thanhnien.vn": 0.85,
    "nhandan.vn": 0.85, "vietnamnet.vn": 0.8, "vtv.vn": 0.8,
    "dantri.com.vn": 0.75, "znews.vn": 0.75, "thesaigontimes.vn": 0.75,
    "vietnamplus.vn": 0.8, "cafef.vn": 0.7, "genk.vn": 0.7, "ictnews.vn": 0.7,
}

APPROVED_DOMAINS = tuple(sorted(TRUSTED_DOMAINS))


def host_of(url: str) -> str:
    return (urlparse(url).hostname or "").lower().removeprefix("www.")


def is_approved(url: str) -> bool:
    host = host_of(url)
    return any(host == domain or host.endswith("." + domain) for domain in TRUSTED_DOMAINS)


def source_score(url: str) -> float:
    host = host_of(url)
    if host in TRUSTED_DOMAINS:
        return TRUSTED_DOMAINS[host]
    for domain, score in TRUSTED_DOMAINS.items():
        if host.endswith("." + domain):
            return score
    return 0.75 if host.endswith(".gov") else 0.65 if host.endswith(".edu") else 0.4
