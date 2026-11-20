from pathlib import Path
import pytest

from hub.memory import MemoryStore
from hub.skills import SkillRegistry
from hub.tools.registry import ToolRegistry, ToolSpec
from hub.tools.builtin import search_project


def test_sqlite_history_survives_new_store(tmp_path):
    db = tmp_path / "hub.sqlite3"
    first = MemoryStore(db)
    first.save_turn("thread-a", "hello", "world", "qa")
    second = MemoryStore(db)
    assert second.load_history("thread-a") == [
        {"role": "user", "content": "hello", "agent": None},
        {"role": "assistant", "content": "world", "agent": "qa"},
    ]


def test_user_memory_is_explicit(tmp_path, monkeypatch):
    monkeypatch.setenv("HUB_DATA_DIR", str(tmp_path))
    store = MemoryStore()
    assert store.read_user_memory() == ""
    store.append_user_memory("Prefer concise answers")
    assert "Prefer concise answers" in store.read_user_memory()


def test_active_project_is_remembered_per_thread(tmp_path):
    db = tmp_path / "hub.sqlite3"
    store = MemoryStore(db)
    store.set_active_project("thread-a", tmp_path)
    assert MemoryStore(db).get_active_project("thread-a") == str(tmp_path)
    assert MemoryStore(db).get_active_project("thread-b") is None
    store.set_active_project("thread-a", None)
    assert store.get_active_project("thread-a") is None


def test_project_memory_lands_where_qa_reads_it(tmp_path):
    path = MemoryStore.append_project_memory(tmp_path, "QA dùng mini-map")
    assert path == tmp_path / ".hub" / "project_memory.md"
    assert MemoryStore.read_project_memory(tmp_path).strip() == "- QA dùng mini-map"
    MemoryStore.append_project_memory(tmp_path, "chạy pytest -q trong env agent-hub")
    text = MemoryStore.read_project_memory(tmp_path)
    assert "mini-map" in text and "pytest -q" in text


def test_skill_registry_loads_and_selects(tmp_path):
    skill = tmp_path / "skills" / "python-debug" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("---\ndescription: Debug Python tracebacks\n---\nCheck the traceback and reproduce the error.", encoding="utf-8")
    registry = SkillRegistry([tmp_path / "skills"])
    assert registry.load_all()[0].name == "python-debug"
    assert registry.select("help debug this Python traceback")[0].name == "python-debug"


def test_tool_registry_requires_confirmation():
    registry = ToolRegistry()
    registry.register(ToolSpec("danger", "test", lambda: "done", requires_confirmation=True))
    with pytest.raises(PermissionError):
        registry.execute("danger")
    assert registry.execute("danger", confirmed=True) == "done"


def test_search_project_returns_matching_source_lines(tmp_path):
    (tmp_path / "model.py").write_text("class ResNet:\n    pass\n", encoding="utf-8")
    result = search_project(str(tmp_path), "find ResNet class")
    assert "model.py:L1" in result and "class ResNet" in result


def test_search_project_skips_venv_release_and_internal_bundle(tmp_path):
    (tmp_path / "main.py").write_text("TARGET_SYMBOL = 1\n", encoding="utf-8")
    for rel in (
        ".venv-release/Lib/site-packages/noise.py",
        "Transcripts_automation/_internal/pkg/noise.py",
    ):
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("TARGET_SYMBOL = 999\n", encoding="utf-8")
    result = search_project(str(tmp_path), "TARGET_SYMBOL", max_results=20)
    assert "main.py:L1" in result
    assert ".venv-release" not in result and "_internal" not in result
