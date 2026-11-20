"""Tests for the QA agent."""
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from langchain_core.messages import AIMessage, HumanMessage

from hub import build_hub
from hub.agents.base import AgentSpec
from hub.agents.qa import SPEC as qa_spec
from hub.agents.qa.node import qa_node, _parse_need_file
from hub.agents.qa.tools import read_file_content, read_function


# ---------------------------------------------------------------------------
# Unit tests for helpers
# ---------------------------------------------------------------------------

def test_parse_need_file_valid_json():
    assert _parse_need_file('{"need_file": "src/model.py"}') == {"need_file": "src/model.py"}


def test_parse_need_file_with_function():
    text = '{"need_file": "src/model.py", "function": "train"}'
    result = _parse_need_file(text)
    assert result == {"need_file": "src/model.py", "function": "train"}


def test_parse_need_file_wrapped_in_code_block():
    text = '```json\n{"need_file": "utils.py"}\n```'
    result = _parse_need_file(text)
    assert result == {"need_file": "utils.py"}


def test_parse_need_file_normal_answer():
    text = "Hàm `train()` nằm ở file `model.py:45`."
    assert _parse_need_file(text) is None


def test_read_file_content(tmp_path):
    f = tmp_path / "hello.py"
    f.write_text("def hello():\n    print('hi')\n", encoding="utf-8")
    content = read_file_content(str(tmp_path), "hello.py")
    assert "L1: def hello():" in content
    assert "L2:" in content


def test_read_file_content_not_found(tmp_path):
    assert read_file_content(str(tmp_path), "nope.py") is None


def test_read_function(tmp_path):
    code = """\
import os

def foo():
    \"\"\"Do foo.\"\"\"
    return 1

def bar(x, y):
    return x + y

class MyClass:
    def method(self):
        pass
"""
    (tmp_path / "example.py").write_text(code, encoding="utf-8")
    result = read_function(str(tmp_path), "example.py", "bar")
    assert result is not None
    assert "def bar(x, y):" in result
    assert "return x + y" in result


def test_read_function_not_found(tmp_path):
    (tmp_path / "example.py").write_text("def foo(): pass\n", encoding="utf-8")
    assert read_function(str(tmp_path), "example.py", "nope") is None


# ---------------------------------------------------------------------------
# Integration test: QA node with mock LLM
# ---------------------------------------------------------------------------

def test_qa_node_without_context_file_uses_on_the_fly_index(tmp_path, monkeypatch):
    """QA builds its own index; no PROJECT_CONTEXT file is required or read."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "pyproject.toml").write_text("[project]\nname='sample'\n", encoding="utf-8")
    (tmp_path / "model.py").write_text("def train():\n    return 'trained'\n", encoding="utf-8")
    mock_response = MagicMock(content="Hàm train nằm trong model.py.")
    mock_llm = MagicMock()
    mock_llm.invoke.return_value = mock_response
    with patch("hub.agents.qa.node.get_llm", return_value=mock_llm):
        result = qa_node({"messages": [HumanMessage("hàm nào xử lý train?")]})
    msg = result["messages"][0].content
    assert "Hàm train" in msg
    assert "🗂️" in msg
    assert "Không có PROJECT_CONTEXT" not in msg
    sent = "\n".join(m.content for m in mock_llm.invoke.call_args[0][0])
    assert "model.py" in sent and "CHỈ MỤC DỰ ÁN" in sent


def test_qa_node_index_replaces_project_context(tmp_path, monkeypatch):
    """An existing (possibly stale) PROJECT_CONTEXT.md is not loaded automatically."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "pyproject.toml").write_text("[project]\nname='sample'\n", encoding="utf-8")
    (tmp_path / "model.py").write_text("def train():\n    return 'trained'\n", encoding="utf-8")
    (tmp_path / "PROJECT_CONTEXT.md").write_text("STALE_MARKER=1", encoding="utf-8")

    mock_llm = MagicMock()
    mock_llm.invoke.return_value = MagicMock(
        content="Hàm `train()` nằm ở `model.py:L1` dùng để huấn luyện model."
    )
    with patch("hub.agents.qa.node.get_llm", return_value=mock_llm):
        result = qa_node({"messages": [HumanMessage("hàm nào để train model?")]})

    msg = result["messages"][0].content
    assert "train()" in msg
    assert "🗂️" in msg
    sent = "\n".join(m.content for m in mock_llm.invoke.call_args[0][0])
    assert "STALE_MARKER" not in sent
    assert "model.py" in sent


def test_qa_node_fetches_extra_code(tmp_path, monkeypatch):
    """QA agent should fetch extra code when LLM requests it."""
    monkeypatch.chdir(tmp_path)

    # The project only needs a marker file; QA builds the index itself.
    (tmp_path / "pyproject.toml").write_text("[project]\nname='demo'\n", encoding="utf-8")
    (tmp_path / "math_utils.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")

    # First call: LLM says it needs more code
    need_response = MagicMock()
    need_response.content = '{"need_file": "math_utils.py"}'

    # Second call: LLM gives a real answer
    answer_response = MagicMock()
    answer_response.content = "Hàm `add(a, b)` ở `math_utils.py:L1` cộng hai số."

    mock_llm = MagicMock()
    mock_llm.invoke.side_effect = [need_response, answer_response]

    with patch("hub.agents.qa.node.get_llm", return_value=mock_llm):
        state = {"messages": [HumanMessage("hàm nào cộng hai số?")]}
        result = qa_node(state)

    msg = result["messages"][0].content
    assert "add(a, b)" in msg
    assert mock_llm.invoke.call_count == 2  # called twice: once for need_file, once for answer
    assert "code bổ sung" in msg  # source should mention extra code


def test_qa_spec_registered():
    """QA agent should be in the global registry."""
    from hub.registry import AGENTS
    names = [a.name for a in AGENTS]
    assert "qa" in names
    assert "context" in names
