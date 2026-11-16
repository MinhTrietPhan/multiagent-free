"""Tests for the code agent: settings, security, patching, approval flow, repair loop, registry."""
import json
import os
import sys
from pathlib import Path

import pytest
from langchain_core.messages import HumanMessage
from langgraph.types import Command

from hub import build_hub
from hub.agents.base import AgentSpec
from hub.agents.code import settings as cs
from hub.agents.code import tools as ct
from hub.agents.code.graph import build_code_graph, choose_model_key
from hub.agents.code.report import parse_approval
from hub.agents.code.security import (PathDenied, check_read, check_write, is_allowed_command,
                                      is_secret_path, resolve_inside)
from hub.cli import run_turn

ADD_ONLY = "def add(a, b):\n    return a + b\n"


class FakeLLM:
    """Stands in for Gemini; answers by schema. `calls` counts PROPOSAL calls only (explore/review are separate)."""

    def __init__(self, proposals, explores=None, reviews=None):
        self.proposals, self.explores, self.reviews = list(proposals), list(explores or [{}]), list(reviews or [{}])
        self.calls = self.explore_calls = self.review_calls = 0
        self.prompts, self.proposal_prompts, self.keys, self.proposal_keys, self._key = [], [], [], [], None

    def with_structured_output(self, schema):
        outer = self

        class _R:
            def invoke(self, prompt):
                outer.prompts.append(prompt)
                if schema.__name__ == "Explore":
                    outer.explore_calls += 1
                    item = outer.explores[min(outer.explore_calls - 1, len(outer.explores) - 1)]
                elif schema.__name__ == "Review":
                    outer.review_calls += 1
                    item = outer.reviews[min(outer.review_calls - 1, len(outer.reviews) - 1)]
                else:
                    outer.calls += 1
                    outer.proposal_prompts.append(prompt)
                    outer.proposal_keys.append(outer._key)
                    item = outer.proposals[min(outer.calls - 1, len(outer.proposals) - 1)]
                return schema(**item)
        return _R()


def proposal(*edits, plan=("thực hiện",)):
    return {"analysis": "phân tích", "plan": list(plan), "risks": [], "edits": list(edits)}


def modify(old, new, path="src/calc.py"):
    return {"path": path, "action": "modify", "old_str": old, "new_str": new}


ADD_SUB = modify(ADD_ONLY, ADD_ONLY + "\n\ndef sub(a, b):\n    return a - b\n")
BREAK_ADD = modify("return a + b", "return a - b")
FIX_ADD = modify("return a - b", "return a + b")


