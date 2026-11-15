from hub.cli import _terminal_markdown
from hub.interfaces.telegram.bot import clean_agent_output, render_telegram_html


def test_terminal_markdown_formats_headings_bold_and_tables():
    out = _terminal_markdown("## Tiêu đề\n**Quan trọng**\n| A | B |\n|---|---|\n| 1 | 2 |", False)
    assert "Tiêu đề" in out
    assert "Quan trọng" in out
    assert "A  │  B" in out
    assert "1  │  2" in out
    assert "##" not in out
    assert "**" not in out


def test_telegram_markdown_renders_html_and_table_as_mobile_rows():
    out = render_telegram_html("## Tiêu đề\n\n**Đậm** và `code`\n\n| Agent | Model |\n|---|---|\n| QA | Gemini |\n\n```python\nx = 1\n```")
    assert "<b>Tiêu đề</b>" in out
    assert "<b>Đậm</b>" in out
    assert "<code>code</code>" in out
    assert "Agent · Model" in out
    assert "QA · Gemini" in out
    assert "<pre><code>x = 1</code></pre>" in out


def test_telegram_output_filters_internal_need_file_payload():
    assert "need_file" not in clean_agent_output('{"need_file": "src/main.py"}')
    ordinary = '{"answer": "42"}'
    assert clean_agent_output(ordinary) == ordinary
