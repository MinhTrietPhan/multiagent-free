from unittest.mock import Mock

from hub.agents.research.controller.decision_engine import LayaDecisionEngine
from hub.agents.research.controller.policy import choose_action
from hub.agents.research.controller.pipeline import ResearchPipeline
from hub.agents.research.processors.deduplicate import deduplicate
from hub.agents.research.ranking.engine import rank_articles
from hub.agents.research.state import Article, ResearchState
from hub.agents.research.collectors.fetch import validate_public_url


def test_laya_adapter_uses_typed_choice_without_gemini():
    router = Mock()
    router.predict.return_value = {
        "answers": {"next_action": {"choice": "FETCH_NEXT", "confidence": 0.81}}
    }
    engine = LayaDecisionEngine(router=router)
    action, confidence = engine.choose_action({"query": "AI news"})
    assert action == "FETCH_NEXT"
    assert confidence == 0.81
    router.predict.assert_called_once()


def test_laya_scores_multiple_articles_in_one_predict_call():
    router = Mock()
    router.predict.return_value = {
        "answers": {
            "article_0": {"score": 4.0},
            "article_1": {"score": 2.0},
        }
    }
    engine = LayaDecisionEngine(router=router)
    scores = engine.score_articles([
        {"url": "https://a.test", "title": "A"},
        {"url": "https://b.test", "title": "B"},
    ])
    assert scores == {"https://a.test": 100.0, "https://b.test": 50.0}
    router.predict.assert_called_once()


def test_shadow_policy_logs_laya_but_uses_safe_python_policy():
    state = ResearchState(query="AI", pending_urls=[{"url": "https://example.com"}], max_pages=2)
    engine = Mock()
    engine._unavailable = False
    engine.choose_action.return_value = ("STOP", 0.99)
    action, prediction = choose_action(state, engine, mode="shadow")
    assert action == "FETCH_NEXT"
    assert prediction == "STOP"
    assert state.actions


def test_active_policy_cannot_fetch_when_budget_exhausted():
    state = ResearchState(query="AI", pending_urls=[{"url": "https://example.com"}],
                          fetched_count=1, max_pages=1)
    engine = Mock()
    engine._unavailable = False
    engine.choose_action.return_value = ("FETCH_NEXT", 0.99)
    action, _ = choose_action(state, engine, mode="active")
    assert action == "STOP"


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


def test_localhost_fetch_is_blocked_before_dns():
    try:
        validate_public_url("http://localhost/admin")
    except ValueError as exc:
        assert "blocked" in str(exc).lower()
    else:
        raise AssertionError("localhost must be blocked")


class FakeSearcher:
    def __init__(self):
        self.calls = 0

    def search(self, query):
        self.calls += 1
        if self.calls == 1:
            return [
                {"url": "https://reuters.com/a", "title": "AI company releases new model", "snippet": "Launch"},
                {"url": "https://bbc.com/b", "title": "AI company releases new model", "snippet": "Details"},
                {"url": "https://example.org/c", "title": "A different technology story", "snippet": "News"},
            ]
        return []


def test_pipeline_runs_without_laya_or_gemini(monkeypatch):
    monkeypatch.setenv("HUB_RESEARCH_LAYA_MODE", "off")
    searcher = FakeSearcher()
    pipeline = ResearchPipeline(searcher=searcher,
                                fetcher=lambda url: ("article body " * 100, url))
    pipeline.max_rounds = 1
    pipeline.max_pages = 2
    state, ranked = pipeline.run("AI news today")
    assert state.fetched_count == 2
    assert len(ranked) == 2
    assert searcher.calls == 1
    assert all(item.final_score >= 0 for item in ranked)
    assert any("bbc.com" in item.corroborating_sources for item in ranked)



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


def test_extract_text_rejects_captcha_or_cloudflare_challenge():
    from hub.agents.research.collectors.fetch import FetchError, extract_text

    try:
        extract_text("<html><body>Checking your browser before accessing</body></html>")
    except FetchError as exc:
        assert exc.category == "access_challenge"
    else:
        raise AssertionError("anti-bot challenge pages must not be treated as article text")
