from hub.agents.research.controller.pipeline import ResearchPipeline
from hub.agents.research.controller.planner import ResearchPlan
from hub.agents.research.controller.policy import evidence_sufficient
from hub.agents.research.processors.deduplicate import independent_source_count
from hub.agents.research.processors.synthesize import ArticleSummarizer
from hub.agents.research.state import Article, ResearchState


class FakePlanner:
    def __init__(self, queries, intent="digest", domains=None, recency_days=7):
        self._plan = ResearchPlan(queries=list(queries), intent=intent,
                                  domains=list(domains or []), recency_days=recency_days,
                                  origin="test")

    def plan(self, _query):
        return self._plan


class FakeSearcher:
    def __init__(self, batches):
        self.batches = [list(batch) for batch in batches]
        self.calls = []
        self.errors = []

    def search(self, query):
        self.calls.append(query)
        return self.batches.pop(0) if self.batches else []


class FakeFetcher:
    def __init__(self, error_urls=(), fixed_content=None):
        self.error_urls = set(error_urls)
        self.fixed_content = fixed_content
        self.urls = []

    def __call__(self, url):
        self.urls.append(url)
        if url in self.error_urls:
            raise RuntimeError("network down")
        body = self.fixed_content or f"{url} nội dung bài viết chi tiết. " * 40
        return body, url, "2026-11-19T08:00:00+00:00"


def _row(url, title, published="2026-11-19T07:00:00+00:00"):
    return {"url": url, "title": title, "snippet": title, "published_at": published}


def _pipeline(searcher, fetcher, planner, **overrides):
    pipeline = ResearchPipeline(
        searcher=searcher,
        fetcher=fetcher,
        planner=planner,
        summarizer=ArticleSummarizer(enabled=False),
    )
    pipeline.workers = 1
    for key, value in overrides.items():
        setattr(pipeline, key, value)
    return pipeline


def test_digest_rounds_until_five_distinct_stories_from_two_domains():
    rows = [
        _row("https://a.com/1", "Story one about chips"),
        _row("https://b.com/1", "Story two about phones"),
        _row("https://a.com/2", "Story three about robots"),
        _row("https://b.com/2", "Story four about batteries"),
        _row("https://c.com/1", "Story five about satellites"),
        _row("https://c.com/2", "Story six about software"),
    ]
    searcher = FakeSearcher([rows, []])
    fetcher = FakeFetcher()
    pipeline = _pipeline(searcher, fetcher,
                         FakePlanner(["q1", "q2", "q3"]),
                         min_items=5, max_pages=6, pages_per_round=4, max_search_rounds=3)

    state, items, _dropped = pipeline.run("tin hot hôm nay")

    assert state.fetched_count == 6
    assert len(state.articles) == 6
    assert len(set(fetcher.urls)) == 6          # never fetches the same URL twice
    assert state.search_rounds == 2
    assert "đủ" in state.stop_reason and "domain" in state.stop_reason
    assert items                               # extractive fallback still produces items


def test_verify_rounds_until_two_independent_sources():
    rows = [
        _row("https://reuters.com/x", "OpenAI releases GPT-6 model"),
        _row("https://bbc.com/x", "OpenAI releases GPT-6 model"),
    ]
    searcher = FakeSearcher([rows])
    fetcher = FakeFetcher()
    pipeline = _pipeline(searcher, fetcher,
                         FakePlanner(["claim check"], intent="verify"),
                         max_pages=4, pages_per_round=4, max_search_rounds=3)

    state, _items, _dropped = pipeline.run("Có đúng OpenAI vừa ra mắt GPT-6?")

    assert state.fetched_count == 2
    assert "nguồn độc lập" in state.stop_reason
    assert state.evidence_level == "corroborated"
    assert len(searcher.calls) >= 2
    assert searcher.calls[1].startswith('"')


