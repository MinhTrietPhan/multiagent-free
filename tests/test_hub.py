from langchain_core.messages import AIMessage, HumanMessage
from langgraph.types import Command

from hub import build_hub
from hub.agents.base import AgentSpec
from hub.agents.context import SPEC as context_spec
from hub.cli import run_turn


class FakeLLM:
    """Stands in for Gemini: routes by keyword and counts calls."""
    calls = 0

    def with_structured_output(self, schema):
        outer = self

        class _R:
            def invoke(self, prompt):
                outer.calls += 1
                req = prompt.split("Yêu cầu của người dùng:")[1]
                return schema(agent="echo" if "echo" in req else "context")
        return _R()


def _echo(state):
    return {"messages": [AIMessage("echo-agent reply")]}


def test_single_agent_no_llm_call_and_menu(tmp_path, monkeypatch):
    (tmp_path / "a.py").write_text("def f():\n    return 1\n")
    monkeypatch.chdir(tmp_path)
    app = build_hub([context_spec], llm_factory=lambda: (_ for _ in ()).throw(AssertionError("LLM called")))
    cfg = {"configurable": {"thread_id": "t"}}
    said = []
    reply = run_turn(app, "tạo context cho dự án .", cfg, ask=lambda _: "1", say=said.append)
    assert "Nhập 1/2/3" in said[0]            # asked the fixed menu
    assert "mode=map" in reply
    assert "def f()" in (tmp_path / "PROJECT_CONTEXT.md").read_text()


def test_mode_keyword_skips_menu(tmp_path, monkeypatch):
    (tmp_path / "a.py").write_text("x = 1\n")
    monkeypatch.chdir(tmp_path)
    app = build_hub([context_spec])
    cfg = {"configurable": {"thread_id": "t2"}}
    result = app.invoke({"messages": [HumanMessage("context full cho .")]}, cfg)
    assert "__interrupt__" not in result and "mode=full" in result["messages"][-1].content


def test_adding_second_agent_routes_by_llm(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    echo = AgentSpec("echo", "Lặp lại lời người dùng", _echo)
    llm = FakeLLM()
    app = build_hub([context_spec, echo], llm_factory=lambda: llm)
    out = app.invoke({"messages": [HumanMessage("hãy echo giúp tôi")]}, {"configurable": {"thread_id": "t3"}})
    assert out["messages"][-1].content == "echo-agent reply" and llm.calls == 1
