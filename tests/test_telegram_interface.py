from hub.interfaces.telegram.bot import parse_agent_message, split_telegram_text


def test_parse_agent_message():
    names = {"qa", "research", "search", "context"}
    assert parse_agent_message("/qa giải thích graph", names) == ("qa", "giải thích graph")
    assert parse_agent_message("/research@my_agent_bot AI news", names) == ("research", "AI news")
    assert parse_agent_message("giải thích graph", names) == (None, "giải thích graph")
    assert parse_agent_message("/unknown x", names) == (None, "/unknown x")


def test_split_telegram_text_preserves_content_and_limit():
    source = ("abc def\n" * 2000).strip()
    chunks = split_telegram_text(source, limit=100)
    assert len(chunks) > 1
    assert all(len(chunk) <= 100 for chunk in chunks)
    assert "".join(chunks).replace(" ", "").replace("\n", "") == source.replace(" ", "").replace("\n", "")


def test_split_telegram_text_empty():
    assert split_telegram_text("   ") == ["Agent Hub không trả về nội dung."]