def test_syndicated_copy_is_not_counted_as_a_second_source():
    rows = [
        _row("https://reuters.com/x", "OpenAI releases GPT-6 model"),
        _row("https://vietnamnet.vn/x", "OpenAI releases GPT-6 model"),
    ]
    searcher = FakeSearcher([rows, []])
    fetcher = FakeFetcher(fixed_content="bản tin gốc " * 100)
    pipeline = _pipeline(searcher, fetcher,
                         FakePlanner(["claim check"], intent="verify"),
                         max_pages=4, pages_per_round=4, max_search_rounds=3)

    state, _items, _dropped = pipeline.run("Có đúng OpenAI vừa ra mắt GPT-6?")

    assert state.fetched_count == 2
    assert independent_source_count(state.articles) == 1
    assert state.evidence_level == "single_source"
    assert "hết truy vấn" in state.stop_reason


def test_page_budget_limits_fetching():
    rows = [_row(f"https://a.com/{index}", f"Unique topic {index} marker{index}") for index in range(10)]
    searcher = FakeSearcher([rows])
    fetcher = FakeFetcher()
    pipeline = _pipeline(searcher, fetcher,
                         FakePlanner(["q1", "q2", "q3"]),
                         min_items=10, max_pages=4, pages_per_round=4,
                         max_search_rounds=3, max_per_domain=10)

    state, _items, _dropped = pipeline.run("tin hot hôm nay")

    assert state.fetched_count == 4
    assert len(fetcher.urls) == 4
    assert len(searcher.calls) == 1
    assert "đủ 4 trang" in state.stop_reason


def test_deadline_stops_before_any_fetch():
    searcher = FakeSearcher([[_row("https://a.com/1", "Story one")]])
    fetcher = FakeFetcher()
    pipeline = _pipeline(searcher, fetcher, FakePlanner(["q1"]))
    pipeline.deadline_seconds = 0

    state, items, _dropped = pipeline.run("tin hot hôm nay")

    assert state.fetched_count == 0
    assert items == []
    assert "hết thời gian" in state.stop_reason


def test_legacy_laya_env_is_reported_as_obsolete(monkeypatch):
    monkeypatch.setenv("HUB_RESEARCH_LAYA_MODE", "shadow")
    pipeline = _pipeline(FakeSearcher([]), FakeFetcher(), FakePlanner(["q1"]))
    pipeline.deadline_seconds = 0

    state, _items, _dropped = pipeline.run("tin hot hôm nay")

    assert any("đã bỏ" in warning for warning in state.warnings)


def test_fetch_error_is_recorded_but_does_not_kill_the_round():
    rows = [
        _row("https://a.com/broken", "Broken story about chips"),
        _row("https://b.com/fine", "Working story about phones"),
    ]
    searcher = FakeSearcher([rows, []])
    fetcher = FakeFetcher(error_urls={"https://a.com/broken"})
    pipeline = _pipeline(searcher, fetcher,
                         FakePlanner(["q1", "q2"]),
                         max_pages=2, pages_per_round=2, max_search_rounds=2)

    state, _items, _dropped = pipeline.run("tin hot hôm nay")

    assert state.fetched_count == 2
    assert any("Tải lỗi" in warning for warning in state.warnings)
    assert any(article.fetch_error for article in state.articles)


def _article(title, source, content=None, fingerprint=None, url=None):
    article = Article(url=url or f"https://{source}/{title.replace(' ', '-')}",
                      title=title, source=source, content=content or "nội dung " * 60)
    article.fingerprint = fingerprint
    return article


def test_digest_evidence_requires_five_stories():
    state = ResearchState(query="q", intent="digest", min_items=5,
                          articles=[_article(f"Unique topic {i} marker{i}", ["a.com", "b.com"][i % 2])
                                    for i in range(4)])
    assert evidence_sufficient(state)[0] is False
    state.articles.append(_article("Unique topic five marker5", "a.com"))
    assert evidence_sufficient(state)[0] is True


def test_verify_evidence_ignores_identical_fingerprints():
    first = _article("OpenAI releases GPT-6", "reuters.com", fingerprint="same")
    second = _article("OpenAI releases GPT-6", "vietnamnet.vn", fingerprint="same")
    same_copy = ResearchState(query="q", intent="verify", articles=[first, second])
    assert evidence_sufficient(same_copy)[0] is False

    second.fingerprint = "different"
    independent = ResearchState(query="q", intent="verify", articles=[first, second])
    assert evidence_sufficient(independent)[0] is True
