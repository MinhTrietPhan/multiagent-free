import json
from types import SimpleNamespace

from hub.agents.research.controller.planner import ResearchPlan
from hub.agents.research.processors.synthesize import (
    ArticleSummarizer,
    SummaryItem,
    first_sentences,
)
from hub.agents.research.reporting.formatter import format_report
from hub.agents.research.state import Article, ResearchState


class FakeLLM:
    def __init__(self, payload=None, error=None):
        self.payload = payload
        self.error = error
        self.calls = 0

    def invoke(self, _prompt):
        self.calls += 1
        if self.error:
            raise self.error
        return SimpleNamespace(content=self.payload)


def _articles(count=6):
    return [
        Article(url=f"https://news{index}.com/story-{index}",
                title=f"Unique topic {index} marker{index}",
                source=f"news{index}.com",
                content=f"Đây là nội dung của bài số {index}. " * 30,
                published_at="2026-11-19T08:00:00+00:00")
        for index in range(count)
    ]


def _summary(url, text="Tóm tắt đủ dài để vượt qua kiểm tra tối thiểu của bộ lọc nội dung."):
    return {"url": url, "headline": "Headline", "summary": text,
            "why": "nguồn rõ ràng", "confidence": "high"}


def test_unknown_urls_are_dropped_and_items_capped_at_five():
    articles = _articles(6)
    payload = {"items": [_summary("https://evil.example/fake")] +
                         [_summary(article.url) for article in articles],
               "dropped": []}
    fake = FakeLLM(json.dumps(payload))
    summarizer = ArticleSummarizer(llm_factory=lambda _agent: fake, enabled=True, top_items=5)

    items, dropped = summarizer.summarize("tin hot", articles)

    assert len(items) == 5
    assert all(item.url.startswith("https://news") for item in items)
    assert any(entry["reason"] == "unknown-url" for entry in dropped)


def test_llm_failure_falls_back_to_whole_sentences():
    articles = _articles(2)
    articles[0].content = ("Câu đầu tiên đầy đủ. Câu thứ hai cũng đầy đủ. "
                           "Câu thứ ba không cần. " * 5)
    fake = FakeLLM(error=RuntimeError("quota"))
    summarizer = ArticleSummarizer(llm_factory=lambda _agent: fake, enabled=True)

    items, dropped = summarizer.summarize("tin hot", articles)

    assert dropped == []
    assert items
    assert "Câu đầu tiên" in items[0].summary
    assert "Câu thứ hai" in items[0].summary
    assert "Câu thứ ba" not in items[0].summary


def test_disabled_llm_makes_zero_model_calls():
    def factory(_agent):
        raise AssertionError("the model must not be called when disabled")

    summarizer = ArticleSummarizer(llm_factory=factory, enabled=False)
    items, _dropped = summarizer.summarize("tin hot", _articles(2))
    assert items
    assert all(item.summary for item in items)


def test_same_story_is_summarised_once():
    articles = _articles(2)
    articles[1].title = articles[0].title
    summarizer = ArticleSummarizer(enabled=False)
    items, _dropped = summarizer.summarize("tin hot", articles)
    assert len(items) == 1


def test_first_sentences_never_cuts_mid_word():
    text = ("một " * 400).strip() + " kết thúc"
    result = first_sentences(text, count=2, max_chars=200)
    assert len(result) <= 220
    assert result.endswith("…")
    assert not result.endswith("mộ…")


def test_report_stays_within_budget_and_reports_filtering(monkeypatch):
    monkeypatch.setenv("HUB_RESEARCH_REPORT_CHARS", "800")
    state = ResearchState(query="tin hot hôm nay",
                          plan=ResearchPlan(queries=["q1", "q2"]),
                          fetched_count=6, max_pages=12, stop_reason="đủ 5 câu chuyện")
    items = [SummaryItem(url=f"https://news{i}.com/a", headline=f"Tin số {i}",
                         summary="Nội dung tóm tắt " * 10, confidence="high",
                         domain=f"news{i}.com", sources=[f"news{i}.com"],
                         independent=2)
             for i in range(5)]
    report = format_report(state, items, [{"url": "a", "reason": "duplicate"},
                                          {"url": "b", "reason": "off-topic"}])
    assert len(report) <= 800
    assert "Đã lọc:" in report
    assert "1 trùng lặp" in report
    assert "Research: tin hot hôm nay" in report
