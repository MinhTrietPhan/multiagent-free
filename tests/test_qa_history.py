"""Tests for the QA conversation memory (verbatim + digest + budget)."""
from langchain_core.messages import AIMessage, HumanMessage

from hub.agents.qa.history import digest_answer, history_messages, render_history


def turn(question, answer, agent="qa"):
    return [HumanMessage(question), AIMessage(answer, name=agent)]


def conversation(turns, current="câu hỏi mới"):
    messages = []
    for item in turns:
        question, answer, agent = item if len(item) == 3 else (*item, "qa")
        messages += turn(question, answer, agent)
    messages.append(HumanMessage(current))
    return {"messages": messages}


def test_newest_turns_are_verbatim_and_older_ones_digested():
    long_answer = "Giải thích chi tiết: " + ("nội dung quan trọng " * 120) + "`src/model.py:L10`"
    old = [("q cũ", "câu trả lời cũ " * 40) for _ in range(4)]
    messages = turn("hỏi 1", long_answer) + turn("hỏi 2", long_answer)
    state = conversation(old + [("hỏi 1", long_answer), ("hỏi 2", long_answer)])

    text = render_history(state)
    # Two newest answers survive at (near) full length, older ones are compressed.
    assert text.count("nội dung quan trọng") > 40
    assert text.count("câu trả lời cũ") < 200
    assert "--- HẾT LỊCH SỬ ---" in text


def test_digest_keeps_citations_and_collapses_code():
    answer = (
        "Đã sửa logic.\n\n```python\n"
        + "\n".join(f"line_{i} = {i}" for i in range(12))
        + "\n```\nXem `src/hub/cli.py:L42` và commit 1234567."
    )
    digest = digest_answer(answer, 400)
    assert "src/hub/cli.py:L42" in digest
    assert "[code 12 dòng]" in digest
    assert "line_7" not in digest


def test_footer_is_never_forwarded():
    answer = "Kết quả đúng.\n\n---\n📂 Project: `C:/x`\ncode bổ sung đã đọc: a.py"
    state = conversation([("hỏi", answer)])
    text = render_history(state)
    assert "Kết quả đúng." in text
    assert "📂 Project" not in text and "code bổ sung" not in text


def test_turns_from_other_agents_are_labelled():
    state = conversation([
        ("q1", "report của code agent", "code"),
        ("q2", "giải thích của qa", "qa"),
    ])
    text = render_history(state)
    assert "[code] report của code agent" in text
    assert "[qa] giải thích của qa" in text


def test_current_question_is_not_part_of_history():
    state = conversation([("q1", "a1")], current="CHỈ_CÂU_HỎI_MỚI")
    text = render_history(state)
    assert "q1" in text
    assert "CHỈ_CÂU_HỎI_MỚI" not in text


def test_no_past_turns_means_no_block():
    assert render_history({"messages": [HumanMessage("chỉ có câu này")]}) == ""
    assert history_messages({"messages": [HumanMessage("chỉ có câu này")]}) == []


def test_total_budget_is_respected():
    turns = [(f"câu hỏi số {i}", "nội dung trả lời " * 200) for i in range(40)]
    text = render_history(conversation(turns))
    assert len(text) <= 8000
    assert "lượt cũ hơn đã lược bớt" in text
    assert "câu hỏi số 39" in text        # newest survives


def test_budget_env_is_honoured(monkeypatch):
    turns = [(f"q{i}", "a" * 900) for i in range(30)]
    monkeypatch.setenv("HUB_QA_HISTORY_CHARS", "2000")
    text = render_history(conversation(turns))
    assert len(text) <= 2100
    # Degrades to the compact form instead of blowing the budget.
    assert "lượt cũ hơn đã lược bớt" in text


def test_turn_cap_env(monkeypatch):
    monkeypatch.setenv("HUB_QA_HISTORY_TURNS", "3")
    turns = [(f"q{i}", f"a{i}") for i in range(10)]
    text = render_history(conversation(turns))
    assert "q9" in text and "q7" in text
    assert "q6" not in text


def test_zero_verbatim_turns_still_works(monkeypatch):
    monkeypatch.setenv("HUB_QA_VERBATIM_TURNS", "0")
    state = conversation([("q1", "một câu trả lời dài " * 60)])
    text = render_history(state)
    assert "q1" in text and len(text) < 3000


def test_long_verbatim_answer_is_capped():
    state = conversation([("q", "X" * 20000)])
    text = render_history(state, total_chars=3000)
    assert len(text) <= 3100
    assert "cắt bớt" in text or "…" in text
