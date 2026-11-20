from hub.agents.research.processors.deduplicate import (
    content_fingerprint,
    deduplicate,
    independent_source_count,
    same_story,
)
from hub.agents.research.ranking.engine import rank_articles
from hub.agents.research.state import Article
from hub.agents.research.collectors.fetch import validate_public_url


def test_deduplicate_removes_tracking_and_near_duplicate_titles():
    rows = [
        {"url": "https://example.com/story?utm_source=x", "title": "OpenAI announces new model today"},
        {"url": "https://example.com/story?utm_source=y", "title": "OpenAI announces new model today"},
        {"url": "https://other.com/x", "title": "OpenAI announces new model today"},
        {"url": "https://other.com/y", "title": "NVIDIA announces new GPU"},
    ]
    result = deduplicate(rows)
    assert len(result) == 2
    assert result[0]["url"] == "https://example.com/story"


def test_ranking_is_deterministic_and_exposes_heuristic_score():
    articles = [
        Article(url="https://unknown.example/a", title="A", published_at=None, content="short"),
        Article(url="https://reuters.com/b", title="B", published_at="2026-11-15T10:00:00+00:00",
                content="x" * 5000, corroborating_sources=["reuters.com", "bbc.com", "apnews.com"]),
    ]
    ranked = rank_articles(articles)
    assert ranked[0].title == "B"
    assert all(0 <= item.final_score <= 100 for item in ranked)


def test_relevance_beats_a_fresher_off_topic_article():
    on_topic = Article(url="https://reuters.com/a", title="OpenAI releases new model",
                       published_at="2026-11-18T10:00:00+00:00", content="model " * 400)
    off_topic = Article(url="https://example.com/b", title="Local football results",
                        published_at="2026-11-19T09:00:00+00:00", content="football " * 400)
    ranked = rank_articles([on_topic, off_topic], terms=["openai", "model"])
    assert ranked[0].url == "https://reuters.com/a"


def test_localhost_fetch_is_blocked_before_dns():
    try:
        validate_public_url("http://localhost/admin")
    except ValueError as exc:
        assert "blocked" in str(exc).lower()
    else:
        raise AssertionError("localhost must be blocked")


def test_same_story_and_fingerprint_helpers():
    assert same_story("OpenAI announces new model", "OpenAI announces a new model")
    left = Article(url="https://a.com/x", title="T", content="ban tin " * 200)
    right = Article(url="https://b.com/x", title="T", content="ban tin " * 200)
    left.fingerprint = content_fingerprint(left.content)
    right.fingerprint = content_fingerprint(right.content)
    assert left.fingerprint and left.fingerprint == right.fingerprint
    # A syndicated copy must not count as a second independent source.
    assert independent_source_count([left, right]) == 1


def test_fetch_retries_rate_limit_then_succeeds(monkeypatch):
    from hub.agents.research.collectors import fetch as fetch_module

    class FakeResponse:
        def __init__(self, status_code, body=b"ok"):
            self.status_code = status_code
            self.headers = {"Content-Type": "text/html", "Retry-After": "0"}
            self.encoding = "utf-8"
            self.is_redirect = False
            self.is_permanent_redirect = False
            self.body = body
            self.closed = False

        def close(self):
            self.closed = True

        def iter_content(self, _chunk_size):
            yield self.body

        def raise_for_status(self):
            if self.status_code >= 400:
                import requests
                raise requests.HTTPError(f"HTTP {self.status_code}")

    class FakeSession:
        def __init__(self):
            self.responses = [FakeResponse(429), FakeResponse(200, b"<html>ok</html>")]
            self.calls = 0

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def get(self, *_args, **_kwargs):
            self.calls += 1
            return self.responses.pop(0)

    session = FakeSession()
    monkeypatch.setattr(fetch_module, "validate_public_url", lambda url: url)
    monkeypatch.setattr(fetch_module.time, "sleep", lambda _seconds: None)
    import requests
    monkeypatch.setattr(requests, "Session", lambda: session)

    html, final_url = fetch_module.fetch_html("https://example.com")
    assert html == "<html>ok</html>"
    assert final_url == "https://example.com"
    assert session.calls == 2


def test_fetch_does_not_retry_or_bypass_forbidden_status(monkeypatch):
    from hub.agents.research.collectors import fetch as fetch_module

    class FakeResponse:
        status_code = 403
        headers = {}
        is_redirect = False
        is_permanent_redirect = False
        closed = False

        def close(self):
            self.closed = True

    class FakeSession:
        calls = 0

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def get(self, *_args, **_kwargs):
            self.calls += 1
            return FakeResponse()

    session = FakeSession()
    monkeypatch.setattr(fetch_module, "validate_public_url", lambda url: url)
    import requests
    monkeypatch.setattr(requests, "Session", lambda: session)

    from hub.agents.research.collectors.fetch import FetchError
    try:
        fetch_module.fetch_html("https://example.com")
    except FetchError as exc:
        assert exc.category == "access_denied"
        assert exc.status_code == 403
    else:
        raise AssertionError("403 must be reported as an access restriction")
    assert session.calls == 1


def test_fetch_article_uses_browser_for_thin_js_page(monkeypatch):
    from hub.agents.research.collectors import fetch as fetch_module

    monkeypatch.setattr(fetch_module, "fetch_html", lambda _url: ("<html>shell</html>", "https://example.com"))
    monkeypatch.setattr(fetch_module, "extract_text", lambda _html: "thin")
    monkeypatch.setattr(
        fetch_module, "_fetch_with_browser", lambda _url: ("rendered article " * 30, "https://example.com/article")
    )
    monkeypatch.setenv("HUB_RESEARCH_BROWSER_FALLBACK", "true")

    text, final_url = fetch_module.fetch_article("https://example.com")
    assert len(text) >= 200
    assert final_url.endswith("/article")


def test_extract_published_prefers_json_ld_then_meta():
    from hub.agents.research.collectors.fetch import extract_published

    json_ld = ('<script type="application/ld+json">'
               '{"@type":"NewsArticle","datePublished":"2026-11-19T08:30:00Z"}'
               '</script>')
    assert extract_published(json_ld).startswith("2026-11-19T08:30:00")
    meta = '<meta property="article:published_time" content="2026-11-18T10:00:00+00:00">'
    assert extract_published(meta).startswith("2026-11-18T10:00:00")


def test_extract_text_rejects_captcha_or_cloudflare_challenge():
    from hub.agents.research.collectors.fetch import FetchError, extract_text

    try:
        extract_text("<html><body>Checking your browser before accessing</body></html>")
    except FetchError as exc:
        assert exc.category == "access_challenge"
    else:
        raise AssertionError("anti-bot challenge pages must not be treated as article text")
