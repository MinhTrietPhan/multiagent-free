import pytest

from hub.agents.research.collectors.search import (
    WebSearchClient,
    _merge,
    _parse_rss,
)

RSS = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <item>
    <title>Tin A - VnExpress</title>
    <link>https://news.google.com/rss/articles/abc?oc=5</link>
    <pubDate>Wed, 19 Nov 2026 08:00:00 GMT</pubDate>
    <description>&lt;a href="https://vnexpress.net/a"&gt;Tin A&lt;/a&gt;</description>
    <source url="https://vnexpress.net">VnExpress</source>
  </item>
  <item>
    <title>Tin B</title>
    <link>https://news.google.com/rss/articles/def</link>
    <description>Tin B</description>
  </item>
</channel></rss>"""


def test_parse_rss_returns_rows_and_strips_publisher_suffix():
    rows = _parse_rss(RSS)
    assert len(rows) == 2
    assert rows[0]["title"] == "Tin A"
    assert rows[0]["url"] == "https://news.google.com/rss/articles/abc?oc=5"
    assert rows[0]["published_at"].startswith("2026-11-19T08:00:00")
    assert rows[0]["backend"] == "rss"
    assert rows[0]["publisher_url"] == "https://vnexpress.net"
    assert rows[1]["published_at"] is None


def test_parse_rss_rejects_malformed_xml():
    with pytest.raises(ValueError):
        _parse_rss(b"<rss><channel>")


def test_merge_deduplicates_urls_and_titles_across_backends():
    rows = [
        {"url": "https://a.com/story?utm_source=x", "title": "OpenAI announces new model"},
        {"url": "https://a.com/story", "title": "OpenAI announces new model", "backend": "rss"},
        {"url": "https://b.com/other", "title": "OpenAI announces new model"},
    ]
    merged = _merge(rows)
    assert len(merged) == 1
    assert merged[0]["url"] == "https://a.com/story"


def test_client_uses_rss_backend_without_network():
    client = WebSearchClient(backends=["rss"], rss_fetcher=lambda url, params: RSS)
    rows = client.search("tin hot")
    assert len(rows) == 2
    assert client.errors == []


def test_client_records_backend_error_and_returns_empty_list():
    def boom(_url, _params):
        raise RuntimeError("blocked")

    client = WebSearchClient(backends=["rss"], rss_fetcher=boom)
    assert client.search("tin hot") == []
    assert client.errors and "rss" in client.errors[0]


def test_client_ignores_unknown_backends():
    client = WebSearchClient(backends=["nope"], rss_fetcher=lambda *_: RSS)
    assert client.backends == ["ddgs"]