@pytest.fixture
def project(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    (root / "src").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "pyproject.toml").write_text('[project]\nname = "x"\n')
    (root / "src" / "calc.py").write_text(ADD_ONLY)
    (root / "tests" / "test_calc.py").write_text(
        "from src.calc import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n")
    monkeypatch.chdir(root)
    monkeypatch.setenv("HUB_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("HUB_WORKSPACE_ROOTS", raising=False)
    return root


def make_app(llm, tmp_path):
    def factory(key):
        llm.keys.append(key)
        llm._key = key
        return llm
    spec = AgentSpec("code", "test code agent", build_code_graph(llm_factory=factory, backup_root=tmp_path / "bk"))
    return build_hub([spec], llm_factory=lambda: (_ for _ in ()).throw(AssertionError("supervisor LLM called")))


def cfg(thread="t"):
    return {"configurable": {"thread_id": thread}}


def run(app, text, answers=(), thread="t"):
    said, it = [], iter(answers)
    reply = run_turn(app, text, cfg(thread), ask=lambda _: next(it), say=said.append, forced="code")
    return reply, said


def write_settings(tmp_path, **values):
    d = tmp_path / "data"
    d.mkdir(exist_ok=True)
    (d / "code-agent.json").write_text(json.dumps(values))


# ----------------------------------------------------------------------------- registry
def test_registered_not_parallel_safe():
    from hub.registry import AGENTS
    spec = next(a for a in AGENTS if a.name == "code")
    assert spec.parallel_safe is False
    assert {"qa", "search", "context", "research"} <= {a.name for a in AGENTS}


def test_choose_model_key():
    assert choose_model_key("cheap", 3, "debug", 9) == "code"
    assert choose_model_key("strong", 0, "modify", 0) == "code_strong"
    assert choose_model_key("hybrid", 0, "modify", 1) == "code"
    assert choose_model_key("hybrid", 1, "modify", 1) == "code_strong"
    assert choose_model_key("hybrid", 0, "refactor", 1) == "code_strong"
    assert choose_model_key("hybrid", 0, "modify", 3) == "code_strong"


# ----------------------------------------------------------------------------- settings
def test_settings_defaults_and_roundtrip(tmp_path):
    path = tmp_path / "c.json"
    assert cs.load_settings(path) == cs.CodeSettings()
    cs.save_settings(cs.CodeSettings(max_tool_calls=7), path)
    assert cs.load_settings(path).max_tool_calls == 7


def test_settings_invalid_values_fall_back_with_warnings(tmp_path):
    path = tmp_path / "c.json"
    path.write_text(json.dumps({"approval_mode": "yolo", "max_tool_calls": -5, "auto_run_tests": "maybe",
                                "max_repair_attempts": True, "bogus": 1, "model_strategy": "STRONG"}))
    loaded, warnings = cs.load_settings_checked(path)
    assert loaded.approval_mode == "approve" and loaded.max_tool_calls == 40
    assert loaded.auto_run_tests is True and loaded.max_repair_attempts == 2
    assert loaded.model_strategy == "strong"
    assert len(warnings) == 5
    path.write_text("{not json")
    assert cs.load_settings_checked(path)[0] == cs.CodeSettings()


def test_config_command_rules(tmp_path):
    path = tmp_path / "c.json"
    assert "approval_mode = approve" in cs.handle_config_command("config", path)
    assert cs.handle_config_command("config max_tool_calls 9", path) == "Đã đặt max_tool_calls = 9."
    assert cs.load_settings(path).max_tool_calls == 9
    assert "Không bật" in cs.handle_config_command("config approval_mode=auto", path)
    assert cs.load_settings(path).approval_mode == "approve"
    assert "không hợp lệ" in cs.handle_config_command("config max_tool_calls 0", path)
    assert cs.handle_config_command("config the logger in app.py", path) is None  # normal request
    assert cs.handle_config_command("thêm model CNN", path) is None
    cs.handle_config_command("config reset", path)
    assert cs.load_settings(path) == cs.CodeSettings()


# ----------------------------------------------------------------------------- security
@pytest.mark.parametrize("rel", [".env", ".env.local", "a/.env", "key.pem", "id_rsa", "credentials.json",
                                 "config/secrets.yaml", ".aws/config", "svc/service-account-prod.json"])
def test_secret_paths(rel):
    assert is_secret_path(rel)


@pytest.mark.parametrize("rel", [".env.example", "src/secret_utils.py", "README.md", "tests/test_secrets_helper.py"])
def test_non_secret_paths(rel):
    assert not is_secret_path(rel)


@pytest.mark.parametrize("rel", ["../x.py", "a/../../x.py", "/etc/passwd", "C:/Windows/x", "C:\\x", "a\\..\\..\\x",
                                 "nul", "src/con.py", "a /b.py", "a:b.py", "", "x\x00.py"])
def test_bad_paths_rejected(project, rel):
    with pytest.raises(PathDenied):
        resolve_inside(project, rel)


def test_read_write_policy(project):
    (project / ".env").write_text("KEY=TOPSECRET")
    (project / ".git").mkdir()
    (project / ".git" / "config").write_text("x")
    for rel in (".env", ".git/config", "node_modules/x.js", ".venv/lib/x.py"):
        with pytest.raises(PathDenied):
            check_read(project, rel)
        with pytest.raises(PathDenied):
            check_write(project, rel)
    assert check_write(project, "src/new_file.py").name == "new_file.py"
    guard = project / "src" / "hub" / "agents" / "code"
    guard.mkdir(parents=True)
    with pytest.raises(PathDenied):
        check_write(project, "src/hub/agents/code/security.py")


def test_symlink_escape_rejected(project, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "x.py").write_text("x = 1\n")
    try:
        (project / "link").symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")
    with pytest.raises(PathDenied):
        check_write(project, "link/x.py")


def test_command_allowlist():
    py = sys.executable
    assert is_allowed_command([py, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider"], py)
    assert is_allowed_command([py, "-X", "pycache_prefix=/tmp/x", "-m", "compileall", "-q", "a.py"], py)
    assert is_allowed_command([py, "-m", "ruff", "check", "a.py"], py)
    for bad in ([py, "-c", "print(1)"], [py, "-m", "pip", "install", "x"], ["rm", "-rf", "/"],
                [py, "-m", "pytest", "--co", "-q"], [py, "-m", "compileall", "-q", "../x.py"],
                [py, "-m", "ruff", "check", "--fix", "a.py"], [py, "-m", "compileall", "-q", "/etc"], [py, "-m", "compileall", "-q", "\\etc"],
                [py, "-m", "compileall", "-q", "a\\..\\..\\x.py"], [py, "-m", "ruff", "check", "C:\\x.py"], []):
        assert not is_allowed_command(bad, py)
    assert not is_allowed_command(["other-python", "-m", "pytest", "-q", "-x"], py)


def test_parse_approval_is_strict():
    assert parse_approval("y") == ("yes", None) and parse_approval(" Đồng ý. ") == ("yes", None)
    assert parse_approval("diff 2") == ("diff", 2) and parse_approval("diff") == ("diff", 1)
    for text in ("n", "no", "", "yes please do it later", "áp dụng đi nhé", "/qa hi", "diff x", None):
        assert parse_approval(text)[0] == "no"


# ----------------------------------------------------------------------------- patch building / applying
def test_build_changes_modify_create_and_diff(project):
    changes, errors, _ = ct.build_changes(project, [
        ADD_SUB, {"path": "src/new.py", "action": "create", "content": "X = 1"}])
    assert not errors and [c["action"] for c in changes] == ["modify", "create"]
    assert "+def sub(a, b):" in changes[0]["diff"] and changes[1]["diff"].startswith("--- /dev/null")
    assert changes[1]["new_text"] == "X = 1\n"
    assert (project / "src" / "calc.py").read_text() == ADD_ONLY  # nothing written yet


@pytest.mark.parametrize("edit,fragment", [
    (modify("không có trong file", "x"), "không khớp"),
    (modify("a", "b"), "xuất hiện"),                       # 'a' appears several times
    (modify("", "x"), "old_str"),
    (modify(ADD_ONLY, ADD_ONLY, path="src/missing.py"), "chưa tồn tại"),
    ({"path": "src/calc.py", "action": "create", "content": "x = 1"}, "đã tồn tại"),
    ({"path": "src/n.py", "action": "create", "content": "  \n"}, "rỗng"),
    (modify("return a + b", "return (a +", path="src/calc.py"), "cú pháp"),
    (modify("return a + b", "return a + b"), "không tạo ra thay đổi"),
    (modify("x", "y", path="../evil.py"), "'..'"),
    (modify("x", "y", path=".env"), "nhạy cảm"),
    (modify("x", "y", path=".git/config"), "bỏ qua"),
    ({"path": "nb.ipynb", "action": "create", "content": "{}"}, ".ipynb"),
])
def test_build_changes_rejections(project, edit, fragment):
    (project / ".env").write_text("A=1")
    changes, errors, _ = ct.build_changes(project, [edit])
    assert not changes and any(fragment in e for e in errors), errors


def test_crlf_preserved_and_whole_file_not_rewritten(project, tmp_path):
    target = project / "src" / "calc.py"
    target.write_bytes(b"def add(a, b):\r\n    return a + b\r\n\r\n\r\ndef keep():\r\n    return 1\r\n")
    changes, errors, _ = ct.build_changes(project, [modify("return a + b", "return a + b  # sum")])
    assert not errors
    result = ct.apply_changes(project, changes, tmp_path / "bk")
    assert result.ok
    data = target.read_bytes()
    assert b"# sum" in data and data.count(b"\r\n") == 6 and b"\n" not in data.replace(b"\r\n", b"")
    assert (Path(result.backup_dir) / "src" / "calc.py").exists()


def test_apply_detects_file_changed_since_validation(project, tmp_path):
    changes, _, _ = ct.build_changes(project, [ADD_SUB])
    (project / "src" / "calc.py").write_text(ADD_ONLY + "# user edit\n")
    result = ct.apply_changes(project, changes, tmp_path / "bk")
    assert not result.ok and "đã thay đổi" in result.error
    assert (project / "src" / "calc.py").read_text().endswith("# user edit\n")


def test_apply_rolls_back_when_a_write_fails(project, tmp_path, monkeypatch):
    other = project / "src" / "other.py"
    other.write_text("Y = 1\n")
    changes, errors, _ = ct.build_changes(project, [ADD_SUB, modify("Y = 1", "Y = 2", path="src/other.py")])
    assert not errors
    real, calls = ct._atomic_write, []

    def flaky(target, data):
        calls.append(target)
        if len(calls) == 2:
            raise OSError("disk full")
        real(target, data)
    monkeypatch.setattr(ct, "_atomic_write", flaky)
    result = ct.apply_changes(project, changes, tmp_path / "bk")
    assert not result.ok and "hoàn tác" in result.error
    assert (project / "src" / "calc.py").read_text() == ADD_ONLY and other.read_text() == "Y = 1\n"


def test_create_then_modify_same_new_file(project):
    changes, errors, _ = ct.build_changes(project, [
        {"path": "src/m.py", "action": "create", "content": "A = 1\nB = 2\n"}, modify("B = 2", "B = 3", path="src/m.py")])
    assert not errors and changes[0]["new_text"] == "A = 1\nB = 3\n"


# ----------------------------------------------------------------------------- context
def test_context_budget_secrets_and_priority(project):
    (project / ".env").write_text("GOOGLE_API_KEY=TOPSECRET")
    (project / "src" / "big.py").write_text("\n".join(f"def f{i}():\n    return {i}" for i in range(3000)))
    budget = ct.ToolBudget(25)
    context, shown, _ = ct.build_context(project, "sửa src/big.py và đọc .env, thêm hàm calc", [], 6000, budget)
    assert len(context) <= 6000 and "TOPSECRET" not in context and ".env" not in context.split("</file>")[-1]
    assert shown[0] == "src/big.py" and "bỏ qua dòng" in context
    assert budget.used <= 25


def test_extract_paths_by_unique_basename(project):
    assert ct.extract_paths(project, "trong calc.py hãy thêm hàm") == ["src/calc.py"]


# ----------------------------------------------------------------------------- end-to-end flow
def test_approve_flow_no_write_before_approval_and_single_llm_call(project, tmp_path):
    llm = FakeLLM([proposal(ADD_SUB)])
    app = make_app(llm, tmp_path)
    result = app.invoke({"messages": [HumanMessage("thêm hàm sub vào src/calc.py")], "forced": "code"}, cfg())
    assert "__interrupt__" in result
    prompt = result["__interrupt__"][0].value
    assert "cần duyệt" in prompt and "src/calc.py" in prompt and "+def sub" in prompt and "pytest" in prompt
    assert len(prompt) < 3900
    assert (project / "src" / "calc.py").read_text() == ADD_ONLY          # nothing written before approval
    final = app.invoke(Command(resume="y"), cfg())
    text = final["messages"][-1].content
    assert "def sub" in (project / "src" / "calc.py").read_text()
    assert llm.calls == 1                                                   # resume did NOT call Gemini again
    assert "Đã sửa: src/calc.py" in text and "[OK] python -m pytest" in text and "hoàn tất" in text
    assert final["active_project_root"] == str(project)


@pytest.mark.parametrize("answer", ["n", "cứ làm đi nhé", "", "/qa giải thích"])
def test_anything_but_explicit_yes_cancels(project, tmp_path, answer):
    llm = FakeLLM([proposal(ADD_SUB)])
    reply, _ = run(make_app(llm, tmp_path), "thêm hàm sub vào src/calc.py", [answer])
    assert (project / "src" / "calc.py").read_text() == ADD_ONLY
    assert "đã huỷ" in reply and "Không có file nào bị sửa" in reply


def test_diff_pages_then_approve(project, tmp_path):
    llm = FakeLLM([proposal(ADD_SUB, {"path": "src/new.py", "action": "create", "content": "Z = 1\n"})])
    reply, said = run(make_app(llm, tmp_path), "thêm sub và file new", ["diff", "diff 2", "diff 99", "y"])
    assert any("Diff file 1/2: src/calc.py" in s for s in said)
    assert any("Diff file 2/2: src/new.py" in s for s in said)
    assert (project / "src" / "new.py").exists() and "Đã tạo: src/new.py" in reply
    assert llm.calls == 1


def test_invalid_patch_is_repaired_without_user_seeing_it(project, tmp_path):
    llm = FakeLLM([proposal(modify("def add(x, y):", "def add(x, y):  # sai neo")), proposal(ADD_SUB)])
    reply, said = run(make_app(llm, tmp_path), "thêm sub", ["y"])
    assert llm.calls == 2 and len(said) == 1                                # user approved only the valid patch
    assert "không khớp" in llm.proposal_prompts[1] and "def sub" in (project / "src" / "calc.py").read_text()
    assert "hoàn tất" in reply


def test_failed_tests_trigger_repair_with_model_upgrade(project, tmp_path):
    llm = FakeLLM([proposal(BREAK_ADD), proposal(FIX_ADD)])
    reply, said = run(make_app(llm, tmp_path), "đổi add trong src/calc.py", ["y", "y"])
    assert llm.calls == 2 and len(said) == 2 and "sửa lỗi lần 1" in said[1]
    assert llm.proposal_keys == ["code", "code_strong"]                              # hybrid upgrades only for the repair
    assert "return a + b" in (project / "src" / "calc.py").read_text()
    assert "hoàn tất" in reply and "[OK] python -m pytest" in reply
    assert "assert" in llm.proposal_prompts[1]                                       # pytest output was fed back


def test_repair_attempts_are_bounded(project, tmp_path):
    llm = FakeLLM([proposal(BREAK_ADD)])                                    # keeps proposing a stale/broken patch
    reply, _ = run(make_app(llm, tmp_path), "đổi add trong src/calc.py", ["y"])
    assert llm.calls == 3                                                   # 1 + max_repair_attempts(2), no infinite loop
    assert "CHƯA đạt" in reply and "[LỖI] python -m pytest" in reply


def test_auto_mode_applies_without_interrupt(project, tmp_path):
    write_settings(tmp_path, approval_mode="auto", auto_run_tests=False)
    llm = FakeLLM([proposal(ADD_SUB)])
    app = make_app(llm, tmp_path)
    result = app.invoke({"messages": [HumanMessage("thêm sub")], "forced": "code"}, cfg())
    assert "__interrupt__" not in result and "def sub" in (project / "src" / "calc.py").read_text()
    assert "Kiểm tra:" not in result["messages"][-1].content


def test_auto_mode_still_enforces_security(project, tmp_path):
    write_settings(tmp_path, approval_mode="auto", max_repair_attempts=0)
    (project / ".env").write_text("KEY=1")
    llm = FakeLLM([proposal(modify("KEY=1", "KEY=2", path=".env"))])
    reply, _ = run(make_app(llm, tmp_path), "đổi key", [])
    assert (project / ".env").read_text() == "KEY=1" and "nhạy cảm" in reply


def test_traversal_from_gemini_is_blocked(project, tmp_path):
    write_settings(tmp_path, max_repair_attempts=0)
    llm = FakeLLM([proposal({"path": "../evil.py", "action": "create", "content": "x = 1"})])
    reply, _ = run(make_app(llm, tmp_path), "tạo file", [])
    assert not (project.parent / "evil.py").exists() and "'..'" in reply


def test_secrets_never_sent_to_gemini(project, tmp_path):
    (project / ".env").write_text("GOOGLE_API_KEY=TOPSECRET")
    (project / "src" / "settings.py").write_text("KEY_NAME = 'GOOGLE_API_KEY'\n")
    llm = FakeLLM([proposal(ADD_SUB)])
    run(make_app(llm, tmp_path), "đọc .env rồi thêm sub vào src/calc.py", ["n"])
    assert llm.prompts and all("TOPSECRET" not in p for p in llm.prompts)


def test_tool_call_limit_stops_before_calling_gemini(project, tmp_path):
    write_settings(tmp_path, max_tool_calls=1)
    llm = FakeLLM([proposal(ADD_SUB)])
    reply, _ = run(make_app(llm, tmp_path), "thêm sub vào src/calc.py", [])
    assert llm.calls == 0 and "max_tool_calls" in reply
    assert (project / "src" / "calc.py").read_text() == ADD_ONLY


def test_empty_proposal_reports_reason(project, tmp_path):
    llm = FakeLLM([{"analysis": "Thiếu thông tin về model hiện tại", "edits": []}])
    reply, _ = run(make_app(llm, tmp_path), "làm gì đó", [])
    assert "Thiếu thông tin" in reply and "không hoàn tất" in reply


def test_project_outside_workspace_is_refused(project, tmp_path, monkeypatch):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.setenv("HUB_WORKSPACE_ROOTS", str(elsewhere))
    llm = FakeLLM([proposal(ADD_SUB)])
    reply, _ = run(make_app(llm, tmp_path), f"thêm sub trong {project}", [])
    assert llm.calls == 0 and "HUB_WORKSPACE_ROOTS" in reply


def test_two_requests_same_thread_do_not_share_run_state(project, tmp_path):
    llm = FakeLLM([proposal(ADD_SUB), proposal({"path": "src/two.py", "action": "create", "content": "T = 2\n"})])
    app = make_app(llm, tmp_path)
    first, _ = run(app, "thêm sub", ["y"])
    second, _ = run(app, "tạo src/two.py", ["y"])
    assert "Đã sửa: src/calc.py" in first
    assert "Đã tạo: src/two.py" in second and "src/calc.py" not in second.split("Kiểm tra")[0]


def test_config_command_through_graph(project, tmp_path):
    llm = FakeLLM([proposal(ADD_SUB)])
    app = make_app(llm, tmp_path)
    reply, _ = run(app, "config max_repair_attempts 1", [])
    assert "max_repair_attempts = 1" in reply and llm.calls == 0
    assert cs.load_settings().max_repair_attempts == 1


def test_gemini_failure_is_reported_not_raised(project, tmp_path):
    class Boom:
        def with_structured_output(self, schema):
            class _R:
                def invoke(self, prompt):
                    raise RuntimeError("Tất cả model trong MODEL_POOL đều không khả dụng.")
            return _R()
    app = make_app(FakeLLM([]), tmp_path)
    app = build_hub([AgentSpec("code", "d", build_code_graph(llm_factory=lambda key: Boom(), backup_root=tmp_path / "bk"))])
    reply, _ = run(app, "thêm sub", [])
    assert "Gemini không khả dụng" in reply and (project / "src" / "calc.py").read_text() == ADD_ONLY


def test_checks_skip_tools_the_project_does_not_use(tmp_path):
    root = tmp_path / "bare"
    root.mkdir()
    (root / "a.py").write_text("x = 1\n")
    cmds = ct.plan_checks(root, ["a.py"])
    assert len(cmds) == 1 and "compileall" in cmds[0]                           # no tests dir / pyproject -> no pytest, no ruff
    assert ct.plan_checks(root, ["README.md"]) == []


# ----------------------------------------------------------------------------- robustness: anchors and append
def test_fuzzy_anchor_ignores_indentation_and_trailing_whitespace(project):
    (project / "src" / "m.py").write_text("class A:\n    def run(self):\n        return 1\n\n\ndef build():\n    return A()\n")
    # Gemini lost the indentation and added trailing spaces
    edit = modify("def run(self):  \nreturn 1", "def run(self):\n    return 2", path="src/m.py")
    edit["new_str"] = "def run(self):\n    return 2"
    changes, errors, warnings = ct.build_changes(project, [edit])
    assert not errors and any("khoảng trắng" in w for w in warnings)
    assert "    def run(self):\n        return 2\n" in changes[0]["new_text"]
    assert changes[0]["new_text"].endswith("def build():\n    return A()\n")


def test_fuzzy_anchor_refuses_ambiguity(project):
    (project / "src" / "d.py").write_text("def f():\n    x = 1\n\n\ndef g():\n    x = 1\n")
    changes, errors, _ = ct.build_changes(project, [modify("  x = 1  ", "x = 2", path="src/d.py")])
    assert not changes and errors


def test_mismatch_error_quotes_real_lines_for_the_retry(project):
    src = '"""doc"""\n\n\ndef build_model(cfg: dict) -> nn.Module:\n    """Construct a model."""\n    return None\n'
    (project / "src" / "models.py").write_text(src)
    wrong = modify('def build_model(cfg) -> nn.Module:\n    """Build a model from config."""', "x", path="src/models.py")
    _, errors, _ = ct.build_changes(project, [wrong])
    assert "THỰC TẾ" in errors[0] and "def build_model(cfg: dict) -> nn.Module:" in errors[0]


def test_append_adds_class_with_spacing_and_keeps_rest(project):
    changes, errors, _ = ct.build_changes(project, [
        {"path": "src/calc.py", "action": "append", "content": "class Mul:\n    pass\n"}])
    assert not errors
    assert changes[0]["new_text"] == ADD_ONLY + "\n\nclass Mul:\n    pass\n"
    assert changes[0]["removed"] == 0


@pytest.mark.parametrize("edit,fragment", [
    ({"path": "src/missing.py", "action": "append", "content": "x = 1"}, "chưa tồn tại"),
    ({"path": "src/calc.py", "action": "append", "content": "  "}, "rỗng"),
])
def test_append_rejections(project, edit, fragment):
    _, errors, _ = ct.build_changes(project, [edit])
    assert any(fragment in e for e in errors)


def test_append_goes_before_main_guard(project):
    (project / "src" / "cli.py").write_text(
        'import os\n\n\ndef main():\n    pass\n\n\n# entry point\nif __name__ == "__main__":\n    main()\n')
    changes, errors, warnings = ct.build_changes(project, [
        {"path": "src/cli.py", "action": "append", "content": "class Late:\n    pass\n"}])
    assert not errors and any("TRƯỚC" in w for w in warnings)
    text = changes[0]["new_text"]
    assert text == ('import os\n\n\ndef main():\n    pass\n\n\nclass Late:\n    pass\n\n\n'
                    '# entry point\nif __name__ == "__main__":\n    main()\n')
    assert changes[0]["removed"] == 0


def test_append_without_main_guard_still_goes_to_end_and_ignores_strings(project):
    (project / "src" / "s.py").write_text('TEXT = "if __name__ == x"\n\n\ndef f():\n    return 1\n')
    changes, errors, _ = ct.build_changes(project, [{"path": "src/s.py", "action": "append", "content": "def g():\n    return 2\n"}])
    assert not errors and changes[0]["new_text"].endswith("def f():\n    return 1\n\n\ndef g():\n    return 2\n")


def test_append_flow_end_to_end(project, tmp_path):
    llm = FakeLLM([proposal({"path": "src/calc.py", "action": "append", "content": "def mul(a, b):\n    return a * b\n"})])
    reply, _ = run(make_app(llm, tmp_path), "thêm hàm mul vào src/calc.py", ["y"])
    assert (project / "src" / "calc.py").read_text() == ADD_ONLY + "\n\ndef mul(a, b):\n    return a * b\n"
    assert "hoàn tất" in reply


def test_failure_report_shows_hint_and_real_reason(project, tmp_path):
    write_settings(tmp_path, max_repair_attempts=0)
    llm = FakeLLM([proposal(modify("def nope():", "x"))])
    reply, _ = run(make_app(llm, tmp_path), "sửa src/calc.py", [])
    assert "không khớp nguyên văn" in reply and "max_context_chars" in reply


def test_hint_only_for_anchor_mismatch(project, tmp_path):
    write_settings(tmp_path, max_repair_attempts=0)
    llm = FakeLLM([proposal({"path": "src/calc.py", "action": "create", "content": "x = 1"})])   # file exists -> other error
    reply, _ = run(make_app(llm, tmp_path), "sửa src/calc.py", [])
    assert "đã tồn tại" in reply and "max_context_chars" not in reply


# ----------------------------------------------------------------------------- agentic behaviour
OTHER_PY = "def helper():\n    return 'other-file-body'\n"


def test_explore_reads_requested_files_but_never_secrets(project, tmp_path):
    (project / "src" / "other.py").write_text(OTHER_PY)
    (project / ".env").write_text("GOOGLE_API_KEY=TOPSECRET")
    llm = FakeLLM([proposal(ADD_SUB)], explores=[{"read": ["src/other.py", ".env"], "reason": "xem helper"}, {}])
    run(make_app(llm, tmp_path), "thêm hàm sub vào src/calc.py", ["n"])
    assert llm.explore_calls == 2 and "other-file-body" in llm.proposal_prompts[0]
    assert all("TOPSECRET" not in p for p in llm.prompts)


def test_repo_map_and_project_rules_reach_gemini(project, tmp_path):
    (project / "src" / "other.py").write_text(OTHER_PY)
    (project / "CONTRIBUTING.md").write_text("Không dùng print(), dùng logging.\n", encoding="utf-8")
    llm = FakeLLM([proposal(ADD_SUB)])
    run(make_app(llm, tmp_path), "thêm hàm sub vào src/calc.py", ["n"])
    prompt = llm.proposal_prompts[0]
    assert "<repo_map>" in prompt and "def helper" in prompt and "Không dùng print()" in prompt


def test_explore_can_be_switched_off(project, tmp_path):
    write_settings(tmp_path, explore_rounds=0, max_rounds=1)
    llm = FakeLLM([proposal(ADD_SUB)])
    run(make_app(llm, tmp_path), "thêm hàm sub vào src/calc.py", ["y"])
    assert llm.explore_calls == 0 and llm.review_calls == 0 and llm.calls == 1


def test_review_continue_runs_a_second_round_with_its_own_approval(project, tmp_path):
    (project / "src" / "registry.py").write_text("MODELS = {}\n")
    second = proposal(modify("MODELS = {}", "MODELS = {'sub': 1}", path="src/registry.py"))
    llm = FakeLLM([proposal(ADD_SUB), second],
                  reviews=[{"status": "continue", "summary": "đã thêm sub",
                            "next_task": "đăng ký sub vào MODELS trong src/registry.py", "read": ["src/registry.py"]}, {}])
    reply, said = run(make_app(llm, tmp_path), "thêm sub và đăng ký", ["y", "y"])
    assert llm.calls == 2 and len(said) == 2 and "vòng 2" in said[1]
    assert "đăng ký sub vào MODELS" in said[1]
    assert (project / "src" / "registry.py").read_text() == "MODELS = {'sub': 1}\n"
    assert "Các bước đã làm" in reply and "Vòng 1" in reply and "Vòng 2" in reply
    assert "đã thêm sub" in llm.proposal_prompts[1]                          # round 2 knows what round 1 did
    assert "def sub" in (project / "src" / "calc.py").read_text()            # round 1 kept


def test_review_ask_turns_the_users_answer_into_the_next_round(project, tmp_path):
    (project / "src" / "loader.py").write_text("MODE = '2d'\n")
    llm = FakeLLM([proposal(ADD_SUB), proposal(modify("MODE = '2d'", "MODE = '1d'", path="src/loader.py"))],
                  reviews=[{"status": "ask", "summary": "đã thêm sub",
                            "question": "data_loader đang trả ảnh 2D, thêm nhánh 1D chứ?"}, {}])
    reply, said = run(make_app(llm, tmp_path), "thêm sub", ["y", "có, thêm nhánh 1D", "y"])
    assert any("CODE AGENT hỏi bạn" in s and "nhánh 1D chứ" in s for s in said)
    assert "có, thêm nhánh 1D" in llm.proposal_prompts[1]
    assert (project / "src" / "loader.py").read_text() == "MODE = '1d'\n" and "hoàn tất" in reply


def test_user_can_stop_at_the_question_and_keep_what_was_done(project, tmp_path):
    llm = FakeLLM([proposal(ADD_SUB)], reviews=[{"status": "ask", "question": "làm tiếp nhé?"}])
    reply, _ = run(make_app(llm, tmp_path), "thêm sub", ["y", "dừng"])
    assert "def sub" in (project / "src" / "calc.py").read_text()
    assert llm.calls == 1 and "dừng" in reply and "Đã sửa: src/calc.py" in reply


def test_rounds_are_bounded_even_if_review_always_continues(project, tmp_path):
    write_settings(tmp_path, approval_mode="auto", auto_run_tests=False)
    files = [proposal({"path": f"src/r{i}.py", "action": "create", "content": f"R = {i}\n"}) for i in range(1, 6)]
    llm = FakeLLM(files, reviews=[{"status": "continue", "summary": "xong", "next_task": "thêm file tiếp"}])
    app = make_app(llm, tmp_path)
    result = app.invoke({"messages": [HumanMessage("tạo r1")], "forced": "code"}, cfg())   # must not hit the recursion limit
    assert "__interrupt__" not in result and llm.calls == 3                                   # max_rounds default = 3
    assert llm.review_calls == 2                                                              # no review after the last round


def test_review_failure_is_not_fatal(project, tmp_path):
    class Flaky(FakeLLM):
        def with_structured_output(self, schema):
            if schema.__name__ == "Review":
                raise RuntimeError("quota")
            return super().with_structured_output(schema)
    llm = Flaky([proposal(ADD_SUB)])
    reply, _ = run(make_app(llm, tmp_path), "thêm sub", ["y"])
    assert "hoàn tất" in reply and "def sub" in (project / "src" / "calc.py").read_text()


def test_agent_written_test_runs_even_when_project_has_no_tests_dir(tmp_path, monkeypatch):
    root = tmp_path / "bare"
    (root / "pkg").mkdir(parents=True)
    (root / "pyproject.toml").write_text('[project]\nname = "x"\n')
    (root / "pkg" / "m.py").write_text("def f():\n    return 1\n")
    monkeypatch.chdir(root)
    monkeypatch.setenv("HUB_DATA_DIR", str(tmp_path / "data"))
    new_test = {"path": "tests/test_f.py", "action": "create",
                "content": "import sys\nsys.path.insert(0, '.')\nfrom pkg.m import f\n\n\ndef test_f():\n    assert f() == 1\n"}
    llm = FakeLLM([proposal(new_test)])
    reply, said = run(make_app(llm, tmp_path), "thêm test cho pkg/m.py", ["y"])
    assert "python -m pytest -q -x tests/test_f.py" in said[0] and "[OK] python -m pytest" in reply


def test_missing_third_party_module_is_an_environment_problem_not_a_patch_bug(project, tmp_path):
    bad_env = {"path": "tests/test_torchy.py", "action": "create", "content": "import torch_not_installed_xyz\n\n\ndef test_x():\n    assert True\n"}
    llm = FakeLLM([proposal(bad_env)])
    reply, _ = run(make_app(llm, tmp_path), "thêm test", ["y"])
    assert llm.calls == 1                                                    # no wasted repair round
    assert "python_path" in reply and "torch_not_installed_xyz" in reply and "[bỏ qua]" in reply


def test_missing_project_module_is_still_a_real_failure(project, tmp_path):
    write_settings(tmp_path, max_repair_attempts=0)
    bad = {"path": "tests/test_mine.py", "action": "create", "content": "from src.nope import x\n\n\ndef test_x():\n    assert x\n"}
    reply, _ = run(make_app(FakeLLM([proposal(bad)]), tmp_path), "thêm test", ["y"])
    assert "CHƯA đạt" in reply


def test_duplicate_imports_in_appended_code_are_dropped(project):
    (project / "src" / "nn.py").write_text("import torch\nimport torch.nn as nn\n\n\nclass A(nn.Module):\n    pass\n")
    changes, errors, warnings = ct.build_changes(project, [{
        "path": "src/nn.py", "action": "append",
        "content": "import torch\nimport torch.nn as nn\nimport math\n\nclass B(nn.Module):\n    pass\n"}])
    assert not errors
    text = changes[0]["new_text"]
    assert text.count("import torch\n") == 1 and text.count("import torch.nn as nn") == 1 and "import math" in text
    assert any("bỏ 2 dòng import" in w for w in warnings) and any("giữa file" in w for w in warnings)


def test_allowlist_accepts_specific_test_files_only():
    py = sys.executable
    base = [py, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider"]
    assert is_allowed_command([*base, "tests/test_a.py"], py)
    for bad in ([*base, "../evil.py"], [*base, "--rootdir=/"], [*base, "/abs/test_a.py"], [*base, "tests/a.txt"]):
        assert not is_allowed_command(bad, py)


def test_python_path_setting(tmp_path):
    path = tmp_path / "c.json"
    assert "không tìm thấy" in cs.handle_config_command("config python_path " + str(tmp_path / "nope.exe"), path)
    assert cs.handle_config_command(f"config python_path {sys.executable}", path).startswith("Đã đặt python_path")
    assert cs.load_settings(path).python_path == sys.executable
    assert ct.project_python(tmp_path, sys.executable) == sys.executable
    assert cs.load_settings(path).max_rounds == 3 and cs.handle_config_command("config max_rounds 9", path).startswith("Giá trị")


def test_project_rules_survive_a_legacy_windows_encoding(project):
    (project / "CONTRIBUTING.md").write_bytes("Không dùng print(), dùng logging.\n".encode("cp1252"))  # not valid UTF-8
    rules = ct.project_rules(project)
    assert "<project_rules" in rules and "print()" in rules and "logging" in rules
