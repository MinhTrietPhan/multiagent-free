from io import StringIO

from hub.cli import _paint, _wrap_reply, print_agent_reply


def test_wrap_reply_keeps_paragraphs_and_bullets():
    lines = _wrap_reply("Tiêu đề\n\n- Một mục có nội dung dài\n- Mục thứ hai", width=48)
    assert lines[0] == "Tiêu đề"
    assert "" in lines
    assert any(line.startswith("- ") for line in lines)


def test_paint_can_be_disabled():
    assert _paint("Bạn", "cyan", enabled=False) == "Bạn"


def test_agent_reply_has_clear_agent_header_and_separate_body():
    stream = StringIO()
    print_agent_reply("qa", "Câu trả lời\n\n- Ý thứ nhất", stream=stream)
    output = stream.getvalue()
    assert "AGENT · QA" in output
    assert "Câu trả lời" in output
    assert "╭─" in output and "╰" in output
