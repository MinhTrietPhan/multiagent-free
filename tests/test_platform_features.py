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
